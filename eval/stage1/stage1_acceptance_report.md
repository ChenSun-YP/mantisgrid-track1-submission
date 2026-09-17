# Stage 1 acceptance report

## Decision

Stage 1 is complete. The deterministic resource branch is retained because it adds
required resource-candidate/onset functionality and improves measured official mean
score from 0.157 to 0.287.
It is not uniformly better: 21 cases improve,
10 regress, and 39 are unchanged.
All regressions remain listed in `per_case_diff.csv`.

## Official scorer ablation

| version | mean score | strict solved |
|---|---:|---:|
| B2 | 0.157 | 4/70 |
| B2 + rolling change-point | 0.190 | 3/70 |
| B2 + rolling change-point + onset | 0.239 | 6/70 |
| B2 + rolling change-point + onset + resource reason scoring | 0.287 | 11/70 |

## Candidate retrieval and local timing

- Exact mixed-universe component Recall@1: 29.2% (14/48).
- Exact mixed-universe component Recall@3: 52.1% (25/48).
- Exact mixed-universe component Recall@5: 64.6% (31/48).
- Exact mixed-universe component Recall@10: 85.4% (41/48).
- Local-onset accuracy within 60 seconds on merged timed incidents: 17.0% (8/47).
- B2 has 8/47 on the same timing diagnostic: local onset recovers the peak-time ablation loss but does not improve the aggregate timing count over B2.
- Candidate ranking is label-independent and retains exact pod, logical service, and node names.
- Network results are diagnostic only; this stage reads no mesh, trace, or log data.

### Exact component recall by fault group

| group | n | R@1 | R@3 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|
| container_non_network | 15 | 40.0% | 60.0% | 73.3% | 100.0% |
| node | 18 | 38.9% | 61.1% | 72.2% | 94.4% |
| network | 8 | 0.0% | 25.0% | 37.5% | 50.0% |

### Local-onset time <=60 seconds by fault group

| group | n | correct | accuracy |
|---|---:|---:|---:|
| container_non_network | 13 | 3 | 23.1% |
| node | 15 | 3 | 20.0% |
| network | 8 | 0 | 0.0% |

### Final reason accuracy by reason (49 merged labelled incidents)

| reason | n | correct | accuracy |
|---|---:|---:|---:|
| container CPU load | 7 | 4 | 57.1% |
| container memory load | 5 | 1 | 20.0% |
| container network latency | 3 | 0 | 0.0% |
| container network packet corruption | 3 | 0 | 0.0% |
| container network packet retransmission | 5 | 0 | 0.0% |
| container packet loss | 1 | 0 | 0.0% |
| container process termination | 3 | 0 | 0.0% |
| container read I/O load | 5 | 4 | 80.0% |
| container write I/O load | 1 | 0 | 0.0% |
| node CPU load | 3 | 3 | 100.0% |
| node CPU spike | 1 | 0 | 0.0% |
| node disk read I/O consumption | 3 | 2 | 66.7% |
| node disk space consumption | 4 | 0 | 0.0% |
| node disk write I/O consumption | 2 | 0 | 0.0% |
| node memory consumption | 3 | 2 | 66.7% |

## Final strict score by task

| task | n | strict | rate |
|---|---:|---:|---:|
| task_1 | 12 | 2 | 16.7% |
| task_2 | 10 | 1 | 10.0% |
| task_3 | 8 | 1 | 12.5% |
| task_4 | 7 | 2 | 28.6% |
| task_5 | 10 | 1 | 10.0% |
| task_6 | 12 | 4 | 33.3% |
| task_7 | 11 | 0 | 0.0% |

## Final results by diagnostic fault group

| group | n query rows | mean score | strict |
|---|---:|---:|---:|
| container_non_network | 15 | 0.455 | 4 |
| mixed | 12 | 0.271 | 0 |
| network | 10 | 0.000 | 0 |
| node | 20 | 0.375 | 5 |
| unknown | 13 | 0.192 | 2 |

## Improvements and regressions

- Improved row IDs: `[0, 6, 7, 9, 10, 11, 15, 17, 19, 21, 24, 36, 40, 42, 43, 44, 48, 51, 60, 63, 66]`.
- Regressed row IDs: `[8, 13, 14, 20, 22, 27, 28, 34, 38, 52]`.
- Change-point vs B2: {'improved': 16, 'regressed': 10, 'unchanged': 44}.
- Local onset vs change-point peak time: {'improved': 8, 'regressed': 4, 'unchanged': 58}.
- Resource reason scoring vs onset: {'improved': 9, 'regressed': 2, 'unchanged': 59}.
- `per_case_diff.csv` identifies whether each change came from time, component, or reason and classifies remaining candidate/selection/reason/time failures.
- Results are internal validation after exploratory audit, not untouched external validation. No thresholds were fitted to labels.

## Scope guard

No LLM/API, mesh, trace, logs, network detector, ReplicaDiff, learned model, or multi-agent logic is part of the protected Stage 1 runtime.

A cold one-case run used 1.065 GB peak RSS and 7.7 seconds on this Mac; the implementation uses one Python process, no worker pool, and no GPU.
