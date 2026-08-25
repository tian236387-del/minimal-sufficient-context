import argparse
import csv
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from statistics import mean

BASE_DIR = Path(__file__).resolve().parent
TEST_CASES_FILE = BASE_DIR / "test_cases.json"
RESULTS_DIR = BASE_DIR / "results"

SYSTEM_PROMPT = """你是一个严格依赖给定上下文回答问题的助手。
只回答当前问题，不主动扩展无关话题。
如果上下文中存在多个相似项目或主题，请根据当前上下文判断，不要混用其他主题的信息。
回答尽量简短。"""


def load_cases():
    with open(TEST_CASES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compile_linear(case):
    """
    模拟传统单线聊天：
    共同上下文、A 分支、B 分支全部保留。
    注意：B 放在 A 后面，故意制造“更近但无关”的干扰。
    """
    parts = [
        "以下是此前完整对话中积累的信息：",
        case["shared_context"],
        case["branch_a"],
        case["branch_b"],
        "",
        f"当前问题：{case['question']}",
    ]
    return "\n\n".join(parts)


def compile_branch(case):
    """
    模拟 Branch-aware Context：
    只保留共同上下文和当前分支。
    """
    active = case["branch_a"] if case["active_branch"] == "A" else case["branch_b"]
    parts = [
        "以下是当前分支真正相关的信息：",
        case["shared_context"],
        active,
        "",
        f"当前问题：{case['question']}",
    ]
    return "\n\n".join(parts)


def call_ollama(model, user_content):
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "options": {
            "temperature": 0
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            "无法连接 Ollama。请确认 Ollama 已启动，并且模型已经 pull。\n"
            f"原始错误：{e}"
        ) from e

    latency_ms = (time.perf_counter() - started) * 1000
    answer = result.get("message", {}).get("content", "").strip()

    return {
        "answer": answer,
        "prompt_tokens": result.get("prompt_eval_count"),
        "response_tokens": result.get("eval_count"),
        "latency_ms": round(latency_ms, 2),
    }


def normalize(text):
    return str(text).lower().replace(" ", "")


def evaluate(answer, expected_contains, forbidden_contains):
    normalized = normalize(answer)

    expected_hits = [
        term for term in expected_contains
        if normalize(term) in normalized
    ]
    forbidden_hits = [
        term for term in forbidden_contains
        if normalize(term) in normalized
    ]

    correct = len(expected_hits) == len(expected_contains)
    polluted = len(forbidden_hits) > 0

    return {
        "correct": correct,
        "polluted": polluted,
        "expected_hits": expected_hits,
        "forbidden_hits": forbidden_hits,
    }


def run_condition(case, condition, model):
    if condition == "linear":
        context = compile_linear(case)
    elif condition == "branch":
        context = compile_branch(case)
    else:
        raise ValueError(condition)

    result = call_ollama(model, context)
    evaluation = evaluate(
        result["answer"],
        case["expected_contains"],
        case["forbidden_contains"],
    )

    return {
        "case_id": case["id"],
        "condition": condition,
        "question": case["question"],
        "answer": result["answer"],
        "prompt_tokens": result["prompt_tokens"],
        "response_tokens": result["response_tokens"],
        "latency_ms": result["latency_ms"],
        "correct": evaluation["correct"],
        "polluted": evaluation["polluted"],
        "expected_hits": " | ".join(evaluation["expected_hits"]),
        "forbidden_hits": " | ".join(evaluation["forbidden_hits"]),
    }


def safe_mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(mean(values), 2) if values else None


def build_summary(rows, model, repeats):
    summary = {
        "model": model,
        "repeats": repeats,
        "conditions": {}
    }

    for condition in ("linear", "branch"):
        subset = [r for r in rows if r["condition"] == condition]
        total = len(subset)
        correct_count = sum(1 for r in subset if r["correct"])
        polluted_count = sum(1 for r in subset if r["polluted"])

        summary["conditions"][condition] = {
            "runs": total,
            "accuracy": round(correct_count / total, 4) if total else None,
            "pollution_rate": round(polluted_count / total, 4) if total else None,
            "avg_prompt_tokens": safe_mean([r["prompt_tokens"] for r in subset]),
            "avg_latency_ms": safe_mean([r["latency_ms"] for r in subset]),
        }

    linear = summary["conditions"]["linear"]
    branch = summary["conditions"]["branch"]

    if linear["avg_prompt_tokens"] and branch["avg_prompt_tokens"]:
        summary["token_reduction_pct"] = round(
            (linear["avg_prompt_tokens"] - branch["avg_prompt_tokens"])
            / linear["avg_prompt_tokens"] * 100,
            2,
        )

    return summary


def print_summary(summary):
    print("\n" + "=" * 64)
    print("实验汇总")
    print("=" * 64)

    for condition in ("linear", "branch"):
        s = summary["conditions"][condition]
        print(f"\n[{condition.upper()}]")
        print(f"Accuracy        : {s['accuracy']:.2%}")
        print(f"Pollution Rate  : {s['pollution_rate']:.2%}")
        print(f"Avg Prompt Token: {s['avg_prompt_tokens']}")
        print(f"Avg Latency     : {s['avg_latency_ms']} ms")

    if "token_reduction_pct" in summary:
        print(f"\nBranch Prompt Token Reduction: {summary['token_reduction_pct']}%")

    print("\n结果已保存到 results/results.csv 和 results/summary.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    if args.repeats < 1:
        raise SystemExit("--repeats 必须 >= 1")

    cases = load_cases()
    RESULTS_DIR.mkdir(exist_ok=True)

    rows = []

    print(f"Model   : {args.model}")
    print(f"Cases   : {len(cases)}")
    print(f"Repeats : {args.repeats}")
    print("-" * 64)

    for repeat_index in range(args.repeats):
        for case in cases:
            for condition in ("linear", "branch"):
                print(
                    f"Run {repeat_index + 1}/{args.repeats} | "
                    f"{case['id']} | {condition} ..."
                )
                row = run_condition(case, condition, args.model)
                row["repeat"] = repeat_index + 1
                rows.append(row)

                status = []
                status.append("正确" if row["correct"] else "错误")
                status.append("污染" if row["polluted"] else "未污染")
                print(
                    f"  -> {' / '.join(status)} | "
                    f"prompt={row['prompt_tokens']} tokens | "
                    f"{row['latency_ms']} ms"
                )
                print(f"  -> {row['answer'][:160]}")
                print()

    csv_path = RESULTS_DIR / "results.csv"
    fields = [
        "repeat", "case_id", "condition", "question", "answer",
        "prompt_tokens", "response_tokens", "latency_ms",
        "correct", "polluted", "expected_hits", "forbidden_hits"
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = build_summary(rows, args.model, args.repeats)
    summary_path = RESULTS_DIR / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print_summary(summary)


if __name__ == "__main__":
    main()
