# local-multiseed-repeated-window-scan

- Completed jobs: 64/64 (100.0%)
- Pending jobs: 0
- Successful runs: 64
- Errors: 0
- Paired comparisons: 32

| Model | Window | Condition | Runs | Accuracy | Pollution | Prompt tokens | Latency ms |
|---|---:|---|---:|---:|---:|---:|---:|
| qwen3:1.7b | 4096 | branch | 8 | 0.0% | 0.0% | 4085.0 | 1681.505 |
| qwen3:1.7b | 4096 | linear_tagged | 8 | 0.0% | 100.0% | 4083.5 | 57226.3875 |
| qwen3:1.7b | 8192 | branch | 8 | 100.0% | 0.0% | 5271.5 | 1055.2825 |
| qwen3:1.7b | 8192 | linear_tagged | 8 | 0.0% | 100.0% | 8185.5 | 54675.6188 |
| qwen3:1.7b | 16384 | branch | 8 | 100.0% | 0.0% | 5271.5 | 1116.8163 |
| qwen3:1.7b | 16384 | linear_tagged | 8 | 0.0% | 100.0% | 16376.0 | 39386.465 |
| qwen3:1.7b | 32768 | branch | 8 | 100.0% | 0.0% | 5271.5 | 1272.6825 |
| qwen3:1.7b | 32768 | linear_tagged | 8 | 0.0% | 100.0% | 25522.5 | 9482.9487 |

## Repeatability

{
  "groups": 32,
  "accuracy_agreement_rate": 1.0,
  "pollution_agreement_rate": 1.0,
  "exact_answer_agreement_rate": 0.9375,
  "avg_unique_answers": 1.0625
}

## Paired Branch vs Linear Tagged

{
  "pairs": 32,
  "avg_accuracy_delta": 0.75,
  "avg_pollution_delta": -1.0,
  "avg_prompt_token_reduction_pct": 45.6713
}
