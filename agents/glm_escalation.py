"""Optional ambiguity-routed GLM adjudication (never the default agent)."""

from rca.glm_escalation import solve as _solve


def solve(instruction, dataset_dir, ctx):
    return _solve(instruction, dataset_dir, ctx)
