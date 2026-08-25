"""
V2.2 evaluator 修正版 v2。

只修评分逻辑，不修改：
- prompt
- benchmark 数据
- Context 策略
- 模型参数

直接覆盖旧的 run_benchmark_v2_2_fixed.py 即可。
"""

import re
import run_benchmark_v2_2 as base


def normalize_basic(text):
    s = str(text).lower().strip()

    replacements = {
        "，": ",",
        "：": ":",
        "／": "/",
        "％": "%",
        "（": "(",
        "）": ")",
        "　": " ",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)

    # 保留词之间的边界：换行 / Tab / 多空格 -> 一个普通空格
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonicalize(text):
    s = normalize_basic(text)

    # 统一斜杠两侧空格
    s = re.sub(r"\s*/\s*", "/", s)

    # 月付表达
    s = re.sub(r"\bper\s*month(?![a-z0-9])", "/月", s)
    s = re.sub(r"/\s*month(?![a-z0-9])", "/月", s)
    s = re.sub(r"\bmonthly(?![a-z0-9])", "/月", s)
    s = re.sub(r"/\s*mo(?![a-z0-9])", "/月", s)
    s = s.replace("每月", "/月")

    # 美元表达
    s = re.sub(r"\$\s*(\d+(?:\.\d+)?)", r"\1美元", s)
    s = re.sub(r"\busd\s*(\d+(?:\.\d+)?)", r"\1美元", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*usd(?![a-z0-9])", r"\1美元", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*美元\s*/月", r"\1美元/月", s)

    # 存储单位：
    # 不使用 \b，因为 Python 的 \b 会把中文字符视为“单词字符”。
    # 例如 "2tb存储" 中 b 和 “存” 之间没有 \b。
    # 改用 ASCII-aware lookahead。
    s = re.sub(
        r"(\d+(?:\.\d+)?)\s*(gb|tb)(?![a-z0-9])",
        lambda m: m.group(1) + m.group(2).lower(),
        s,
        flags=re.I,
    )

    return s


def boundary_pattern(canonical_value):
    needle = canonical_value
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
    hay = canonicalize(text)
    needle = canonicalize(value)
    return re.search(boundary_pattern(needle), hay) is not None


def self_test_evaluator():
    tests = [
        # 金额等价表达
        ("$149/月，2TB存储", "149美元/月", True),
        ("USD149/month, 2TB", "149美元/月", True),
        ("149美元每月，2TB", "149美元/月", True),

        # 防止 substring 污染误判
        ("149美元/月", "49美元/月", False),
        ("temperature 0.2", "0", False),
        ("temperature 0", "0", True),
        ("12TB", "2TB", False),

        # 空格 / 换行
        ("Qwen3 1.7B\n0", "Qwen3 1.7B", True),
        ("Qwen3 1.7B\n0", "0", True),
        ("Qwen3 1.7B\n0.2", "0", False),

        # V2.2 新发现：ASCII 单位后紧跟中文
        ("包含2TB存储", "2TB", True),
        ("包含50GB存储", "50GB", True),
        ("2 tb", "2TB", True),
        ("200GB容量", "200GB", True),
        ("12TB存储", "2TB", False),
    ]

    failures = []
    for text, value, expected in tests:
        got = contains_value(text, value)
        if got != expected:
            failures.append((text, value, expected, got))

    if failures:
        raise RuntimeError(f"Evaluator self-test failed: {failures}")

    print("Evaluator fixed self-test v2: PASS")


# 替换原 V2.2 模块中的 evaluator 函数
base.normalize_basic = normalize_basic
base.canonicalize = canonicalize
base.boundary_pattern = boundary_pattern
base.contains_value = contains_value
base.self_test_evaluator = self_test_evaluator


if __name__ == "__main__":
    base.main()
