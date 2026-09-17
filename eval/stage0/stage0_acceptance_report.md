# Stage 0 acceptance report

## Result

The original 70-row heuristic result was reproduced exactly: mean official score
0.073, strict 2/70,
and 24 semantic-empty predictions. B1 changes only the timebase;
B2 adds contract validation and a truthful registry-backed fallback. No LLM was called.

| version | mean official score | strict | empty | contract-invalid |
|---|---:|---:|---:|---:|
| B0_original | 0.073 | 2/70 | 24 | 24 |
| B1_timezone_only | 0.157 | 4/70 | 0 | 0 |
| B2_contract_safe | 0.157 | 4/70 | 0 | 0 |

## What changed

- B0 to B1 changed 68/70 predictions. All 24 original blanks are classified from their own evidence; empty-primary causes: {'incorrect_timezone_window': 24}.
- B1 to B2 changed 0/70 predictions in this dataset. B1 already happened to satisfy the contract, so B2 is a safety boundary rather than an algorithmic score gain.
- Telemetry seconds were verified against aware UTC+8 query bounds. The representative raw counts and exact bounds are in `timezone_regression.csv`. B1 remains metric-only, so it does not ingest or reinterpret log seconds or trace milliseconds.
- Logical service names are derived only from observed container IDs matching `<service>-<numeric replica>`; nodes and arbitrary hyphenated names are not shortened.

## Reproducibility and discrepancies

- The live scorer matched the frozen original scorer during the measured audit.
- B0 fresh prediction strings matched the supplied baseline for all 70 row IDs; only measured wall times differed.
- B2 builds the input-name registry once per telemetry date and caches it; no score or prediction was repaired after inference.
- Remaining score errors are diagnosis errors, not empty-output or contract failures. No new detector or classifier was added.

The compact submission retains the measured score summary, contract validation,
and timezone regression tables alongside this report.
