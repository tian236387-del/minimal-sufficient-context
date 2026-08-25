import argparse
import json
import random
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "benchmark_v2_1.json"

FAMILY_SPECS = [
    ("nova", "Nova", ["Web", "Mobile", "Admin", "Data", "Edge"]),
    ("atlas", "Atlas", ["Analytics", "Admin", "Sync", "Core", "Studio"]),
    ("helios", "Helios", ["API", "Worker", "Portal", "Ops", "Search"]),
    ("phoenix", "Phoenix", ["Desktop", "Mobile", "Console", "Cloud", "Labs"]),
    ("orbit", "Orbit", ["CRM", "Billing", "Support", "Growth", "Insights"]),
    ("aurora", "Aurora", ["Web", "Mobile", "Engine", "Studio", "Hub"]),
    ("vector", "Vector", ["Search", "Index", "Query", "Console", "Agent"]),
    ("nimbus", "Nimbus", ["API", "Jobs", "Flow", "Desk", "Lake"]),
    ("meridian", "Meridian", ["Core", "Pay", "Auth", "Data", "App"]),
    ("zenith", "Zenith", ["One", "Pro", "Teams", "Edge", "Lab"]),
]

VALUE_POOLS = {
    "database": ["PostgreSQL 16", "MongoDB 7", "MySQL 8", "SQLite", "CockroachDB"],
    "backend": ["FastAPI", "Express", "Django", "Spring Boot", "Go Gin"],
    "cache": ["Redis", "Memcached", "KeyDB", "Dragonfly", "无缓存"],
    "region": [
        "AWS ap-southeast-1",
        "GCP us-central1",
        "Azure eastasia",
        "AWS eu-west-1",
        "GCP asia-northeast1",
    ],
    "owner": ["Alice", "Bob", "Carol", "David", "Eve"],
    "deadline": ["8月30日", "9月15日", "10月8日", "11月12日", "12月3日"],
    "price": ["29美元/月", "49美元/月", "79美元/月", "99美元/月", "149美元/月"],
    "storage": ["50GB", "200GB", "500GB", "1TB", "2TB"],
    "model": ["Qwen3 1.7B", "Llama 3.2 3B", "Gemma 3 4B", "Phi-4 Mini", "Qwen3 4B"],
    "temperature": ["0", "0.2", "0.4", "0.7", "1.0"],
    "port": ["8100", "8200", "8300", "9200", "9300"],
}

LEGACY_VALUES = {
    "database": "MariaDB 10.11",
    "backend": "Flask",
    "cache": "本地 LRU",
    "region": "东京自建机房",
    "owner": "Frank",
    "deadline": "7月1日",
    "price": "19美元/月",
    "storage": "20GB",
    "model": "TinyLlama 1.1B",
    "temperature": "0.9",
    "port": "7000",
}

FIELD_LABELS = {
    "database": "数据库",
    "backend": "后端框架",
    "cache": "缓存",
    "region": "部署区域",
    "owner": "负责人",
    "deadline": "最终发布日期",
    "price": "Pro 价格",
    "storage": "Pro 存储",
    "model": "本地模型",
    "temperature": "temperature",
    "port": "服务端口",
}

QUESTION_SPECS = [
    ("database_owner", ["database", "owner"], "当前这个项目最终使用什么数据库，负责人是谁？"),
    ("backend_cache", ["backend", "cache"], "{name} 当前最终采用什么后端框架和缓存？"),
    ("region_port", ["region", "port"], "当前这个项目部署在哪个区域，服务端口是多少？"),
    ("deadline_owner", ["deadline", "owner"], "{name} 的最终发布日期是什么时候，负责人是谁？"),
    ("price_storage", ["price", "storage"], "当前这个产品的 Pro 方案多少钱，包含多少存储？"),
    ("model_temperature", ["model", "temperature"], "{name} 的本地模型和 temperature 最终分别是什么？"),
    ("database_backend", ["database", "backend"], "当前这个项目的数据库和后端框架最终是什么？"),
    ("region_database", ["region", "database"], "{name} 部署在哪个区域，生产数据库是什么？"),
    ("deadline_port", ["deadline", "port"], "当前这个版本最终发布日期和服务端口分别是什么？"),
    ("model_storage", ["model", "storage"], "{name} 当前选用什么本地模型，Pro 存储是多少？"),
]

FILLER_TOPICS = [
    "日志格式统一为结构化日志，但暂时不影响核心技术栈。",
    "团队讨论过 UI 主题色，决定以后再处理。",
    "监控仪表盘还在整理，不影响当前发布计划。",
    "文档目录需要清理，但这不是本轮决策重点。",
    "测试数据会在发布前刷新一次。",
    "代码仓库命名规范保持不变。",
    "CI 的提示信息准备稍后优化。",
    "产品截图会在最终发布前重新制作。",
    "错误码文档计划补充更多示例。",
    "团队周会时间已经确认，但和技术决策无关。",
    "埋点命名将统一使用 snake_case。",
    "README 需要补充本地开发说明。",
    "接口示例将增加更多边界情况。",
    "设计稿中的占位图标稍后替换。",
    "客服 FAQ 会在上线后继续补充。",
    "压测报告模板还没有最终定稿。",
    "代码注释规范准备单独开会讨论。",
    "开发环境的示例账号需要重新生成。",
    "告警文案会在下个迭代优化。",
    "演示环境数据将在发布前一天重置。",
]

