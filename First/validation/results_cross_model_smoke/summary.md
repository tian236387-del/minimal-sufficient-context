# cross-model-window-smoke

- Completed jobs: 32/32 (100.0%)
- Pending jobs: 0
- Successful runs: 32
- Errors: 0
- Paired comparisons: 16

| Model | Window | Condition | Runs | Accuracy | Pollution | Prompt tokens | Latency ms |
|---|---:|---|---:|---:|---:|---:|---:|
| gemma3:4b | 4096 | branch | 1 | 0.0% | 0.0% | 2051.0 | 2026.26 |
| gemma3:4b | 4096 | linear_tagged | 1 | 0.0% | 100.0% | 4088.0 | 55283.31 |
| gemma3:4b | 8192 | branch | 1 | 100.0% | 0.0% | 5809.0 | 1715.53 |
| gemma3:4b | 8192 | linear_tagged | 1 | 0.0% | 100.0% | 8178.0 | 42314.8 |
| gemma3:4b | 16384 | branch | 1 | 100.0% | 0.0% | 5809.0 | 1838.3 |
| gemma3:4b | 16384 | linear_tagged | 1 | 0.0% | 100.0% | 16371.0 | 32679.35 |
| gemma3:4b | 32768 | branch | 1 | 100.0% | 0.0% | 5809.0 | 1875.28 |
| gemma3:4b | 32768 | linear_tagged | 1 | 100.0% | 0.0% | 26121.0 | 10555.45 |
| llama3.1:8b | 4096 | branch | 1 | 0.0% | 0.0% | 4078.0 | 3692.0 |
| llama3.1:8b | 4096 | linear_tagged | 1 | 0.0% | 0.0% | 4090.0 | 60783.22 |
| llama3.1:8b | 8192 | branch | 1 | 100.0% | 0.0% | 6477.0 | 3467.46 |
| llama3.1:8b | 8192 | linear_tagged | 1 | 0.0% | 100.0% | 8184.0 | 56930.97 |
| llama3.1:8b | 16384 | branch | 1 | 100.0% | 0.0% | 6477.0 | 4837.0 |
| llama3.1:8b | 16384 | linear_tagged | 1 | 0.0% | 100.0% | 16372.0 | 51965.49 |
| llama3.1:8b | 32768 | branch | 1 | 100.0% | 0.0% | 6477.0 | 4115.36 |
| llama3.1:8b | 32768 | linear_tagged | 1 | 0.0% | 100.0% | 29123.0 | 40108.77 |
| llama3.2:3b | 4096 | branch | 1 | 0.0% | 0.0% | 4088.0 | 2982.43 |
| llama3.2:3b | 4096 | linear_tagged | 1 | 0.0% | 0.0% | 4079.0 | 57277.41 |
| llama3.2:3b | 8192 | branch | 1 | 100.0% | 0.0% | 6487.0 | 1921.57 |
| llama3.2:3b | 8192 | linear_tagged | 1 | 0.0% | 0.0% | 8169.0 | 53651.9 |
| llama3.2:3b | 16384 | branch | 1 | 100.0% | 0.0% | 6487.0 | 1950.48 |
| llama3.2:3b | 16384 | linear_tagged | 1 | 0.0% | 0.0% | 16382.0 | 45017.94 |
| llama3.2:3b | 32768 | branch | 1 | 100.0% | 0.0% | 6487.0 | 1429.46 |
| llama3.2:3b | 32768 | linear_tagged | 1 | 0.0% | 100.0% | 29133.0 | 15328.91 |
| qwen3:4b | 4096 | branch | 1 | 0.0% | 0.0% | 4093.0 | 4025.48 |
| qwen3:4b | 4096 | linear_tagged | 1 | 0.0% | 100.0% | 4094.0 | 56523.63 |
| qwen3:4b | 8192 | branch | 1 | 100.0% | 0.0% | 5552.0 | 3448.55 |
| qwen3:4b | 8192 | linear_tagged | 1 | 0.0% | 0.0% | 8182.0 | 52581.5 |
| qwen3:4b | 16384 | branch | 1 | 100.0% | 0.0% | 5552.0 | 3719.93 |
| qwen3:4b | 16384 | linear_tagged | 1 | 0.0% | 100.0% | 16368.0 | 40123.18 |
| qwen3:4b | 32768 | branch | 1 | 100.0% | 0.0% | 5552.0 | 8685.48 |
| qwen3:4b | 32768 | linear_tagged | 1 | 0.0% | 100.0% | 24939.0 | 42300.23 |

## Repeatability

{
  "groups": 0,
  "accuracy_agreement_rate": null,
  "pollution_agreement_rate": null,
  "exact_answer_agreement_rate": null,
  "avg_unique_answers": null
}

## Paired Branch vs Linear Tagged

{
  "pairs": 16,
  "avg_accuracy_delta": 0.6875,
  "avg_pollution_delta": -0.625,
  "avg_prompt_token_reduction_pct": 44.6819
}
