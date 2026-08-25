# MSC V0.2 外部验证快照

日期：2026-08-25（本机时区）

## 已完成的自动实验

| 实验 | 模型 | Seed / 重复 | 窗口 | 条件 | 调用数 | 状态 |
|---|---|---|---|---|---:|---|
| 本地重复控制 | `qwen3:1.7b` | 2 个数据 seed × 2 个推理 seed × 2 次重复 | 4k/8k/16k/32k | Linear/Branch | 64 | 完成 |
| 跨模型冒烟 | Qwen 4B、Llama 3.2 3B、Gemma 4B、Llama 3.1 8B | 1 × 1 × 1 | 4k/8k/16k/32k | Linear/Branch | 32 | 完成 |
| 跨模型重复 | 同上 4 个模型 | 2 个数据 seed × 2 个推理 seed × 2 次重复 | 4k/8k/16k/32k | Linear/Branch | 256 | 完成 |

三份结果分别位于：

- `results_repeated_local/`
- `results_cross_model_smoke/`
- `results_cross_model_repeated/`

## 完整性审计

- 结果行：`352`；唯一 `run_id`：`352`。
- 模型调用成功：`352/352`；错误：`0`。
- 所有结果行都有模型 digest；manifest 记录了模型、Ollama、Python、GPU、驱动和代码 SHA-256。
- 重复矩阵的配对比较：本地 `32` 对，跨模型 `128` 对。
- 重复一致性：跨模型矩阵的准确、污染和答案文本一致率均为 `100%`；这表示相同推理 seed 下可重复，不等于不同 seed 下没有变化。

硬件记录为 NVIDIA GeForce RTX 5060 Laptop GPU（8,151 MiB，驱动 582.05），Ollama `0.32.15`，Python `3.14.3`。模型均为本地 `Q4_K_M` 量化；延迟不能直接外推到其他硬件。

## 自动筛查结果

本地 `qwen3:1.7b` 的 32 对结果中：

- Branch：4k 准确率 `0/8`，8k/16k/32k 均为 `8/8`；污染率均为 `0/8`。
- Linear：四个窗口准确率均为 `0/8`，污染率均为 `8/8`。
- Branch 相对 Linear 的平均准确率差为 `+0.75`，污染率差为 `-1.0`，平均 prompt token 减少 `45.6713%`。

跨模型 128 对结果中：

- 四个模型的 Branch 在 8k 和 16k 均为 `8/8`；Gemma 4B、Llama 3.2 3B、Llama 3.1 8B 在 32k 为 `8/8`，Qwen 4B 为 `6/8`。
- 四个模型的 Branch 在 4k 均未达到正确筛查标准，说明 4k 是当前长历史设置的压力边界，不应被解释为产品在正常预算下的表现。
- 跨模型 Branch 相对 Linear 的平均准确率差为 `+0.6719`，污染率差为 `-0.6406`，平均 prompt token 减少 `44.95%`。

这些准确率和污染率来自自动关键词/兄弟分支词筛查，只能作为工程回归指标，不能替代完整答案质量评分。

## 真人实验状态

真人实验服务已启动于 `http://127.0.0.1:8765`，固定使用 `qwen3:4b`，任务包包含编程、研究、写作三个领域，每个参与者完成 6 个交叉平衡任务。当前真实数据为：

- 已开始会话：`0`
- 已完成会话：`0`
- 已提交任务：`0`
- 配对结果：`0`

上述 `0` 是真实状态，不用模型生成或人工补写。服务端会记录匿名会话、任务事件、模型草稿、答案、计时和评分；正式质量结论还需要两名不知道条件的人工评分者独立评分。建议先完成 `n=24` 可用性试点，再冻结协议并以 `n=48` 做第一轮对外验证。

## 尚未完成与限制

- `matrix_pilot.json` 的 11,520 条多题试点尚未运行；它是可恢复的长时计划，不把配置量当成已完成样本。
- 自动矩阵当前使用代表性题 `nova_q01`（`limit=1`），不能证明编程、研究、写作等真实工作流的整体收益。
- runner 按固定顺序先跑 Linear 再跑 Branch；因此延迟只作描述性指标，不能据此做因果速度结论。主要判断依据是配对准确率、污染和 token 使用量。
- 4k 结果接近上下文饱和；窗口溢出/截断行为是压力测试现象，需要单独报告。
- 真人实验完成前，不发布“产品有效”或“普遍提升”的确认性结论。

## 重跑命令

```powershell
python -B First/validation/run_matrix.py `
  --config First/validation/matrix_pilot.json `
  --results-dir First/validation/results_pilot `
  --resume

python -B First/validation/human_study/analyze_study.py
```