def rotate_values(family_index, branch_index):
    return {
        field: pool[(family_index + branch_index) % len(pool)]
        for field, pool in VALUE_POOLS.items()
    }

def add_pair(history, user_text, assistant_text):
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})

def make_history(name, facts, rng, target_messages):
    history = []
    add_pair(
        history,
        f"我们现在单独讨论 {name}，后面的决定都只属于这个项目。",
        f"明白，当前主题是 {name}，我会把它和其他相似项目区分开。",
    )

    for field in ["database", "owner", "deadline", "backend"]:
        old = LEGACY_VALUES[field]
        label = FIELD_LABELS[field]
        add_pair(
            history,
            f"{name} 早期曾考虑把{label}设为 {old}，但这只是旧草案，还没定。",
            f"已记录：{old} 只是 {name} 的旧草案，不应当作最终结论。",
        )

    fillers = FILLER_TOPICS[:]
    rng.shuffle(fillers)
    for filler in fillers[:6]:
        role = "user" if len(history) % 2 == 0 else "assistant"
        prefix = f"{name}：" if role == "user" else ""
        history.append({"role": role, "content": prefix + filler})

    fields = list(facts.keys())
    rng.shuffle(fields)
    for i, field in enumerate(fields):
        label = FIELD_LABELS[field]
        value = facts[field]
        if i % 3 == 0:
            add_pair(
                history,
                f"{name} 这项已经定了：{label}最终改为 {value}，以前的相关方案全部作废。",
                f"确认，{name} 的最终{label}是 {value}。",
            )
        else:
            history.append({
                "role": "user",
                "content": f"{name} 最终{label}：{value}。这是当前有效结论。",
            })

    filler_index = 0
    while len(history) < max(target_messages - 4, 0):
        text = FILLER_TOPICS[filler_index % len(FILLER_TOPICS)]
        role = "assistant" if len(history) % 2 else "user"
        content = (
            f"{name}：{text}"
            if role == "user"
            else f"已记录 {name} 的补充讨论：{text}"
        )
        history.append({"role": role, "content": content})
        filler_index += 1

    tail_fields = rng.sample(list(facts.keys()), k=2)
    for field in tail_fields:
        if len(history) >= target_messages:
            break
        history.append({
            "role": "assistant",
            "content": f"{name} 当前有效的{FIELD_LABELS[field]}仍然是 {facts[field]}。",
        })

    return history[:target_messages]

def make_oracle_summary(name, facts):
    lines = [f"{name} 当前有效结论："]
    for field in [
        "database", "backend", "cache", "region", "owner",
        "deadline", "price", "storage", "model", "temperature", "port"
    ]:
        lines.append(f"- {FIELD_LABELS[field]}：{facts[field]}")
    return "\n".join(lines)

def unique_preserve(values):
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out

def make_linear_order(branch_ids, active_branch_id, desired_position, rng):
    """
    随机排列 sibling，同时把 active branch 放到预定位置。
    这样 active branch 不会总是最新/最旧，避免 recency confound。
    """
    siblings = [b for b in branch_ids if b != active_branch_id]
    rng.shuffle(siblings)
    order = siblings[:]
    order.insert(desired_position, active_branch_id)
    return order

