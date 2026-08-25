from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


STUDY_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = STUDY_DIR / "data"
DEFAULT_OUTPUT_DIR = STUDY_DIR / "analysis"
RATING_FIELDS = ("confidence", "workload", "usability", "trust")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSONL in {path}:{line_number}") from error
    return rows


def safe_mean(values: Iterable[float | int | None]) -> float | None:
    filtered = [float(value) for value in values if value is not None]
    return round(statistics.mean(filtered), 4) if filtered else None


def safe_median(values: Iterable[float | int | None]) -> float | None:
    filtered = [float(value) for value in values if value is not None]
    return round(statistics.median(filtered), 4) if filtered else None


def wilson_interval(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * (
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        ** 0.5
        / denominator
    )
    return [round(max(0, centre - margin), 4), round(min(1, centre + margin), 4)]


def summarize_results(rows: list[dict]) -> dict:
    contaminated = sum(1 for row in rows if row["contaminated"])
    metrics = {
        "tasks": len(rows),
        "avg_expected_coverage": safe_mean(row["expected_coverage"] for row in rows),
        "contamination_rate": (
            round(contaminated / len(rows), 4) if rows else None
        ),
        "contamination_ci95": wilson_interval(contaminated, len(rows)),
        "median_elapsed_minutes": safe_median(
            row["elapsed_ms"] / 60_000 for row in rows
        ),
        "median_active_minutes": safe_median(
            row["active_ms"] / 60_000 for row in rows
        ),
        "avg_answer_chars": safe_mean(row["answer_chars"] for row in rows),
        "avg_draft_count": safe_mean(row["draft_count"] for row in rows),
    }
    for field in RATING_FIELDS:
        metrics[f"avg_{field}"] = safe_mean(
            row["ratings"][field] for row in rows
        )
    return metrics


def paired_deltas(results: list[dict]) -> list[dict]:
    groups = defaultdict(dict)
    for row in results:
        groups[(row["session_id"], row["domain"])][row["condition"]] = row
    paired = []
    for (session_id, domain), conditions in groups.items():
        branch = conditions.get("branch")
        linear = conditions.get("linear")
        if branch is None or linear is None:
            continue
        item = {
            "session_id": session_id,
            "domain": domain,
            "expected_coverage_delta": round(
                branch["expected_coverage"] - linear["expected_coverage"],
                4,
            ),
            "contamination_delta": int(branch["contaminated"])
            - int(linear["contaminated"]),
            "active_minutes_delta": round(
                (branch["active_ms"] - linear["active_ms"]) / 60_000,
                4,
            ),
        }
        for field in RATING_FIELDS:
            item[f"{field}_delta"] = (
                branch["ratings"][field] - linear["ratings"][field]
            )
        paired.append(item)
    return paired


def clustered_bootstrap_ci(
    rows: list[dict],
    field: str,
    iterations: int = 5_000,
    seed: int = 20260824,
) -> list[float] | None:
    by_session = defaultdict(list)
    for row in rows:
        by_session[row["session_id"]].append(float(row[field]))
    session_ids = sorted(by_session)
    if len(session_ids) < 2:
        return None
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        sampled_values = []
        for _ in session_ids:
            sampled_values.extend(by_session[rng.choice(session_ids)])
        samples.append(statistics.mean(sampled_values))
    samples.sort()
    lower_index = int(0.025 * (len(samples) - 1))
    upper_index = int(0.975 * (len(samples) - 1))
    return [round(samples[lower_index], 4), round(samples[upper_index], 4)]


def analyze(data_dir: Path) -> tuple[dict, list[dict]]:
    sessions = read_jsonl(data_dir / "sessions.jsonl")
    results = read_jsonl(data_dir / "results.jsonl")
    completions = read_jsonl(data_dir / "completions.jsonl")
    completed_session_ids = {row["session_id"] for row in completions}
    result_session_ids = {row["session_id"] for row in results}
    completed_results = [
        row for row in results if row["session_id"] in completed_session_ids
    ]

    by_condition = {}
    all_observed_by_condition = {}
    for condition in ("linear", "branch"):
        by_condition[condition] = summarize_results(
            [row for row in completed_results if row["condition"] == condition]
        )
        all_observed_by_condition[condition] = summarize_results(
            [row for row in results if row["condition"] == condition]
        )

    by_domain_condition = {}
    for domain in ("programming", "research", "writing"):
        by_domain_condition[domain] = {}
        for condition in ("linear", "branch"):
            by_domain_condition[domain][condition] = summarize_results(
                [
                    row
                    for row in completed_results
                    if row["domain"] == domain and row["condition"] == condition
                ]
            )

    paired = paired_deltas(completed_results)
    paired_fields = (
        "expected_coverage_delta",
        "contamination_delta",
        "active_minutes_delta",
        "confidence_delta",
        "workload_delta",
        "usability_delta",
        "trust_delta",
    )
    paired_summary = {"pairs": len(paired), "sessions": len({row['session_id'] for row in paired})}
    for field in paired_fields:
        paired_summary[f"avg_{field}"] = safe_mean(row[field] for row in paired)
        paired_summary[f"{field}_cluster_bootstrap_ci95"] = clustered_bootstrap_ci(
            paired,
            field,
        )

    preferences = Counter(row["preference"] for row in completions)
    cell_counts = Counter(row["counterbalance_cell"] for row in sessions)
    task_pack_counts = Counter(
        row.get("task_pack_sha256", "missing") for row in sessions
    )
    runtime_counts = Counter(
        json.dumps(row.get("runtime", {}), sort_keys=True, ensure_ascii=False)
        for row in sessions
    )
    report = {
        "generated_at": utc_now(),
        "data_dir": str(data_dir.resolve()),
        "sessions_started": len(sessions),
        "sessions_with_results": len(result_session_ids),
        "sessions_completed": len(completed_session_ids),
        "completion_rate": (
            round(len(completed_session_ids) / len(sessions), 4) if sessions else None
        ),
        "task_results": len(results),
        "completed_session_task_results": len(completed_results),
        "counterbalance_cells": {str(cell): cell_counts[cell] for cell in range(12)},
        "task_pack_sha256_counts": dict(sorted(task_pack_counts.items())),
        "runtime_counts": dict(sorted(runtime_counts.items())),
        "protocol_consistent": len(task_pack_counts) <= 1 and len(runtime_counts) <= 1,
        "by_condition": by_condition,
        "all_observed_by_condition": all_observed_by_condition,
        "by_domain_condition": by_domain_condition,
        "paired_branch_minus_linear": paired_summary,
        "post_study": {
            "preferences": dict(sorted(preferences.items())),
            "avg_perceived_difference": safe_mean(
                row["perceived_difference"] for row in completions
            ),
        },
        "interpretation_guardrails": [
            "Expected-term coverage is a screening metric, not a complete quality score.",
            "Contamination is detected by task-specific sibling-branch terms.",
            "Paired confidence intervals resample participants as clusters.",
            "Do not make confirmatory claims before the preregistered sample is complete.",
        ],
    }
    return report, paired


def write_outputs(
    output_dir: Path,
    report: dict,
    paired: list[dict],
    results: list[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metric_fields = [
        "session_id",
        "task_id",
        "domain",
        "variant",
        "condition",
        "order",
        "answer_chars",
        "expected_coverage",
        "contaminated",
        "elapsed_ms",
        "active_ms",
        "draft_count",
        *RATING_FIELDS,
    ]
    with (output_dir / "task_metrics.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=metric_fields)
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    **{field: row.get(field) for field in metric_fields},
                    **row["ratings"],
                }
            )

    paired_fields = list(paired[0]) if paired else [
        "session_id",
        "domain",
        "expected_coverage_delta",
        "contamination_delta",
        "active_minutes_delta",
        "confidence_delta",
        "workload_delta",
        "usability_delta",
        "trust_delta",
    ]
    with (output_dir / "paired_deltas.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=paired_fields)
        writer.writeheader()
        writer.writerows(paired)

    lines = [
        "# MSC V0.2 Human Study",
        "",
        f"- Sessions started: {report['sessions_started']}",
        f"- Sessions completed: {report['sessions_completed']}",
        f"- Task results: {report['task_results']}",
        "",
        "## Condition Summary",
        "",
        "| Condition | Tasks | Expected coverage | Contamination | Active minutes | Confidence | Workload | Usability | Trust |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for condition in ("linear", "branch"):
        metrics = report["by_condition"][condition]
        coverage = metrics["avg_expected_coverage"]
        contamination = metrics["contamination_rate"]
        lines.append(
            "| "
            + " | ".join(
                [
                    condition,
                    str(metrics["tasks"]),
                    f"{coverage:.1%}" if coverage is not None else "n/a",
                    f"{contamination:.1%}" if contamination is not None else "n/a",
                    str(metrics["median_active_minutes"] or "n/a"),
                    str(metrics["avg_confidence"] or "n/a"),
                    str(metrics["avg_workload"] or "n/a"),
                    str(metrics["avg_usability"] or "n/a"),
                    str(metrics["avg_trust"] or "n/a"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Paired Branch Minus Linear",
            "",
            "```json",
            json.dumps(report["paired_branch_minus_linear"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Guardrails",
            "",
            *[f"- {item}" for item in report["interpretation_guardrails"]],
        ]
    )
    (output_dir / "report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze the MSC V0.2 human study")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    report, paired = analyze(args.data_dir)
    results = read_jsonl(args.data_dir / "results.jsonl")
    write_outputs(args.output_dir, report, paired, results)
    print(f"Sessions completed: {report['sessions_completed']}")
    print(f"Task results: {report['task_results']}")
    print(f"Report: {args.output_dir / 'report.md'}")


if __name__ == "__main__":
    main()
