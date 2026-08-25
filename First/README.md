# Minimal Sufficient Context — First

这是一个最小实验，用来比较：

- **Linear Context**：把同一会话里的所有历史都塞给模型
- **Branch Context**：只给模型“共同上下文 + 当前分支”

目标不是做完整产品，而是先验证：

1. Branch Context 是否减少无关上下文
2. 是否降低跨分支信息污染
3. 是否减少 prompt token
4. 是否保持或提高回答正确率

## 1. 环境要求

- Windows 11
- Python 3.10+
- Ollama
- 一个本地 instruct 模型

推荐先用较小模型：

```powershell
ollama pull qwen3:1.7b
```

如果你的机器跑得很轻松，也可以改成：

```powershell
ollama pull qwen3:4b
```

## 2. 运行

在 PowerShell 里：

```powershell
cd First
python run_experiment.py
```

默认模型是：

```text
qwen3:1.7b
```

也可以指定：

```powershell
python run_experiment.py --model qwen3:4b
```

多跑几次：

```powershell
python run_experiment.py --repeats 3
```

## 3. 输出

运行结束后会在：

```text
results\
```

生成：

- `results.csv`
- `summary.json`

重点看这些指标：

- `prompt_tokens`
- `correct`
- `polluted`
- `latency_ms`

其中：

```text
Pollution Rate = polluted answers / total answers
```

## 4. 当前实验逻辑

每一个测试样本都有：

```text
shared_context
branch_a
branch_b
active_branch
question
expected_contains
forbidden_contains
```

### Linear

如果 A 是当前分支，Linear 仍然会看到：

```text
shared + A + B + question
```

也就是说，它会带着另一个分支的信息回答。

### Branch

Branch 模式只看到：

```text
shared + A + question
```

B 不进入 context。

## 5. 第一版的限制

这一版故意非常简单：

- 没有 UI
- 没有数据库
- 没有 RAG
- 没有 embedding
- 没有 summary
- 没有 LangChain

因为第一步只验证一个假设：

> 在同一个模型、同一个问题、同一个 system prompt 下，
> Branch-aware Context 是否比单线历史更少受到其他话题污染。

如果这个实验能稳定观察到差异，再做第二版 UI 和 Conversation DAG。