def generate(seed=42, history_min=30, history_max=50):
    rng = random.Random(seed)
    families = {}
    questions = []

    for family_index, (family_id, base_name, suffixes) in enumerate(FAMILY_SPECS):
        shared_history = [
            {
                "role": "user",
                "content": f"我正在并行推进 {base_name} 系列的多个相似项目，它们的名称和技术讨论很接近。",
            },
            {
                "role": "assistant",
                "content": f"明白。{base_name} 系列包含多个独立项目，我会注意不要混用它们的事实。",
            },
            {
                "role": "user",
                "content": "每个项目的数据库、负责人、部署区域、发布日期、定价和模型选择都可能不同。",
            },
            {
                "role": "assistant",
                "content": "收到。后续应以具体项目自己的最终结论为准。",
            },
        ]

        branches = {}
        branch_ids = []
        for branch_index, suffix in enumerate(suffixes):
            branch_id = f"{family_id}_b{branch_index + 1}"
            branch_ids.append(branch_id)
            name = f"{base_name} {suffix}"
            facts = rotate_values(family_index, branch_index)
            branch_rng = random.Random(seed * 1000 + family_index * 100 + branch_index)
            target_messages = branch_rng.randint(history_min, history_max)
            history = make_history(name, facts, branch_rng, target_messages)
            branches[branch_id] = {
                "id": branch_id,
                "name": name,
                "facts": facts,
                "history": history,
                "oracle_summary": make_oracle_summary(name, facts),
                "history_messages": len(history),
            }

        families[family_id] = {
            "id": family_id,
            "base_name": base_name,
            "shared_history": shared_history,
            "branch_order": branch_ids,
            "branches": branches,
        }

        # 每个 family 10 道题，共 100 道。
        # active branch 轮转；其在线性历史里的位置也轮转 0..4。
        for q_index, (question_id, fields, template) in enumerate(QUESTION_SPECS):
            active_branch_id = branch_ids[q_index % len(branch_ids)]
            active = branches[active_branch_id]
            question_mode = "implicit" if q_index % 2 == 0 else "explicit"
            question_text = (
                template
                if question_mode == "implicit"
                else template.format(name=active["name"])
            )

            expected = [active["facts"][field] for field in fields]
            forbidden = []
            for sibling_id in branch_ids:
                if sibling_id == active_branch_id:
                    continue
                sibling = branches[sibling_id]
                forbidden.extend(sibling["facts"][field] for field in fields)

            order_rng = random.Random(
                seed * 100000 + family_index * 1000 + q_index
            )
            desired_position = (family_index + q_index) % len(branch_ids)
            linear_branch_order = make_linear_order(
                branch_ids,
                active_branch_id,
                desired_position,
                order_rng,
            )

            questions.append({
                "id": f"{family_id}_q{q_index + 1:02d}",
                "family_id": family_id,
                "active_branch_id": active_branch_id,
                "active_branch_name": active["name"],
                "question_id": question_id,
                "question_mode": question_mode,
                "question": question_text,
                "target_fields": fields,
                "expected_contains": expected,
                "forbidden_contains": unique_preserve(forbidden),
                "linear_branch_order": linear_branch_order,
                "active_branch_position": linear_branch_order.index(active_branch_id),
                "active_history_messages": active["history_messages"],
            })

    return {
        "meta": {
            "benchmark": "Minimal Sufficient Context Benchmark V2.1",
            "seed": seed,
            "families": len(families),
            "branches_per_family": 5,
            "questions": len(questions),
            "history_min": history_min,
            "history_max": history_max,
            "changes_from_v2": [
                "新增 linear_tagged，用同样的完整历史但明确标记当前活动线程。",
                "每道题随机 sibling 顺序，并让 active branch 在线性历史里的位置均衡分布。",
                "污染检测改用边界感知匹配，避免 49美元/月 误命中 149美元/月。",
            ],
        },
        "families": families,
        "questions": questions,
    }

def validate(data):
    assert len(data["families"]) == 10
    assert len(data["questions"]) == 100

    positions = []
    for case in data["questions"]:
        order = case["linear_branch_order"]
        assert len(order) == 5
        assert len(set(order)) == 5
        assert case["active_branch_id"] in order
        assert order[case["active_branch_position"]] == case["active_branch_id"]
        positions.append(case["active_branch_position"])

    # 100 道题应大致均衡落在 5 个位置。
    counts = {p: positions.count(p) for p in range(5)}
    assert max(counts.values()) - min(counts.values()) <= 1

    for family in data["families"].values():
        assert len(family["branches"]) == 5
        for branch in family["branches"].values():
            assert history_min_global <= len(branch["history"]) <= history_max_global

    return True

# validate() 需要知道当前生成范围；main / 模块导入时会赋值。
history_min_global = 30
history_max_global = 50

def main():
    global history_min_global, history_max_global

    parser = argparse.ArgumentParser(description="Generate Benchmark V2.1")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--history-min", type=int, default=30)
    parser.add_argument("--history-max", type=int, default=50)
    args = parser.parse_args()

    if args.history_min < 10 or args.history_max < args.history_min:
        raise SystemExit("history-min/history-max 参数不合法")

    history_min_global = args.history_min
    history_max_global = args.history_max

    data = generate(
        seed=args.seed,
        history_min=args.history_min,
        history_max=args.history_max,
    )
    validate(data)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    lengths = [
        b["history_messages"]
        for f in data["families"].values()
        for b in f["branches"].values()
    ]
    positions = [q["active_branch_position"] for q in data["questions"]]
    pos_counts = {p: positions.count(p) for p in range(5)}

    print(f"Generated : {output}")
    print(f"Families  : {len(data['families'])}")
    print(f"Branches  : {sum(len(f['branches']) for f in data['families'].values())}")
    print(f"Questions : {len(data['questions'])}")
    print(
        "History messages per branch: "
        f"min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)/len(lengths):.1f}"
    )
    print(f"Active branch positions: {pos_counts}")

if __name__ == "__main__":
    main()
