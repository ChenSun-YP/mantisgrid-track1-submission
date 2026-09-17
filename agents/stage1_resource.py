"""Ablation: rolling candidates, local onset, and resource-reason scoring."""
from rca.resource_stage1 import solve as _solve


def solve(instruction, dataset_dir, ctx):
    return _solve(instruction, dataset_dir, ctx, "resource_reason")
