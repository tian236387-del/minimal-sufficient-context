"""
直接重新评分已经跑过的 V2.2 CSV，不重新调用 Ollama。

默认读取：
    results_v2_2/results_v2_2.csv
    benchmark_v2_2.json

输出：
    results_v2_2/results_v2_2_rescored.csv
    results_v2_2/summary_v2_2_rescored.json
"""

import csv
import json
from pathlib import Path

import run_benchmark_v2_2 as base
import run_benchmark_v2_2_fixed as fixed


ROOT = Path(__file__).resolve().parent
BENCHMARK_PATH = ROOT / "benchmark_v2_2.json"
RESULTS_DIR = ROOT / "results_v2_2"
INPUT_CSV = RESULTS_DIR / "results_v2_2.csv"
OUTPUT_CSV = RESULTS_DIR / "results_v2_2_rescored.csv"
OUTPUT_SUMMARY = RESULTS_DIR / "summary_v2_2_rescored.json"


def parse_bool(value):
    return str(value).strip().lower() == "true"


def main():
    fixed.self_test_evaluator()

    if not BENCHMARK_PATH.exists():
        raise SystemExit(f"找不到 benchmark: {BENCHMARK_PATH}")

    if not INPUT_CSV.exists():
        raise SystemExit(f"找不到结果 CSV: {INPUT_CSV}")

    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in benchmark["questions"]}

    rows = []

    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for raw in reader:
            case_id = raw["case_id"]
            case = cases[case_id]

            evaluation = base.evaluate(
                raw["answer"],
                case["expected_contains"],
                case["forbidden_contains"],
            )

            row = dict(raw)

            # 转成 summary builder 需要的类型
            row["repeat"] = int(row["repeat"])
            row["prompt_tokens"] = (
                int(row["prompt_tokens"]) if row["prompt_tokens"] else None
            )
            row["response_tokens"] = (
                int(row["response_tokens"]) if row["response_tokens"] else None
            )
            row["prompt_chars"] = int(row["prompt_chars"])
            row["latency_ms"] = (
                float(row["latency_ms"]) if row["latency_ms"] else None
            )
            row["active_branch_position"] = int(row["active_branch_position"])
            row["active_history_messages"] = int(row["active_history_messages"])

            row["correct"] = evaluation["correct"]
            row["polluted"] = evaluation["polluted"]
            row["expected_hits"] = " | ".join(evaluation["expected_hits"])
            row["forbidden_hits"] = " | ".join(evaluation["forbidden_hits"])

            rows.append(row)

    # 写新的 rescored CSV
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=base.FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    model = "unknown"
    repeats = 1

    # 如果原 summary 存在，沿用 model / repeats
    old_summary = RESULTS_DIR / "summary_v2_2.json"
    if old_summary.exists():
        old = json.loads(old_summary.read_text(encoding="utf-8"))
        model = old.get("model", model)
        repeats = old.get("repeats", repeats)

    summary = base.build_summary(rows, model, repeats)

    OUTPUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n重新评分完成。没有调用 Ollama。")
    base.print_summary(summary)
    print(f"\nRescored CSV    : {OUTPUT_CSV}")
    print(f"Rescored Summary: {OUTPUT_SUMMARY}")


if __name__ == "__main__":
    main()
