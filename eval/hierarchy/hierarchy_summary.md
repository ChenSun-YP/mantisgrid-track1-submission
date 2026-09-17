# Coherent-service hierarchy result

Measured on the 70-case development set with the unchanged official scorer, on
top of the process-restart version.

- Parent: 0.308 mean, 13/70 strict
- With coherent-service promotion: 0.317 mean, 14/70 strict
- Improved rows: 14, 20, and 30
- Regressed rows: 47 and 48
- Service-labelled component accuracy: 3/20
- Pod-labelled component accuracy: 6/10
- LLM calls: 0

The promotion gate passed because the strict solve count increased despite two
per-case regressions.
