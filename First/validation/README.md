# External Validation

本目录把 V2.2 基准扩展为可恢复的多模型、多 Seed、多次重复和 4k/8k/16k/32k 上下文扫描，并提供独立的真人交叉实验。

## 三层矩阵

| 配置 | 用途 | 调用数 |
|---|---|---:|
| `matrix_smoke.json` | Qwen/Llama/Gemma/8B 大模型跨窗口冒烟 | 32 |
| `matrix_repeated_local.json` | 两个数据 Seed、两个推理 Seed、两次重复的本机控制实验 | 64 |
| `matrix_pilot.json` | 四模型、三数据 Seed、三推理 Seed、两次重复、20 题试点 | 11,520 |

所有矩阵默认比较 `linear_tagged` 与 `branch`，并使用每分支 230–270 条长历史，使 4k 到 32k 窗口都能产生可观测压力。

先查看计划，不会请求模型：

```powershell
python -B First/validation/run_matrix.py --dry-run
python -B First/validation/run_matrix.py --config First/validation/matrix_repeated_local.json --dry-run
```

运行本机重复矩阵：

```powershell
python -B First/validation/run_matrix.py `
  --config First/validation/matrix_repeated_local.json `
  --results-dir First/validation/results_repeated_local
```

运行跨模型冒烟：

```powershell
ollama pull llama3.2:3b
ollama pull gemma3:4b
ollama pull llama3.1:8b
python -B First/validation/run_matrix.py `
  --config First/validation/matrix_smoke.json `
  --results-dir First/validation/results_cross_model_smoke
```

中断后使用完全相同的配置续跑：

```powershell
python -B First/validation/run_matrix.py `
  --config First/validation/matrix_smoke.json `
  --results-dir First/validation/results_cross_model_smoke `
  --resume
```

`--max-jobs N` 可做小批次检查，`--installed-only` 会跳过未安装模型，`--fail-fast` 在首次错误时停止。结果目录已存在时必须显式 `--resume`；续跑配置不一致会被拒绝，避免把不同实验混在同一 CSV。

## 输出

- `results.csv`：每次调用的 Seed、窗口、条件、准确率、串线、token、延迟和吞吐。
- `summary.json` / `summary.md`：按模型、窗口、条件聚合的 Wilson 区间及 Branch 对 Linear 配对差值。
- `manifest.json`：完整配置、配置指纹、Ollama 标签与 digest、GPU/驱动、Python/Ollama 版本、生成器和评估器 SHA-256。

失败任务可以重试；汇总只采用每个稳定 `run_id` 的最新一次尝试，旧失败仍保留在 CSV 作为审计记录。

## 统计边界

- 相同推理 Seed 的重复运行用于检查可重复性；不同推理 Seed 才提供随机变化。
- 当前 Wilson 区间把任务结果视作伯努利观察，完整报告还应按数据 Seed/题目做分层或混合效应分析。
- `limit=1` 的冒烟和本机矩阵验证执行链路与窗口趋势，不足以支持总体产品有效性结论。
- 11,520 次试点在 8GB 显存笔记本上可能持续数天，应该按模型分批续跑并保留 manifest。
- 8B 模型在 32k 窗口下可能部分落到 CPU；延迟是该硬件部署结果，不能直接当作模型固有速度。

真人任务、招募协议和分析说明位于 `human_study/README.md`。真实参与者数据必须由参与者完成，不能用模型生成或人工补写。

## 当前验证快照

- `matrix_cross_model_repeated.json`：四模型、两个数据 Seed、两个推理 Seed、两次重复、4k/8k/16k/32k，共 256 次调用。
- `EXTERNAL_VALIDATION_REPORT.md`：当前自动实验、完整性审计、真人实验状态和限制的快照。
- 当前自动矩阵共完成 352 次调用；完整 `matrix_pilot.json` 的 11,520 次多题试点仍需单独续跑，不能把配置量当成已完成样本。
