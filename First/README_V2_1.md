# Minimal Sufficient Context Benchmark V2.1

V2.1 主要修复 V2 的两个实验问题。

## 变化 1：新增 `linear_tagged`

V2 的 implicit 问题存在一个 confound：

- Linear 看到了所有 branch
- 但不知道哪个 branch 是“当前线程”
- Branch 不仅更短，而且天然知道当前线程

所以 V2.1 新增：

```text
linear_tagged
```

它看到和 `linear` 完全一样的完整历史、完全一样的 branch 顺序，但在当前问题前额外得到：

```text
[当前活动线程：Nova Web]
```

于是最关键的比较变成：

```text
linear_tagged vs branch
```

两边都知道当前线程，主要区别只剩：

```text
是否包含其他 4 个无关 branch
```

这才更接近纯粹的 Context Pollution 实验。

---

## 变化 2：修复 Pollution substring bug

V2 中：

```text
49美元/月
```

会错误命中：

```text
149美元/月
```

所以明明正确的答案也可能被标记为污染。

V2.1 使用边界感知匹配，例如：

```text
49美元/月 != 149美元/月
0 != 0.2
2TB != 12TB
```

---

## 变化 3：随机化 Linear 中的 branch 顺序

V2 中 active branch 的位置容易和生成顺序绑定。

V2.1 每一道题都会：

- 随机排列另外 4 个 sibling
- 让 active branch 在 position 0～4 之间均衡出现

100 道题中：

```text
position 0: 20
position 1: 20
position 2: 20
position 3: 20
position 4: 20
```

其中：

```text
0 = 在线性历史中最早
4 = 在线性历史中最晚
```

这样可以观察模型是否存在明显的 recency bias。

---

# 四个实验条件

## 1. linear

```text
Shared
+ 5 个 Branch 全部历史
+ Question
```

不知道 active thread。

用途：

> 模拟普通单线聊天 UX。

## 2. linear_tagged

```text
Shared
+ 5 个 Branch 全部历史
+ [当前活动线程：X]
+ Question
```

用途：

> 在目标线程明确的情况下，测试其他无关 Context 是否仍然造成污染。

## 3. branch

```text
Shared
+ [当前活动线程：X]
+ Current Branch
+ Question
```

用途：

> 你的核心 Branch-aware Context 策略。

## 4. branch_compact_oracle

```text
Shared
+ [当前活动线程：X]
+ Oracle Summary
+ 最近 6 条消息
+ Question
```

用途：

> 测试 Context 压缩的理论上限。

注意：Oracle Summary 直接使用 benchmark 的已知最终事实，不是生产级 summarizer。

---

# 推荐运行顺序

从仓库根目录进入：

```text
First/
```

## 第一步

```powershell
python run_benchmark_v2_1.py --dry-run
```

确认看到：

```text
linear
linear_tagged
branch
branch_compact_oracle
```

以及每道题的：

```text
active=
position=
linear order=
```

---

## 第二步：还是先跑 10 道

```powershell
python run_benchmark_v2_1.py --limit 10
```

总共：

```text
10 × 4 = 40 次模型调用
```

重点比较：

```text
LINEAR_TAGGED
vs
BRANCH
```

而不是只看：

```text
LINEAR
vs
BRANCH
```

---

# 如何解释结果

### 情况 A

如果：

```text
Linear Tagged
Accuracy 80%
Pollution 20%

Branch
Accuracy 100%
Pollution 0%
```

说明：

> 即便模型明确知道当前线程，其他相似 Branch 仍然造成了可测量的 Context Pollution。

这会强支持 H3。

### 情况 B

如果：

```text
Linear Tagged
Accuracy 100%
Pollution 0%

Branch
Accuracy 100%
Pollution 0%
```

也不是失败。

它说明在当前约 4k context 和 Qwen3 1.7B 下：

> 清晰的 active-thread identifier 已足以避免准确率污染。

此时 Branch 已经确定的价值仍然有：

- 大幅降低 prompt token
- 更低延迟
- 更自然的 thread navigation

下一步就应该增加 context 长度到：

```text
8k / 16k / 32k
```

寻找污染开始出现的临界点。

---

# 输出

```text
results_v2_1\
├── results_v2_1.csv
└── summary_v2_1.json
```

Summary 还会额外输出：

```text
by_active_position
```

方便观察：

> active branch 越靠近线性历史末尾，Linear 是否越容易答对？
