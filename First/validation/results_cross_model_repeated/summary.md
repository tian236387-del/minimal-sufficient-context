# cross-model-multiseed-repeated-window-scan

- Completed jobs: 256/256 (100.0%)
- Pending jobs: 0
- Successful runs: 256
- Errors: 0
- Paired comparisons: 128

| Model | Window | Condition | Runs | Accuracy | Pollution | Prompt tokens | Latency ms |
|---|---:|---|---:|---:|---:|---:|---:|
| gemma3:4b | 4096 | branch | 8 | 0.0% | 0.0% | 3073.0 | 1891.5988 |
| gemma3:4b | 4096 | linear_tagged | 8 | 0.0% | 100.0% | 4087.0 | 45939.8025 |
| gemma3:4b | 8192 | branch | 8 | 100.0% | 0.0% | 5510.5 | 1644.0563 |
| gemma3:4b | 8192 | linear_tagged | 8 | 0.0% | 50.0% | 8183.0 | 44126.9925 |
| gemma3:4b | 16384 | branch | 8 | 100.0% | 0.0% | 5510.5 | 1721.1375 |
| gemma3:4b | 16384 | linear_tagged | 8 | 0.0% | 100.0% | 16370.5 | 34981.24 |
| gemma3:4b | 32768 | branch | 8 | 100.0% | 0.0% | 5510.5 | 1796.545 |
| gemma3:4b | 32768 | linear_tagged | 8 | 100.0% | 0.0% | 26726.5 | 10736.5637 |
| llama3.1:8b | 4096 | branch | 8 | 0.0% | 0.0% | 4083.0 | 3393.7587 |
| llama3.1:8b | 4096 | linear_tagged | 8 | 0.0% | 0.0% | 4089.0 | 62825.61 |
| llama3.1:8b | 8192 | branch | 8 | 100.0% | 0.0% | 6146.5 | 3305.135 |
| llama3.1:8b | 8192 | linear_tagged | 8 | 0.0% | 100.0% | 8187.0 | 60231.1913 |
| llama3.1:8b | 16384 | branch | 8 | 100.0% | 0.0% | 6146.5 | 4597.555 |
| llama3.1:8b | 16384 | linear_tagged | 8 | 0.0% | 100.0% | 16369.0 | 55422.8037 |
| llama3.1:8b | 32768 | branch | 8 | 100.0% | 0.0% | 6146.5 | 3874.225 |
| llama3.1:8b | 32768 | linear_tagged | 8 | 0.0% | 100.0% | 29807.5 | 40518.345 |
| llama3.2:3b | 4096 | branch | 8 | 0.0% | 50.0% | 4079.5 | 2536.7888 |
| llama3.2:3b | 4096 | linear_tagged | 8 | 0.0% | 0.0% | 4079.5 | 59546.7038 |
| llama3.2:3b | 8192 | branch | 8 | 100.0% | 0.0% | 6156.5 | 1852.54 |
| llama3.2:3b | 8192 | linear_tagged | 8 | 0.0% | 75.0% | 8171.5 | 57463.8962 |
| llama3.2:3b | 16384 | branch | 8 | 100.0% | 0.0% | 6156.5 | 1874.2425 |
| llama3.2:3b | 16384 | linear_tagged | 8 | 0.0% | 50.0% | 16379.0 | 47810.9662 |
| llama3.2:3b | 32768 | branch | 8 | 100.0% | 0.0% | 6156.5 | 1494.7812 |
| llama3.2:3b | 32768 | linear_tagged | 8 | 0.0% | 100.0% | 29817.5 | 15910.265 |
| qwen3:4b | 4096 | branch | 8 | 0.0% | 0.0% | 4088.0 | 3696.2263 |
| qwen3:4b | 4096 | linear_tagged | 8 | 0.0% | 50.0% | 4088.5 | 57662.77 |
| qwen3:4b | 8192 | branch | 8 | 100.0% | 0.0% | 5265.5 | 3314.7487 |
| qwen3:4b | 8192 | linear_tagged | 8 | 0.0% | 50.0% | 8179.5 | 54652.1375 |
| qwen3:4b | 16384 | branch | 8 | 100.0% | 0.0% | 5265.5 | 3510.165 |
| qwen3:4b | 16384 | linear_tagged | 8 | 0.0% | 100.0% | 16370.0 | 42517.6687 |
| qwen3:4b | 32768 | branch | 8 | 75.0% | 0.0% | 5265.5 | 8677.9312 |
| qwen3:4b | 32768 | linear_tagged | 8 | 0.0% | 100.0% | 25516.5 | 41890.7312 |

## Repeatability

{
  "groups": 128,
  "accuracy_agreement_rate": 1.0,
  "pollution_agreement_rate": 1.0,
  "exact_answer_agreement_rate": 1.0,
  "avg_unique_answers": 1.0
}

## Paired Branch vs Linear Tagged

{
  "pairs": 128,
  "avg_accuracy_delta": 0.6719,
  "avg_pollution_delta": -0.6406,
  "avg_prompt_token_reduction_pct": 44.95
}
