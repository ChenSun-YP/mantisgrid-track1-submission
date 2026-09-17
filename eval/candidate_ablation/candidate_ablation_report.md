# Candidate-source ablation

| source | mean | strict /70 | R@1 | R@5 | R@10 | improved | regressed | mean seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| rolling | 0.287 | 11/70 | 29.2% | 64.6% | 85.4% | 0 | 0 | 1.61 |
| fixed_half | 0.175 | 4/70 | 10.4% | 39.6% | 60.4% | 13 | 19 | 1.98 |
| union | 0.281 | 11/70 | 29.2% | 58.3% | 75.0% | 5 | 5 | 3.37 |

- fixed_half improved row IDs: `[14, 22, 25, 27, 34, 39, 46, 47, 54, 56, 58, 59, 61]`.
- fixed_half regressed row IDs: `[0, 6, 7, 9, 10, 19, 20, 21, 24, 26, 31, 44, 45, 48, 51, 60, 62, 63, 66]`.

- union improved row IDs: `[14, 22, 27, 34, 47]`.
- union regressed row IDs: `[9, 10, 23, 44, 51]`.

Decision: keep the protected rolling Stage 1 baseline. Fixed-half is worse on mean and strict score. Union does not beat 0.287 / 11 and approximately doubles runtime. Neither ablation is promoted.
