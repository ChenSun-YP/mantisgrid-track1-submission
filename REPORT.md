# MantisGrid Track 1 — Deterministic Resource RCA

## Problem

The agent must identify the occurrence time, component, and reason for one or more failures in a 30-minute microservice incident window. The output contract is unforgiving: a wrong failure count, illegal component or reason, malformed key order, or empty answer can zero an otherwise useful diagnosis. The goal is a fast, grounded answer with an auditable fallback, not an unconstrained narrative.

## Baselines and the UTC+8 failure

All numbers below are measured on the 70-case `Market-cloudbed-1` development set with the official scorer.

| Version | Mean official score | Strictly solved | Empty predictions |
|---|---:|---:|---:|
| B0 original | 0.073 | 2/70 | 24 |
| B2 contract-safe | 0.157 | 4/70 | 0 |

B0 parsed the query clock as UTC, while query timestamps and telemetry follow UTC+8. Epoch timestamps are absolute, so treating `16:00 UTC+8` as `16:00 UTC` shifted the requested window eight hours later. On the available day files, that systematically left late-day queries with no in-window samples: all 24 B0 semantic-empty predictions were attributed to the incorrect timezone window. Parsing query bounds as aware UTC+8 datetimes removed all 24 empty answers. B2 then added exact failure-count checks, legal registry-backed component and reason validation, required key order, and a deterministic best-guess fallback. B1 and B2 made identical predictions on this dataset, so B2's value is contract safety rather than a claimed score gain.

## Telemetry audit

The development bundle has 70 rows but only 57 unique windows; labels are incomplete (47 incidents have time, 48 component, 49 reason, and 21 all three). Results are therefore development-set measurements, not hidden-set or independent-test estimates.

The audit found three design constraints:

- Component granularity matters: the labelled incidents mix nodes (18/48), exact pods (10/48), and logical services (20/48). The candidate set must retain all three levels.
- Change-point retrieval was the strongest audited general candidate generator: exact component Recall@1/3/5/10 was 45.8%/72.9%/79.2%/91.7% on 48 component-labelled incidents. The final implementation's mixed Top-10 retrieval, measured under its own candidate contract, reached 85.4% (41/48); these differently defined figures are not interchangeable.
- The globally earliest anomaly is usually not the root: among 26 incidents where a root anomaly was detected, the root was globally earliest only 11.5% of the time. Local onset is useful for timing, while component selection needs persistence, hierarchy, and KPI-family evidence.

The audit also showed why network support was not claimed. For eight labelled network incidents, mesh and trace anomaly methods each reached 75.0% Recall@5, versus 37.5% for each tested container-metric method. Logs vary greatly in volume, so rates or normalized changes—not raw counts—would be required. The submitted method deliberately reads only container and node metrics.

## Final deterministic method

The submitted default is `agents.stage1_resource`. It is the measured Stage 1
resource method plus process-restart detection and coherent service hierarchy
promotion.

1. Parse the half-open 30-minute interval in UTC+8 and load eligible container and node resource metrics.
2. Detect persistent local changes with rolling windows of three and five samples. Score both increases and decreases against daily robust scale, with explicit handling for zero-MAD state changes.
3. Build a mixed Top-10 candidate set containing exact pods, logical services, and nodes. Promote a logical service when at least two replicas show the same KPI direction within 120 seconds; otherwise retain the exact pod ahead of a weak service aggregate.
4. Convert KPI families into soft resource-reason scores for CPU, memory, disk space, read I/O, and write I/O. These scores are evidence weights, not calibrated probabilities.
5. For reason-only requests, detect any in-window change in `container_start_time_seconds` and report container process termination for the selected answer.
6. Select distinct source components, use the first sustained local transition as occurrence time, validate the exact answer contract, and fall back to B2 if candidates are insufficient or invalid.

No labels, logs, traces, mesh telemetry, ReplicaDiff, external API, or LLM are used at inference.

## Final measured results

| Ablation | Mean score | Strictly solved |
|---|---:|---:|
| B2 | 0.157 | 4/70 |
| + rolling change-point | 0.190 | 3/70 |
| + local onset | 0.239 | 6/70 |
| + resource-reason scoring (Stage 1) | 0.287 | 11/70 |
| + process-restart detection | 0.308 | 13/70 |
| + coherent service hierarchy (final) | **0.317** | **14/70** |

