# Minimal Sufficient Context Benchmark V2.2

V2.2 保留 V2.1 的 100 道题和四个实验条件，只修两个关键变量。

## 修复 1：Strong Active-Thread Grounding

V2.1 只有：

```text
[当前活动线程：Nova Web]
```

V2.2 对 `linear_tagged`、`branch`、`branch_compact_oracle` 使用相同、更明确的 grounding：

```text
[当前活动线程：Nova Web]
当前问题中的“这个项目”“当前项目”“当前产品”“当前版本”“这个版本”等指代，一律指 Nova Web。
只使用 Nova Web 的事实回答当前问题，不要使用其他项目的事实。
```

因此重点比较仍然是：

```text
linear_tagged vs branch
```

两者都知道目标线程，主要差别是前者还保留另外 4 个相似 sibling branch。

## 修复 2：Evaluator canonicalization

以下常见表达现在视为等价：

```text
149美元/月
$149/月
USD149/month
149美元每月
```

同时继续避免子串误判：

```text
49美元/月 != 149美元/月
0 != 0.2
2TB != 12TB
```

程序启动时会先跑 evaluator 自测，正常时输出：

```text
Evaluator self-test: PASS
```

## 使用

从仓库根目录进入：

```text
First/
```

先检查：

```powershell
python run_benchmark_v2_2.py --dry-run
```

然后跑前 10 道：

```powershell
python run_benchmark_v2_2.py --limit 10
```

共 40 次 Ollama 调用。

## 四个条件

- `linear`：完整 5 分支历史，没有 active-thread grounding。
- `linear_tagged`：完整 5 分支历史 + 强 active-thread grounding。
- `branch`：只保留 shared + 当前 branch + 同样的 grounding。
- `branch_compact_oracle`：当前 branch 的 oracle summary + 最近消息 + 同样的 grounding。

## 重点解释

如果 `linear_tagged` 仍显著差于 `branch`，就更有力地支持“无关相似 Context 本身会造成污染”。

如果两者准确率都达到 100%、污染都为 0%，则说明在当前约 4k token 范围内，强 thread grounding 足以解决准确率问题；Branch 的确定优势仍然是 token、延迟和可导航性。
