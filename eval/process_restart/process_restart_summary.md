# Process-restart result

Measured on the 70-case development set with the unchanged official scorer.

- Parent Stage 1: 0.287 mean, 11/70 strict
- With process-restart detection: 0.308 mean, 13/70 strict
- Improved rows: 31 and 32
- Regressed rows: none
- Other changed row: 55
- Runtime: 1.59 seconds per case
- LLM calls: 0

The rule applies only to reason-only requests and requires an in-window change in
`container_start_time_seconds`.
