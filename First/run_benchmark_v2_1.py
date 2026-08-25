import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from statistics import mean, median

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BENCHMARK = BASE_DIR / "benchmark_v2_1.json"
DEFAULT_RESULTS_DIR = BASE_DIR / "results_v2_1"

SYSTEM_PROMPT = """你是一个严格依赖当前可见对话上下文回答问题的助手。
只回答当前问题，不主动补充无关项目的信息。
如果同一个上下文里出现多个相似项目，必须根据用户当前问题和当前活动线程识别目标项目。
如果信息已经明确，直接给答案，不需要解释你看到了多少上下文。
回答尽量简短。"""

ALL_CONDITIONS = (
    "linear",
    "linear_tagged",
    "branch",
    "branch_compact_oracle",
)

def load_benchmark(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def copy_messages(messages):
    return [{"role": m["role"], "content": m["content"]} for m in messages]

def active_marker(case):
    return f"[当前活动线程：{case['active_branch_name']}]"

def compile_messages(data, case, condition, recent_messages=6):
    family = data["families"][case["family_id"]]
    active_id = case["active_branch_id"]
    active = family["branches"][active_id]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(copy_messages(family["shared_history"]))

    if condition in ("linear", "linear_tagged"):
        # 两个条件看到完全一样的完整历史和完全一样的 branch 顺序。
        for branch_id in case["linear_branch_order"]:
            branch = family["branches"][branch_id]
            messages.append({
                "role": "user",
                "content": f"接下来讨论 {branch['name']}。",
            })
            messages.extend(copy_messages(branch["history"]))

        if condition == "linear_tagged":
            # 唯一额外信息：明确当前活动线程。
            messages.append({
                "role": "system",
                "content": active_marker(case),
            })

    elif condition == "branch":
        messages.append({
            "role": "system",
            "content": active_marker(case),
        })
        messages.append({
            "role": "user",
            "content": f"接下来讨论 {active['name']}。",
        })
        messages.extend(copy_messages(active["history"]))

    elif condition == "branch_compact_oracle":
        messages.append({
            "role": "system",
            "content": active_marker(case),
        })
        messages.append({
            "role": "user",
            "content": f"下面是当前分支 {active['name']} 的已确认摘要。",
        })
        messages.append({
            "role": "assistant",
            "content": active["oracle_summary"],
        })
        if recent_messages > 0:
            messages.extend(copy_messages(active["history"][-recent_messages:]))

    else:
        raise ValueError(f"Unknown condition: {condition}")

    messages.append({"role": "user", "content": case["question"]})
    return messages

def call_ollama(model, messages, num_ctx=8192, timeout=180):
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            "无法连接 Ollama。请确认 Ollama 正在运行，而且模型已经 pull。\n"
            f"原始错误：{e}"
        ) from e

    latency_ms = (time.perf_counter() - started) * 1000

    return {
        "answer": result.get("message", {}).get("content", "").strip(),
        "prompt_tokens": result.get("prompt_eval_count"),
        "response_tokens": result.get("eval_count"),
        "latency_ms": round(latency_ms, 2),
    }

def normalize(text):
    return (
        str(text)
        .lower()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("，", ",")
        .replace("：", ":")
    )

def boundary_pattern(value):
    """
    边界感知匹配：
    - 49美元/月 不应命中 149美元/月
    - 0 不应命中 0.2
    - 2TB 不应命中 12TB
    对中文/字母混合值保留完整字符串匹配。
    """
    needle = normalize(value)
    escaped = re.escape(needle)

    prefix = ""
    suffix = ""

    if needle and needle[0].isdigit():
        prefix = r"(?<![\d.])"
    elif needle and needle[0].isascii() and needle[0].isalnum():
        prefix = r"(?<![a-z0-9])"

    if needle and needle[-1].isdigit():
        suffix = r"(?![\d.])"
    elif needle and needle[-1].isascii() and needle[-1].isalnum():
        suffix = r"(?![a-z0-9])"

    return prefix + escaped + suffix