Hierarchy promotion over the process-restart version improved rows 14, 20, and 30 and regressed rows 47 and 48. The final component accuracy was 3/20 on service-labelled incidents and 6/10 on pod-labelled incidents. All 70 outputs were non-empty and had the requested failure count.

The reproduced final run averaged 1.83 seconds per case, with a 7.93-second maximum. It made zero LLM calls and used zero prompt tokens. The measured model cost is therefore $0.

These are internal development results after exploratory audit, not untouched external validation. No thresholds were fitted to labels, but analysis and evaluation used the same development bundle.

## Evidence design

Each case writes the ranked mixed candidate universe with component level, deterministic score, source component, observed KPI, direction, persistence, local onset, and selected resource reason. It then records the exact emitted answer and states that scores are anomaly contrasts rather than probabilities. The evidence cites only telemetry actually read by the method. When Stage 1 cannot produce a complete valid answer, the evidence says that B2 was retained instead of fabricating support.

The runtime preserves explicit `Answer`, `Confidence`, `Evidence`, and `Ruled out`
sections for every case. A hierarchy-promoted service retains its source pod and
event in the ranked table. A process-restart answer identifies the computed
`container_start_time_seconds` change used by the deterministic override.

For example, development row 0 ranked `shippingservice-1` first, identified a sustained transition at `2022-03-20 09:09:00` UTC+8, and emitted `container read I/O load`. The official case score was 1.0. The evidence file preserves the competing service, pod, and node candidates so the selection can be inspected rather than accepted as a black box.

## Runtime and cost design

The deterministic agent is the whole default path, not merely an error branch. It is reproducible, made zero model calls in the measured run, and had measured model cost of $0. No LLM or model-routing configuration is enabled in the default runtime.

## Negative ablations

The candidate-source experiment kept rolling generation: fixed-half scored 0.175
and the union scored 0.281 while taking 3.37 seconds per case, so neither replaced
the rolling path. In the earlier hierarchy experiment, reason-conditioned onset
held the result at 0.295/12 while raising runtime to 3.15 seconds per case, and
distinct multi-event selection reduced mean score to 0.288 without adding a strict
solve. These negative results were not promoted.

## Optional GLM-routing design

An optional `agents.glm_escalation` module wraps, but does not modify, the
deterministic solver and always preserves its answer as the fallback. It constructs
at most five structured hypotheses from cached deterministic candidate data and
applies configurable score/reason/hierarchy/
multi-event ambiguity checks, and makes at most one adjudication completion. The
model may select only supplied hypothesis and evidence IDs; all answer fields come
from the selected precomputed hypothesis. Strict validation or any missing key,
API failure, timeout, malformed JSON, unknown ID, or wrong count returns the exact
deterministic prediction. The optional path is neither enabled by default nor
accuracy-evaluated on the final harness. The measured default therefore remains
deterministic at 0.317 mean and 14/70 strict with zero LLM calls.

## Limitations

- Network diagnosis remains weak: the resource-only method scored 0.000 mean and 0/10 strict on the diagnostic network group.
- `task_7` scored 0.105 mean and remained unsolved strictly (0/11); multi-failure selection is based on ranked distinct sources rather than joint causal reasoning.
- Candidate selection remains imperfect, including the two hierarchy regressions on rows 47 and 48.
- Timing remains weak: only 8/47 timed incidents were within 60 seconds.
- Soft KPI-family mappings can be confounded by correlated victim signals; scores are not calibrated confidence.
- These are internal development-set results after exploratory audit, not hidden-set performance. Development labels are incomplete and repeated windows are not independent; hidden-set generalization is unverified.
- No final GLM-routing accuracy evaluation was run, so its generalization and cost-effectiveness are unknown.
- Container execution not locally verified because no runtime is installed.

## AI-use disclosure

OpenAI Codex/ChatGPT assisted with analysis, coding, testing, evaluation tooling, and documentation. The measured default runtime does not call an AI model. All reported numbers come from checked repository artifacts produced by the official scorer or deterministic audit code; no result was generated or estimated by the model.

## Reproduction pointers

- Stage 0: `eval/stage0/stage0_acceptance_report.md`
- Stage 1: `eval/stage1/stage1_acceptance_report.md`
- Candidate-source ablation: `eval/candidate_ablation/candidate_ablation_report.md`
- Process restart: `eval/process_restart/process_restart_summary.md`
- Hierarchy promotion: `eval/hierarchy/hierarchy_summary.md`
- Final release: `eval/final/final_release_summary.md`
