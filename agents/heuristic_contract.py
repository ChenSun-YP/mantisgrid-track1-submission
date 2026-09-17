"""B2: contract-safe wrapper around the B1 timezone-only heuristic."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re

import pandas as pd

from agents import heuristic_timezone as b1
from agents.heuristic import NODE_REASONS, POD_REASONS, failure_count
from run import Solution, format_prediction

_REGISTRY_CACHE: dict[tuple[str, tuple[str, ...]], list[str]] = {}


def _derived_services(pods: set[str]) -> set[str]:
    return {m.group("service") for x in pods
            if not x.startswith("node-")
            if (m := re.fullmatch(r"(?P<service>.+)-\d+", x))}


def _registry(dataset: Path, instruction: str) -> list[str]:
    win = b1.parse_window(instruction)
    if win is None:
        return []
    lo, hi = win
    dates = (lo.strftime("%Y_%m_%d"),)
    if (hi - timedelta(microseconds=1)).date() != lo.date():
        dates += ((hi - timedelta(microseconds=1)).strftime("%Y_%m_%d"),)
    key = (str(dataset.resolve()), dates)
    if key in _REGISTRY_CACHE:
        return _REGISTRY_CACHE[key]
    day = b1._load_interval(dataset, *win)
    exact = sorted({b1.component_of(str(x)) for x in day.cmdb_id.dropna()})
    # A logical service is derived only from an observed container entity with
    # the benchmark's explicit <service>-<numeric replica> form.  Node names or
    # arbitrary hyphenated names are never shortened.
    pods = {b1.component_of(str(x)) for x in day.loc[
        day.source.eq("metric_container"), "cmdb_id"].dropna()}
    services = sorted(_derived_services(pods))
    result = sorted(set(exact) | set(services))
    _REGISTRY_CACHE[key] = result
    return result


def _fallback_answers(instruction: str, dataset: Path, n: int) -> list[dict]:
    win = b1.parse_window(instruction)
    components = _registry(dataset, instruction)
    preferred = [x for x in components if not x.startswith("node-")] or components
    if not preferred:
        raise RuntimeError("input registry cannot be discovered")
    when = win[0] if win else datetime(1970, 1, 1)
    answers = []
    for i in range(n):
        component = preferred[i % len(preferred)]
        reason = ("node CPU load" if component.startswith("node-") else "container CPU load")
        answers.append({"datetime": when.strftime("%Y-%m-%d %H:%M:%S"),
                        "component": component, "reason": reason})
    return answers


def _validate(answers: list[dict], legal_components: set[str], n: int) -> list[str]:
    issues = []
    if len(answers) != n:
        issues.append(f"answer_count={len(answers)} expected={n}")
    for i, answer in enumerate(answers):
        component, reason = answer.get("component"), answer.get("reason")
        if component not in legal_components:
            issues.append(f"answer_{i + 1}:illegal_component")
        legal_reasons = NODE_REASONS.values() if str(component).startswith("node-") else POD_REASONS.values()
        if reason not in legal_reasons:
            issues.append(f"answer_{i + 1}:illegal_reason")
        try:
            datetime.strptime(str(answer.get("datetime")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            issues.append(f"answer_{i + 1}:invalid_datetime")
    return issues


def solve(instruction: str, dataset_dir: Path, ctx: dict) -> Solution:
    dataset = Path(dataset_dir); n = failure_count(instruction)
    analysis = b1.analyse(instruction, dataset)
    fallback_used = False
    if isinstance(analysis, b1.Analysis) and len(analysis.ranked) >= n:
        answers = [b1.answer_for(analysis, c) for c in analysis.ranked.head(n).index]
        evidence = b1.solution_from_analysis(analysis).evidence
    else:
        answers = _fallback_answers(instruction, dataset, n)
        fallback_used = True
        reason = analysis.evidence.strip() if isinstance(analysis, Solution) else "Insufficient ranked candidates."
        evidence = ("## Answer\n\nContract-safe best guess.\n\n## Confidence\n\nLow.\n\n"
                    "## Evidence\n\nNo positive diagnosis is claimed. The metric heuristic could not "
                    f"produce a ranked answer: {reason}\n\n## Ruled out\n\nNothing was ruled out.\n")
    legal = set(_registry(dataset, instruction))
    issues = _validate(answers, legal, n)
    if issues:
        answers = _fallback_answers(instruction, dataset, n)
        fallback_used = True
        issues = _validate(answers, legal, n)
    if issues:
        raise RuntimeError("contract-safe fallback invalid: " + "; ".join(issues))
    evidence += ("\n## Contract validation\n\n"
                 f"- Required failures: {n}\n- Emitted failures: {len(answers)}\n"
                 f"- Legal registry-backed names: yes\n- Fallback guess used: {fallback_used}\n")
    return Solution(prediction=format_prediction(answers), evidence=evidence)
