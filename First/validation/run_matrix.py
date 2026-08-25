from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


VALIDATION_DIR = Path(__file__).resolve().parent
FIRST_DIR = VALIDATION_DIR.parent
DEFAULT_CONFIG = VALIDATION_DIR / "matrix_smoke.json"
DEFAULT_RESULTS_DIR = VALIDATION_DIR / "results_matrix"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

if str(FIRST_DIR) not in sys.path:
    sys.path.insert(0, str(FIRST_DIR))

import benchmark_generator_v2_2 as generator  # noqa: E402
import run_benchmark_v2_2_fixed as evaluator_patch  # noqa: E402


benchmark = evaluator_patch.base


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    family: str = "unknown"
    size: str = "unknown"
    tier: str = "standard"


@dataclass(frozen=True, slots=True)
class MatrixConfig:
    name: str
    models: tuple[ModelSpec, ...]
    dataset_seeds: tuple[int, ...]
    inference_seeds: tuple[int, ...]
    repeats: int
    context_windows: tuple[int, ...]
    conditions: tuple[str, ...]
    history_min: int
    history_max: int
    limit: int
    temperature: float
    think: bool
    num_predict: int
    timeout_seconds: int
    retries: int
    keep_alive: str

    @classmethod
    def from_dict(cls, data: dict) -> "MatrixConfig":
        models = []
        for item in data["models"]:
            if isinstance(item, str):
                models.append(ModelSpec(name=item))
            else:
                models.append(
                    ModelSpec(
                        name=item["name"],
                        family=item.get("family", "unknown"),
                        size=item.get("size", "unknown"),
                        tier=item.get("tier", "standard"),
                    )
                )
        config = cls(
            name=data.get("name", "validation-matrix"),
            models=tuple(models),
            dataset_seeds=tuple(int(value) for value in data["dataset_seeds"]),
            inference_seeds=tuple(
                int(value) for value in data["inference_seeds"]
            ),
            repeats=int(data["repeats"]),
            context_windows=tuple(
                int(value) for value in data["context_windows"]
            ),
            conditions=tuple(data["conditions"]),
            history_min=int(data["history_min"]),
            history_max=int(data["history_max"]),
            limit=int(data.get("limit", 0)),
            temperature=float(data.get("temperature", 0.0)),
            think=bool(data.get("think", False)),
            num_predict=int(data.get("num_predict", 256)),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            retries=int(data.get("retries", 1)),
            keep_alive=str(data.get("keep_alive", "15m")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.models:
            raise ValueError("models cannot be empty")
        if not self.dataset_seeds or not self.inference_seeds:
            raise ValueError("dataset_seeds and inference_seeds cannot be empty")
        if self.repeats < 1:
            raise ValueError("repeats must be at least 1")
        if any(window < 1024 for window in self.context_windows):
            raise ValueError("context windows must be at least 1024")
        unknown = set(self.conditions) - set(benchmark.ALL_CONDITIONS)
        if unknown:
            raise ValueError(f"unknown conditions: {sorted(unknown)}")
        if self.history_min < 10 or self.history_max < self.history_min:
            raise ValueError("invalid history range")
        if self.limit < 0 or self.limit > 100:
            raise ValueError("limit must be between 0 and 100")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.num_predict < 1:
            raise ValueError("num_predict must be positive")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "models": [
                {
                    "name": model.name,
                    "family": model.family,
                    "size": model.size,
                    "tier": model.tier,
                }
                for model in self.models
            ],
            "dataset_seeds": list(self.dataset_seeds),
            "inference_seeds": list(self.inference_seeds),
            "repeats": self.repeats,
            "context_windows": list(self.context_windows),
            "conditions": list(self.conditions),
            "history_min": self.history_min,
            "history_max": self.history_max,
            "limit": self.limit,
            "temperature": self.temperature,
            "think": self.think,
            "num_predict": self.num_predict,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "keep_alive": self.keep_alive,
        }


@dataclass(frozen=True, slots=True)
class MatrixJob:
    model: ModelSpec
    dataset_seed: int
    inference_seed: int
    repeat: int
    num_ctx: int
    case: dict
    condition: str
    messages: list[dict]
    history_min: int
    history_max: int
    temperature: float
    think: bool
    num_predict: int

    @property
    def run_id(self) -> str:
        source = json.dumps(
            {
                "model": self.model.name,
                "dataset_seed": self.dataset_seed,
                "inference_seed": self.inference_seed,
                "repeat": self.repeat,
                "num_ctx": self.num_ctx,
                "case_id": self.case["id"],
                "condition": self.condition,
                "history_min": self.history_min,
                "history_max": self.history_max,
                "temperature": self.temperature,
                "think": self.think,
                "num_predict": self.num_predict,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        return f"run-{digest}"


FIELDS = [
    "run_id",
    "status",
    "error",
    "started_at",
    "model",
    "model_digest",
    "model_family",
    "model_size",
    "model_tier",
    "dataset_seed",
    "inference_seed",
    "repeat",
    "num_ctx",
    "history_min",
    "history_max",
    "temperature",
    "think",
    "num_predict",
    "case_id",
    "family_id",
    "question_mode",
    "active_branch_position",
    "condition",
    "question",
    "answer",
    "prompt_chars",
    "prompt_tokens",
    "response_tokens",
    "latency_ms",
    "load_ms",
    "prompt_eval_ms",
    "eval_ms",
    "tokens_per_second",
    "context_saturation",
    "correct",
    "polluted",
    "expected_hits",
    "forbidden_hits",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_int_list(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def parse_str_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def load_config(path: Path) -> MatrixConfig:
    return MatrixConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def with_overrides(config: MatrixConfig, args: argparse.Namespace) -> MatrixConfig:
    data = config.to_dict()
    if args.models:
        known = {model["name"]: model for model in data["models"]}
        data["models"] = [
            known.get(name, {"name": name}) for name in parse_str_list(args.models)
        ]
    if args.dataset_seeds:
        data["dataset_seeds"] = list(parse_int_list(args.dataset_seeds))
    if args.inference_seeds:
        data["inference_seeds"] = list(parse_int_list(args.inference_seeds))
    if args.context_windows:
        data["context_windows"] = list(parse_int_list(args.context_windows))
    if args.conditions:
        data["conditions"] = list(parse_str_list(args.conditions))
    for field in (
        "repeats",
        "limit",
        "history_min",
        "history_max",
        "num_predict",
        "timeout_seconds",
        "retries",
    ):
        value = getattr(args, field)
        if value is not None:
            data[field] = value
    if args.temperature is not None:
        data["temperature"] = args.temperature
    return MatrixConfig.from_dict(data)


def generate_benchmark(config: MatrixConfig, dataset_seed: int) -> dict:
    data = generator.generate(
        seed=dataset_seed,
        history_min=config.history_min,
        history_max=config.history_max,
    )
    generator.validate(data)
    return data


def stratified_cases(cases: list[dict], limit: int) -> list[dict]:
    if not limit or limit >= len(cases):
        return cases
    if limit == 1:
        return [cases[0]]
    indexes = {
        round(index * (len(cases) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [cases[index] for index in sorted(indexes)]


def expected_job_count(config: MatrixConfig) -> int:
    case_count = config.limit or 100
    return (
        len(config.models)
        * len(config.dataset_seeds)
        * len(config.inference_seeds)
        * config.repeats
        * len(config.context_windows)
        * case_count
        * len(config.conditions)
    )


def iter_jobs(config: MatrixConfig) -> Iterator[MatrixJob]:
    datasets = {
        seed: generate_benchmark(config, seed) for seed in config.dataset_seeds
    }
    for model in config.models:
        for dataset_seed, data in datasets.items():
            cases = stratified_cases(data["questions"], config.limit)
            for inference_seed in config.inference_seeds:
                for repeat in range(1, config.repeats + 1):
                    for num_ctx in config.context_windows:
                        for case in cases:
                            for condition in config.conditions:
                                messages = benchmark.compile_messages(
                                    data,
                                    case,
                                    condition,
                                )
                                yield MatrixJob(
                                    model=model,
                                    dataset_seed=dataset_seed,
                                    inference_seed=inference_seed,
                                    repeat=repeat,
                                    num_ctx=num_ctx,
                                    case=case,
                                    condition=condition,
                                    messages=messages,
                                    history_min=config.history_min,
                                    history_max=config.history_max,
                                    temperature=config.temperature,
                                    think=config.think,
                                    num_predict=config.num_predict,
                                )


def request_json(url: str, payload: dict | None, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error


def installed_model_inventory() -> list[dict]:
    payload = request_json(f"{OLLAMA_BASE_URL}/api/tags", None, 15)
    return [
        {
            "name": item["name"],
            "digest": item.get("digest"),
            "size": item.get("size"),
            "modified_at": item.get("modified_at"),
            "details": item.get("details"),
        }
        for item in payload.get("models", [])
        if isinstance(item, dict) and item.get("name")
    ]


def call_ollama(job: MatrixJob, config: MatrixConfig) -> dict:
    payload = {
        "model": job.model.name,
        "messages": job.messages,
        "stream": False,
        "think": config.think,
        "keep_alive": config.keep_alive,
        "options": {
            "temperature": config.temperature,
            "seed": job.inference_seed,
            "num_ctx": job.num_ctx,
            "num_predict": config.num_predict,
        },
    }
    started = time.perf_counter()
    result = request_json(
        f"{OLLAMA_BASE_URL}/api/chat",
        payload,
        config.timeout_seconds,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    eval_count = result.get("eval_count")
    eval_duration = result.get("eval_duration")
    tokens_per_second = None
    if eval_count and eval_duration:
        tokens_per_second = eval_count / (eval_duration / 1_000_000_000)
    return {
        "answer": result.get("message", {}).get("content", "").strip(),
        "prompt_tokens": result.get("prompt_eval_count"),
        "response_tokens": eval_count,
        "latency_ms": round(latency_ms, 2),
        "load_ms": round((result.get("load_duration") or 0) / 1_000_000, 2),
        "prompt_eval_ms": round(
            (result.get("prompt_eval_duration") or 0) / 1_000_000,
            2,
        ),
        "eval_ms": round((eval_duration or 0) / 1_000_000, 2),
        "tokens_per_second": (
            round(tokens_per_second, 2) if tokens_per_second is not None else None
        ),
    }


def run_job(
    job: MatrixJob,
    config: MatrixConfig,
    model_digest: str | None = None,
) -> dict:
    prompt_chars = benchmark.estimate_chars(job.messages)
    base = {
        "run_id": job.run_id,
        "started_at": utc_now(),
        "model": job.model.name,
        "model_digest": model_digest,
        "model_family": job.model.family,
        "model_size": job.model.size,
        "model_tier": job.model.tier,
        "dataset_seed": job.dataset_seed,
        "inference_seed": job.inference_seed,
        "repeat": job.repeat,
        "num_ctx": job.num_ctx,
        "history_min": job.history_min,
        "history_max": job.history_max,
        "temperature": config.temperature,
        "think": config.think,
        "num_predict": config.num_predict,
        "case_id": job.case["id"],
        "family_id": job.case["family_id"],
        "question_mode": job.case["question_mode"],
        "active_branch_position": job.case["active_branch_position"],
        "condition": job.condition,
        "question": job.case["question"],
        "prompt_chars": prompt_chars,
    }
    last_error = None
    for attempt in range(config.retries + 1):
        try:
            result = call_ollama(job, config)
            evaluation = benchmark.evaluate(
                result["answer"],
                job.case["expected_contains"],
                job.case["forbidden_contains"],
            )
            prompt_tokens = result["prompt_tokens"]
            return {
                **base,
                **result,
                "status": "ok",
                "error": "",
                "context_saturation": (
                    round(prompt_tokens / job.num_ctx, 4)
                    if prompt_tokens is not None
                    else None
                ),
                "correct": evaluation["correct"],
                "polluted": evaluation["polluted"],
                "expected_hits": " | ".join(evaluation["expected_hits"]),
                "forbidden_hits": " | ".join(evaluation["forbidden_hits"]),
            }
        except Exception as error:
            last_error = error
            if attempt < config.retries:
                time.sleep(min(2 ** attempt, 5))
    return {
        **base,
        "status": "error",
        "error": str(last_error),
        "answer": "",
        "prompt_tokens": None,
        "response_tokens": None,
        "latency_ms": None,
        "load_ms": None,
        "prompt_eval_ms": None,
        "eval_ms": None,
        "tokens_per_second": None,
        "context_saturation": None,
        "correct": False,
        "polluted": False,
        "expected_hits": "",
        "forbidden_hits": "",
    }


def append_csv(path: Path, row: dict) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    fieldnames = FIELDS
    if not write_header:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            existing_fieldnames = csv.DictReader(file).fieldnames
        if existing_fieldnames:
            fieldnames = existing_fieldnames
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def ensure_csv_schema(path: Path, model_digests: dict[str, str | None]) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        existing_fieldnames = reader.fieldnames or []
        rows = list(reader)

    missing_fields = [field for field in FIELDS if field not in existing_fieldnames]
    if not missing_fields:
        return False

    if "model_digest" in missing_fields:
        unresolved_models = sorted(
            {
                row.get("model", "")
                for row in rows
                if not model_digests.get(row.get("model", ""))
            }
        )
        if unresolved_models:
            raise RuntimeError(
                "Cannot migrate CSV schema without model digests for: "
                + ", ".join(unresolved_models)
            )

    fieldnames = list(dict.fromkeys([*FIELDS, *existing_fieldnames]))
    backup_path = path.with_name(path.name + ".pre_schema_migration")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            for row in rows:
                if not row.get("model_digest"):
                    row["model_digest"] = model_digests.get(row.get("model", ""))
                writer.writerow(row)
        os.replace(temporary_name, path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise
    return True


def parse_optional_number(value: str, converter):
    return converter(value) if value not in (None, "") else None


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for raw in csv.DictReader(file):
            row = dict(raw)
            for field in (
                "dataset_seed",
                "inference_seed",
                "repeat",
                "num_ctx",
                "history_min",
                "history_max",
                "num_predict",
                "active_branch_position",
                "prompt_chars",
                "prompt_tokens",
                "response_tokens",
            ):
                row[field] = parse_optional_number(row.get(field), int)
            for field in (
                "temperature",
                "latency_ms",
                "load_ms",
                "prompt_eval_ms",
                "eval_ms",
                "tokens_per_second",
                "context_saturation",
            ):
                row[field] = parse_optional_number(row.get(field), float)
            row["think"] = str(row.get("think", "")).lower() == "true"
            row["correct"] = str(row.get("correct", "")).lower() == "true"
            row["polluted"] = str(row.get("polluted", "")).lower() == "true"
            rows.append(row)
    return rows


def latest_attempt_rows(rows: list[dict]) -> list[dict]:
    latest = {}
    for row in rows:
        latest[row["run_id"]] = row
    return list(latest.values())


def safe_mean(values: Iterable[float | int | None]) -> float | None:
    filtered = [float(value) for value in values if value is not None]
    return round(statistics.mean(filtered), 4) if filtered else None


def wilson_interval(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return [round(max(0, centre - margin), 4), round(min(1, centre + margin), 4)]


def summarize_group(rows: list[dict]) -> dict:
    successes = sum(1 for row in rows if row["correct"])
    polluted = sum(1 for row in rows if row["polluted"])
    return {
        "runs": len(rows),
        "accuracy": round(successes / len(rows), 4),
        "accuracy_ci95": wilson_interval(successes, len(rows)),
        "pollution_rate": round(polluted / len(rows), 4),
        "pollution_ci95": wilson_interval(polluted, len(rows)),
        "avg_prompt_tokens": safe_mean(row["prompt_tokens"] for row in rows),
        "avg_latency_ms": safe_mean(row["latency_ms"] for row in rows),
        "avg_tokens_per_second": safe_mean(
            row["tokens_per_second"] for row in rows
        ),
        "avg_context_saturation": safe_mean(
            row["context_saturation"] for row in rows
        ),
    }


def summarize_repeatability(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    repeat_fields = (
        "model",
        "dataset_seed",
        "inference_seed",
        "num_ctx",
        "case_id",
        "condition",
    )
    for row in rows:
        key = tuple(row[field] for field in repeat_fields)
        groups[key].append(row)
    repeated = [group for group in groups.values() if len(group) > 1]
    return {
        "groups": len(repeated),
        "accuracy_agreement_rate": safe_mean(
            len({bool(row["correct"]) for row in group}) == 1
            for group in repeated
        ),
        "pollution_agreement_rate": safe_mean(
            len({bool(row["polluted"]) for row in group}) == 1
            for group in repeated
        ),
        "exact_answer_agreement_rate": safe_mean(
            len({row.get("answer", "") for row in group}) == 1
            for group in repeated
        ),
        "avg_unique_answers": safe_mean(
            len({row.get("answer", "") for row in group})
            for group in repeated
        ),
    }


def build_summary(rows: list[dict], config: MatrixConfig) -> dict:
    current_rows = latest_attempt_rows(rows)
    successful = [row for row in current_rows if row["status"] == "ok"]
    expected_jobs = expected_job_count(config)
    observed_jobs = len(current_rows)
    grouped = defaultdict(list)
    for row in successful:
        grouped[(row["model"], row["num_ctx"], row["condition"])].append(row)

    by_model_window = {}
    for (model, num_ctx, condition), group_rows in sorted(grouped.items()):
        model_entry = by_model_window.setdefault(model, {})
        window_entry = model_entry.setdefault(str(num_ctx), {})
        window_entry[condition] = summarize_group(group_rows)

    paired = []
    pair_groups = defaultdict(dict)
    pair_fields = (
        "model",
        "dataset_seed",
        "inference_seed",
        "repeat",
        "num_ctx",
        "case_id",
    )
    for row in successful:
        key = tuple(row[field] for field in pair_fields)
        pair_groups[key][row["condition"]] = row
    for key, conditions in pair_groups.items():
        linear = conditions.get("linear_tagged")
        branch = conditions.get("branch")
        if not linear or not branch:
            continue
        reduction = None
        if (
            linear["prompt_tokens"] is not None
            and linear["prompt_tokens"] > 0
            and branch["prompt_tokens"] is not None
        ):
            reduction = round(
                (linear["prompt_tokens"] - branch["prompt_tokens"])
                / linear["prompt_tokens"]
                * 100,
                2,
            )
        paired.append(
            {
                **dict(zip(pair_fields, key)),
                "branch_accuracy_delta": int(branch["correct"])
                - int(linear["correct"]),
                "branch_pollution_delta": int(branch["polluted"])
                - int(linear["polluted"]),
                "prompt_token_reduction_pct": reduction,
            }
        )

    return {
        "generated_at": utc_now(),
        "config": config.to_dict(),
        "expected_jobs": expected_jobs,
        "observed_jobs": observed_jobs,
        "pending_jobs": max(expected_jobs - observed_jobs, 0),
        "completion_rate": round(observed_jobs / expected_jobs, 4)
        if expected_jobs
        else None,
        "rows": observed_jobs,
        "attempt_rows": len(rows),
        "successful_rows": len(successful),
        "error_rows": len(current_rows) - len(successful),
        "by_model_window": by_model_window,
        "repeatability": summarize_repeatability(successful),
        "paired_branch_vs_linear_tagged": {
            "pairs": len(paired),
            "avg_accuracy_delta": safe_mean(
                row["branch_accuracy_delta"] for row in paired
            ),
            "avg_pollution_delta": safe_mean(
                row["branch_pollution_delta"] for row in paired
            ),
            "avg_prompt_token_reduction_pct": safe_mean(
                row["prompt_token_reduction_pct"] for row in paired
            ),
        },
        "errors": [
            {
                "run_id": row["run_id"],
                "model": row["model"],
                "error": row["error"],
            }
            for row in current_rows
            if row["status"] != "ok"
        ],
    }


def write_summary(results_dir: Path, rows: list[dict], config: MatrixConfig) -> None:
    summary = build_summary(rows, config)
    (results_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# {config.name}",
        "",
        f"- Completed jobs: {summary['observed_jobs']}/{summary['expected_jobs']} "
        f"({summary['completion_rate']:.1%})",
        f"- Pending jobs: {summary['pending_jobs']}",
        f"- Successful runs: {summary['successful_rows']}",
        f"- Errors: {summary['error_rows']}",
        f"- Paired comparisons: {summary['paired_branch_vs_linear_tagged']['pairs']}",
        "",
        "| Model | Window | Condition | Runs | Accuracy | Pollution | Prompt tokens | Latency ms |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for model, windows in summary["by_model_window"].items():
        for window, conditions in windows.items():
            for condition, metrics in conditions.items():
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            model,
                            window,
                            condition,
                            str(metrics["runs"]),
                            f"{metrics['accuracy']:.1%}",
                            f"{metrics['pollution_rate']:.1%}",
                            str(metrics["avg_prompt_tokens"]),
                            str(metrics["avg_latency_ms"]),
                        ]
                    )
                    + " |"
                )
    lines.extend(
        [
            "",
            "## Repeatability",
            "",
            json.dumps(
                summary["repeatability"],
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## Paired Branch vs Linear Tagged",
            "",
            json.dumps(
                summary["paired_branch_vs_linear_tagged"],
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )
    (results_dir / "summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def ollama_version() -> str | None:
    try:
        result = subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return (result.stdout or result.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_info() -> list[str] | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
        return rows or None
    except (OSError, subprocess.SubprocessError):
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_fingerprint(config: MatrixConfig) -> str:
    source = json.dumps(
        config.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def print_dry_run(config: MatrixConfig) -> None:
    print(f"Matrix      : {config.name}")
    print(f"Models      : {', '.join(model.name for model in config.models)}")
    print(f"Data seeds  : {config.dataset_seeds}")
    print(f"Model seeds : {config.inference_seeds}")
    print(f"Repeats     : {config.repeats}")
    print(f"Windows     : {config.context_windows}")
    print(f"Conditions  : {config.conditions}")
    print(f"History     : {config.history_min}-{config.history_max} messages/branch")
    print(f"Cases       : {config.limit or 100} stratified")
    print(f"Total jobs  : {expected_job_count(config):,}")
    first_jobs = []
    for job in iter_jobs(config):
        first_jobs.append(job)
        if len(first_jobs) >= len(config.conditions):
            break
    print("Prompt samples:")
    for job in first_jobs:
        print(
            f"  {job.condition:<18} case={job.case['id']} "
            f"messages={len(job.messages)} chars={benchmark.estimate_chars(job.messages):,}"
        )


def main() -> None:
    benchmark.self_test_evaluator()
    parser = argparse.ArgumentParser(
        description="Run a resumable multi-model Minimal Sufficient Context matrix"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--models")
    parser.add_argument("--dataset-seeds")
    parser.add_argument("--inference-seeds")
    parser.add_argument("--context-windows")
    parser.add_argument("--conditions")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--history-min", type=int)
    parser.add_argument("--history-max", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--num-predict", type=int)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--max-jobs", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--installed-only", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = with_overrides(load_config(Path(args.config)), args)
    if args.dry_run:
        print_dry_run(config)
        return

    model_inventory = installed_model_inventory()
    available = {item["name"] for item in model_inventory}
    model_digests = {
        item["name"]: item.get("digest") for item in model_inventory
    }
    missing = [model.name for model in config.models if model.name not in available]
    if missing and not args.installed_only:
        raise SystemExit(
            "Missing Ollama models: " + ", ".join(missing) + ". Pull them first."
        )
    if args.installed_only and missing:
        data = config.to_dict()
        data["models"] = [
            model for model in data["models"] if model["name"] in available
        ]
        if not data["models"]:
            raise SystemExit("None of the configured Ollama models are installed.")
        config = MatrixConfig.from_dict(data)

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "results.csv"
    manifest_path = results_dir / "manifest.json"
    if not args.resume and (csv_path.exists() or manifest_path.exists()):
        raise SystemExit(
            f"Results already exist in {results_dir}. Use --resume or a new directory."
        )
    previous_manifest = None
    if args.resume and manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_fingerprint = previous_manifest.get("config_fingerprint")
        if previous_fingerprint is None and previous_manifest.get("config"):
            previous_config = MatrixConfig.from_dict(previous_manifest["config"])
            previous_fingerprint = config_fingerprint(previous_config)
        if previous_fingerprint != config_fingerprint(config):
            raise SystemExit(
                "Resume config does not match the existing manifest. "
                "Use a new results directory."
            )
        previous_inventory = previous_manifest.get("model_inventory")
        if previous_inventory:
            previous_digests = {
                item["name"]: item.get("digest") for item in previous_inventory
            }
            changed_models = [
                model.name
                for model in config.models
                if previous_digests.get(model.name) != model_digests.get(model.name)
            ]
            if changed_models:
                raise SystemExit(
                    "Cannot resume because model digests changed: "
                    + ", ".join(changed_models)
                )
    if args.resume and csv_path.exists() and previous_manifest is None:
        raise SystemExit("Cannot safely resume: manifest.json is missing.")
    schema_migrated = False
    if args.resume:
        schema_migrated = ensure_csv_schema(csv_path, model_digests)
    existing_rows = load_rows(csv_path) if args.resume else []
    completed = {
        row["run_id"]
        for row in latest_attempt_rows(existing_rows)
        if row["status"] == "ok"
    }
    manifest = {
        "started_at": (
            previous_manifest.get("started_at")
            if previous_manifest
            else utc_now()
        ),
        "last_started_at": utc_now(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "ollama": ollama_version(),
        "gpu": gpu_info(),
        "installed_models": sorted(available),
        "model_inventory": model_inventory,
        "result_fields": FIELDS,
        "csv_schema_migrated": schema_migrated,
        "expected_jobs": expected_job_count(config),
        "config_fingerprint": config_fingerprint(config),
        "config": config.to_dict(),
        "artifact_sha256": {
            "runner": file_sha256(Path(__file__).resolve()),
            "generator": file_sha256(FIRST_DIR / "benchmark_generator_v2_2.py"),
            "evaluator": file_sha256(FIRST_DIR / "run_benchmark_v2_2_fixed.py"),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = expected_job_count(config)
    executed = 0
    seen = 0
    for job in iter_jobs(config):
        seen += 1
        if job.run_id in completed:
            continue
        if args.max_jobs and executed >= args.max_jobs:
            break
        executed += 1
        print(
            f"[{seen}/{total}] {job.model.name} ctx={job.num_ctx} "
            f"data={job.dataset_seed} seed={job.inference_seed} "
            f"r={job.repeat} {job.case['id']} {job.condition}"
        )
        row = run_job(job, config, model_digests.get(job.model.name))
        append_csv(csv_path, row)
        existing_rows.append(row)
        if row["status"] == "ok":
            completed.add(row["run_id"])
            print(
                f"  accuracy={row['correct']} pollution={row['polluted']} "
                f"prompt={row['prompt_tokens']} latency={row['latency_ms']}ms"
            )
        else:
            print(f"  ERROR: {row['error']}")
            if args.fail_fast:
                write_summary(results_dir, existing_rows, config)
                raise SystemExit(1)
        write_summary(results_dir, existing_rows, config)

    write_summary(results_dir, existing_rows, config)
    print(f"Completed new jobs: {executed}")
    print(f"Results: {csv_path}")
    print(f"Summary: {results_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
