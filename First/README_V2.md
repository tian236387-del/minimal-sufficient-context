# Benchmark V2 — Long Context / Multi-Branch

第二版把第一版的小实验扩展成一个更接近真实长对话的 benchmark。

## 三种 Context 策略

- `linear`：shared history + 5 个相似 branch 的全部历史 + 当前问题
- `branch`：shared history + 当前 active branch 的全部历史 + 当前问题
- `branch_compact_oracle`：shared history + 当前 branch 的“无损最终事实摘要” + 最近 6 条消息 + 当前问题

`branch_compact_oracle` 是压缩上限实验。它直接使用 benchmark 已知的最终事实生成摘要，因此不是生产级 summarizer。

## 默认规模

- 10 个 project family
- 每个 family 5 个相似 branch
- 每个 branch 30～50 条历史消息
- 每个 family 10 道问题
- 总计 100 道问题
- 50 道 implicit 问题
- 50 道 explicit 问题

`implicit` 例如：`当前这个项目最终使用什么数据库，负责人是谁？`

`explicit` 例如：`Nova Mobile 当前最终采用什么后端框架和缓存？`

这样可以分别观察：当前分支本身的价值，以及即使目标项目已经明确，长 Context 是否仍然造成污染。

## 放到你的目录

从仓库根目录进入：

```text
First/
```

## 1. Dry run

不调用 Ollama，只看三种策略的 Context 大小：

```powershell
python run_benchmark_v2.py --dry-run
```

## 2. 先跑 10 道题

```powershell
python run_benchmark_v2.py --limit 10
```

这会进行 30 次模型调用。

## 3. 只比较 Linear vs Branch

```powershell
python run_benchmark_v2.py --conditions linear,branch
```

全部 100 道题时总共 200 次模型调用。

## 4. 跑全部三种条件

```powershell
python run_benchmark_v2.py
```

总共 300 次模型调用。

如果中途 Ctrl+C：

```powershell
python run_benchmark_v2.py --resume
```

每次模型调用后都会保存结果，因此可以断点续跑。

## 5. 重新生成 Benchmark

默认已经附带 `benchmark_v2.json`。

重新生成：

```powershell
python benchmark_generator_v2.py
```

改变 seed：

```powershell
python benchmark_generator_v2.py --seed 123
```

改变 branch 长度：

```powershell
python benchmark_generator_v2.py --history-min 40 --history-max 60
```

## 输出

```text
results_v2\
├── results_v2.csv
└── summary_v2.json
```

主要指标：

- Accuracy
- Pollution Rate
- Wrong without Pollution
- Avg / Median Prompt Tokens
- Avg Latency
- 相对 Linear 的 Prompt Token Reduction
- implicit / explicit 分组结果

建议顺序：先 `--dry-run`，再 `--limit 10`，确认无误后再跑完整 100 题。