def contains_value(text, value):
    hay = normalize(text)
    return re.search(boundary_pattern(value), hay) is not None

def evaluate(answer, expected_contains, forbidden_contains):
    expected_hits = [v for v in expected_contains if contains_value(answer, v)]
    forbidden_hits = [v for v in forbidden_contains if contains_value(answer, v)]

    return {
        "correct": len(expected_hits) == len(expected_contains),
        "polluted": len(forbidden_hits) > 0,
        "expected_hits": expected_hits,
        "forbidden_hits": forbidden_hits,
    }

def estimate_chars(messages):
    return sum(len(m.get("content", "")) for m in messages)

def safe_mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(mean(values), 2) if values else None

def safe_median(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(median(values), 2) if values else None

def summarize_subset(rows):
    total = len(rows)
    if not total:
        return {}

    correct = sum(1 for r in rows if r["correct"])
    polluted = sum(1 for r in rows if r["polluted"])
    wrong_not_polluted = sum(
        1 for r in rows if (not r["correct"]) and (not r["polluted"])
    )

    return {
        "runs": total,
        "accuracy": round(correct / total, 4),
        "pollution_rate": round(polluted / total, 4),
        "wrong_without_pollution_rate": round(wrong_not_polluted / total, 4),
        "avg_prompt_tokens": safe_mean([r["prompt_tokens"] for r in rows]),
        "median_prompt_tokens": safe_median([r["prompt_tokens"] for r in rows]),
        "avg_latency_ms": safe_mean([r["latency_ms"] for r in rows]),
        "avg_response_tokens": safe_mean([r["response_tokens"] for r in rows]),
        "avg_prompt_chars": safe_mean([r["prompt_chars"] for r in rows]),
    }

def build_summary(rows, model, repeats):
    summary = {
        "model": model,
        "repeats": repeats,
        "overall": {},
        "by_question_mode": {},
        "by_active_position": {},
        "relative_to_linear_tagged": {},
        "relative_to_linear": {},
    }

    conditions = [c for c in ALL_CONDITIONS if any(r["condition"] == c for r in rows)]

    for condition in conditions:
        summary["overall"][condition] = summarize_subset(
            [r for r in rows if r["condition"] == condition]
        )

    for mode in ("implicit", "explicit"):
        summary["by_question_mode"][mode] = {}
        for condition in conditions:
            subset = [
                r for r in rows
                if r["condition"] == condition and r["question_mode"] == mode
            ]
            if subset:
                summary["by_question_mode"][mode][condition] = summarize_subset(subset)

    # 主要用来观察 linear 的 recency / position bias。
    for pos in range(5):
        key = str(pos)
        summary["by_active_position"][key] = {}
        for condition in ("linear", "linear_tagged"):
            subset = [
                r for r in rows
                if r["condition"] == condition and int(r["active_branch_position"]) == pos
            ]
            if subset:
                summary["by_active_position"][key][condition] = summarize_subset(subset)

    linear = summary["overall"].get("linear", {})
    tagged = summary["overall"].get("linear_tagged", {})

    linear_tokens = linear.get("avg_prompt_tokens")
    tagged_tokens = tagged.get("avg_prompt_tokens")

    if linear_tokens:
        for condition in conditions:
            if condition == "linear":
                continue
            tokens = summary["overall"][condition].get("avg_prompt_tokens")
            if tokens:
                summary["relative_to_linear"][condition] = {
                    "prompt_token_reduction_pct": round(
                        (linear_tokens - tokens) / linear_tokens * 100, 2
                    )
                }

    if tagged_tokens:
        for condition in conditions:
            if condition == "linear_tagged":
                continue
            tokens = summary["overall"][condition].get("avg_prompt_tokens")
            if tokens:
                summary["relative_to_linear_tagged"][condition] = {
                    "prompt_token_reduction_pct": round(
                        (tagged_tokens - tokens) / tagged_tokens * 100, 2
                    )
                }

    return summary

def print_summary(summary):
    print("\n" + "=" * 82)
    print("Benchmark V2.1 汇总")
    print("=" * 82)

    for condition, s in summary["overall"].items():
        print(f"\n[{condition.upper()}]")
        print(f"Runs                    : {s['runs']}")
        print(f"Accuracy                : {s['accuracy']:.2%}")
        print(f"Pollution Rate          : {s['pollution_rate']:.2%}")
        print(f"Wrong w/o Pollution     : {s['wrong_without_pollution_rate']:.2%}")
        print(f"Avg Prompt Tokens       : {s['avg_prompt_tokens']}")
        print(f"Median Prompt Tokens    : {s['median_prompt_tokens']}")
        print(f"Avg Latency             : {s['avg_latency_ms']} ms")

    if summary["relative_to_linear_tagged"]:
        print("\n相对 Linear Tagged 的 Prompt Token 降幅：")
        for condition, s in summary["relative_to_linear_tagged"].items():
            print(f"  {condition}: {s['prompt_token_reduction_pct']}%")

    print("\n按问题类型拆分：")
    for mode, by_condition in summary["by_question_mode"].items():
        print(f"\n  {mode}:")
        for condition, s in by_condition.items():
            print(
                f"    {condition:<22} "
                f"acc={s['accuracy']:.2%} "
                f"pollution={s['pollution_rate']:.2%} "
                f"tokens={s['avg_prompt_tokens']}"
            )

    if summary["by_active_position"]:
        print("\nLinear / Linear Tagged 按 active branch 在线性历史中的位置：")
        print("  position 0=最早，4=最晚")
        for pos, by_condition in summary["by_active_position"].items():
            if not by_condition:
                continue
            print(f"  position {pos}:")
            for condition, s in by_condition.items():
                print(
                    f"    {condition:<14} "
                    f"acc={s['accuracy']:.2%} "
                    f"pollution={s['pollution_rate']:.2%}"
                )

def load_existing(csv_path):
    if not csv_path.exists():
        return [], set()

    rows = []
    keys = set()
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["repeat"] = int(row["repeat"])
            row["prompt_tokens"] = int(row["prompt_tokens"]) if row["prompt_tokens"] else None
            row["response_tokens"] = int(row["response_tokens"]) if row["response_tokens"] else None
            row["latency_ms"] = float(row["latency_ms"]) if row["latency_ms"] else None
            row["prompt_chars"] = int(row["prompt_chars"])
            row["correct"] = row["correct"].lower() == "true"
            row["polluted"] = row["polluted"].lower() == "true"
            row["active_branch_position"] = int(row["active_branch_position"])
            rows.append(row)
            keys.add((row["case_id"], row["condition"], row["repeat"]))
    return rows, keys

FIELDS = [
    "repeat", "case_id", "family_id", "active_branch_id",
    "active_branch_name", "question_mode", "condition", "question", "answer",
    "prompt_tokens", "response_tokens", "prompt_chars", "latency_ms",
    "correct", "polluted", "expected_hits", "forbidden_hits",
    "active_branch_position", "active_history_messages",
]

def write_csv(csv_path, rows):
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

def main():
    parser = argparse.ArgumentParser(description="Run Benchmark V2.1")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 道题；0=全部")
    parser.add_argument(
        "--conditions",
        default="linear,linear_tagged,branch,branch_compact_oracle",
        help="逗号分隔",
    )
    parser.add_argument("--recent-messages", type=int, default=6)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    args = parser.parse_args()

    conditions = tuple(c.strip() for c in args.conditions.split(",") if c.strip())
    unknown = [c for c in conditions if c not in ALL_CONDITIONS]
    if unknown:
        raise SystemExit(f"未知 condition: {unknown}")

    if args.repeats < 1:
        raise SystemExit("--repeats 必须 >= 1")

    data = load_benchmark(args.benchmark)
    cases = data["questions"][: args.limit or None]

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "results_v2_1.csv"
    summary_path = results_dir / "summary_v2_1.json"

    rows, completed = (load_existing(csv_path) if args.resume else ([], set()))

    print(f"Model      : {args.model}")
    print(f"Questions  : {len(cases)}")
    print(f"Conditions : {', '.join(conditions)}")
    print(f"Repeats    : {args.repeats}")
    print(f"num_ctx    : {args.num_ctx}")
    print("-" * 82)

    if args.dry_run:
        for case in cases[:3]:
            print(
                f"\n{case['id']} | {case['question_mode']} | "
                f"active={case['active_branch_name']} | "
                f"position={case['active_branch_position']}"
            )
            print(f"Q: {case['question']}")
            print("linear order:", " -> ".join(
                data["families"][case["family_id"]]["branches"][bid]["name"]
                for bid in case["linear_branch_order"]
            ))
            for condition in conditions:
                msgs = compile_messages(data, case, condition, args.recent_messages)
                print(
                    f"  {condition:<22} "
                    f"messages={len(msgs):>3} "
                    f"chars={estimate_chars(msgs):>6}"
                )
        print("\nDry run 完成，没有调用 Ollama。")
        return

    total_jobs = len(cases) * len(conditions) * args.repeats
    job_index = 0

    for repeat_index in range(1, args.repeats + 1):
        for case in cases:
            for condition in conditions:
                job_index += 1
                key = (case["id"], condition, repeat_index)

                if key in completed:
                    print(f"[{job_index}/{total_jobs}] skip {case['id']} | {condition}")
                    continue

                messages = compile_messages(
                    data, case, condition, args.recent_messages
                )

                print(
                    f"[{job_index}/{total_jobs}] "
                    f"{case['id']} | {case['question_mode']} | "
                    f"pos={case['active_branch_position']} | {condition} ..."
                )

                result = call_ollama(
                    args.model,
                    messages,
                    num_ctx=args.num_ctx,
                    timeout=args.timeout,
                )
                evaluation = evaluate(
                    result["answer"],
                    case["expected_contains"],
                    case["forbidden_contains"],
                )

                row = {
                    "repeat": repeat_index,
                    "case_id": case["id"],
                    "family_id": case["family_id"],
                    "active_branch_id": case["active_branch_id"],
                    "active_branch_name": case["active_branch_name"],
                    "question_mode": case["question_mode"],
                    "condition": condition,
                    "question": case["question"],
                    "answer": result["answer"],
                    "prompt_tokens": result["prompt_tokens"],
                    "response_tokens": result["response_tokens"],
                    "prompt_chars": estimate_chars(messages),
                    "latency_ms": result["latency_ms"],
                    "correct": evaluation["correct"],
                    "polluted": evaluation["polluted"],
                    "expected_hits": " | ".join(evaluation["expected_hits"]),
                    "forbidden_hits": " | ".join(evaluation["forbidden_hits"]),
                    "active_branch_position": case["active_branch_position"],
                    "active_history_messages": case["active_history_messages"],
                }
                rows.append(row)
                completed.add(key)

                status = (
                    ("正确" if row["correct"] else "错误")
                    + " / "
                    + ("污染" if row["polluted"] else "未污染")
                )
                print(
                    f"  -> {status} | prompt={row['prompt_tokens']} | "
                    f"{row['latency_ms']} ms"
                )
                print(f"  -> {row['answer'][:180]}")

                write_csv(csv_path, rows)

    summary = build_summary(rows, args.model, args.repeats)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print_summary(summary)
    print(f"\nCSV    : {csv_path}")
    print(f"Summary: {summary_path}")

if __name__ == "__main__":
    main()
