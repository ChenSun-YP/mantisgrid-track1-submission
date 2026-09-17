"""Optional one-call GLM adjudication over deterministic Stage 1 hypotheses."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agents import heuristic_contract as b2
from agents.heuristic import failure_count
from rca import resource_stage1 as deterministic
from run import Solution, format_prediction

PRIMARY_MODEL = "zai-org/GLM-5.2"
FALLBACK_MODEL = "zai-org/GLM-5.1"
SYSTEM_PROMPT = """You are the final RCA adjudicator.

Use only the supplied hypotheses and evidence.

Do not invent a component, reason, timestamp, KPI, or telemetry value.

Select the hypothesis or hypotheses that best explain the observed evidence.

You must choose only supplied hypothesis IDs.

Return JSON only:
{
  "selected_hypotheses": ["H1"],
  "decisive_evidence_ids": ["E1", "E4"],
  "explanation": "brief explanation"
}"""


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def build_hypotheses(candidates: list[deterministic.Candidate]) -> tuple[list[dict], list[dict]]:
    """Convert the existing ranked candidates into a bounded GLM payload."""
    hypotheses, evidence = [], []
    for index, candidate in enumerate(candidates[:5], 1):
        event = candidate.event
        prefix = f"H{index}"
        base = (index - 1) * 5
        evidence_ids = [f"E{base + offset}" for offset in range(1, 5)]
        peers = [peer.component for peer in candidates
                 if peer.level == "pod" and candidate.level == "service"
                 and deterministic.service_of(peer.component) == candidate.component
                 and peer.event.kpi == event.kpi and peer.event.direction == event.direction
                 and abs(peer.event.onset - event.onset) <= 120]
        hierarchy = {"coherent_peer_components": peers, "peer_count": len(peers)}
        if candidate.level == "service":
            evidence_ids.append(f"E{base + 5}")
        reason_scores = [{"reason": reason, "score": score}
                         for reason, score in candidate.reason_scores]
        contradictions = []
        if len(reason_scores) > 1 and reason_scores[0]["score"]:
            ratio = reason_scores[1]["score"] / reason_scores[0]["score"]
            if ratio >= _float_env("GLM_REASON_CLOSE_RATIO", 0.85):
                contradictions.append("close competing reason evidence")
        onset = datetime.fromtimestamp(event.onset, deterministic.b1.TZ8).strftime(
            "%Y-%m-%d %H:%M:%S")
        hypotheses.append({
            "hypothesis_id": prefix,
            "component": candidate.component,
            "hierarchy_level": candidate.level,
            "reason": candidate.resource_reason,
            "deterministic_onset": onset,
            "component_score": candidate.score,
            "reason_evidence_scores": reason_scores,
            "source_event": {"component": event.source_component, "kpi": event.kpi,
                             "direction": event.direction},
            "persistence": {"fraction": event.persistence,
                            "duration_samples": event.duration_samples},
            "hierarchy_evidence": hierarchy,
            "supporting_evidence_ids": evidence_ids,
            "contradictions": contradictions,
        })
        evidence.extend([
            {"evidence_id": evidence_ids[0], "kind": "component_score",
             "value": candidate.score},
            {"evidence_id": evidence_ids[1], "kind": "reason_scores",
             "value": reason_scores},
            {"evidence_id": evidence_ids[2], "kind": "source_event",
             "value": hypotheses[-1]["source_event"]},
            {"evidence_id": evidence_ids[3], "kind": "persistence",
             "value": hypotheses[-1]["persistence"]},
        ])
        if candidate.level == "service":
            evidence.append({"evidence_id": evidence_ids[-1], "kind": "hierarchy",
                             "value": hierarchy})
    return hypotheses, evidence


def _apply_deterministic_overrides(baseline: Solution, hypotheses: list[dict],
                                   evidence: list[dict]) -> None:
    """Carry process-restart decisions into selectable precomputed hypotheses."""
    if "Process restart override: yes" not in baseline.evidence:
        return
    try:
        body = baseline.prediction.removeprefix("```json\n").removesuffix("\n```")
        answers = json.loads(body).values()
    except (ValueError, json.JSONDecodeError):
        return
    by_component = {answer.get("root cause component"): answer for answer in answers}
    for hypothesis in hypotheses:
        answer = by_component.get(hypothesis["component"])
        if not answer:
            continue
        highest_id = max((int(item["evidence_id"][1:]) for item in evidence), default=0)
        evidence_id = f"E{highest_id + 1}"
        hypothesis["reason"] = answer.get("root cause reason", hypothesis["reason"])
        hypothesis["deterministic_onset"] = answer.get(
            "root cause occurrence datetime", hypothesis["deterministic_onset"])
        hypothesis["supporting_evidence_ids"].append(evidence_id)
        evidence.append({"evidence_id": evidence_id, "kind": "process_restart",
                         "value": {"kpi": "container_start_time_seconds",
                                   "change": "within query window"}})


def should_escalate(hypotheses: list[dict], required_count: int,
                    deterministic_fallback: bool = False) -> list[str]:
    """Return explicit ambiguity reasons; an empty list means stay deterministic."""
    reasons = []
    if deterministic_fallback:
        reasons.append("deterministic fallback was required")
    if len(hypotheses) >= 2 and hypotheses[0]["component_score"]:
        ratio = hypotheses[1]["component_score"] / hypotheses[0]["component_score"]
        if ratio >= _float_env("GLM_COMPONENT_CLOSE_RATIO", 0.85):
            reasons.append("top component scores are close")
        a, b = hypotheses[:2]
        same_fault = (a["component"] == deterministic.service_of(b["component"])
                      or b["component"] == deterministic.service_of(a["component"]))
        if same_fault and ratio >= _float_env("GLM_HIERARCHY_CLOSE_RATIO", 0.75):
            reasons.append("service and pod alternatives are close")
    if hypotheses and hypotheses[0]["contradictions"]:
        reasons.append("top reason alternatives are close")
    if required_count > 1 and len(hypotheses) > required_count:
        selected = hypotheses[required_count - 1]["component_score"]
        alternate = hypotheses[required_count]["component_score"]
        if selected and alternate / selected >= _float_env("GLM_ASSIGNMENT_CLOSE_RATIO", 0.85):
            reasons.append("multi-failure event assignment is ambiguous")
    if any(h["contradictions"] for h in hypotheses):
        reasons.append("deterministic evidence is internally conflicting")
    return list(dict.fromkeys(reasons))


def _request_json(url: str, headers: dict, payload: dict | None, timeout: float) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, headers=headers,
                      method="GET" if payload is None else "POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _choose_model(base_url: str, headers: dict, timeout: float) -> str:
    try:
        models = _request_json(f"{base_url}/models", headers, None, timeout)
        ids = {item.get("id") for item in models.get("data", [])}
        if PRIMARY_MODEL in ids:
            return PRIMARY_MODEL
        if FALLBACK_MODEL in ids:
            return FALLBACK_MODEL
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        pass
    return PRIMARY_MODEL


def call_glm(hypotheses: list[dict], evidence: list[dict], required_count: int) -> dict:
    """Make exactly one completion request, choosing the model before that request."""
    api_key = os.getenv("FEATHERLESS_API_KEY", "")
    if not api_key:
        raise RuntimeError("FEATHERLESS_API_KEY is not set")
    base_url = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1").rstrip("/")
    timeout = _float_env("GLM_TIMEOUT_SECONDS", 30.0)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    model = _choose_model(base_url, headers, timeout)
    payload = {"model": model, "temperature": 0, "max_tokens": 300,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps({
                                "required_hypothesis_count": required_count,
                                "hypotheses": hypotheses, "evidence": evidence})}]}
    response = _request_json(f"{base_url}/chat/completions", headers, payload, timeout)
    content = response["choices"][0]["message"]["content"]
    result = json.loads(content)
    usage = response.get("usage", {})
    return {"model": model, "result": result, "usage": {
        model: {"prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0), "calls": 1}}}


def _validated_selection(response: dict, hypotheses: list[dict], evidence: list[dict],
                         required_count: int, dataset: Path, instruction: str) -> tuple[list[dict], list[str]]:
    result = response["result"]
    selected = result.get("selected_hypotheses")
    decisive = result.get("decisive_evidence_ids")
    if not isinstance(selected, list) or len(selected) != required_count:
        raise ValueError("wrong selected hypothesis count")
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate hypothesis IDs")
    by_id = {h["hypothesis_id"]: h for h in hypotheses}
    if any(hid not in by_id for hid in selected):
        raise ValueError("unknown hypothesis ID")
    valid_evidence = {item["evidence_id"] for item in evidence}
    if not isinstance(decisive, list) or any(eid not in valid_evidence for eid in decisive):
        raise ValueError("unknown decisive evidence ID")
    answers = [{"datetime": by_id[hid]["deterministic_onset"],
                "component": by_id[hid]["component"], "reason": by_id[hid]["reason"]}
               for hid in selected]
    issues = b2._validate(answers, set(b2._registry(dataset, instruction)), required_count)
    if issues:
        raise ValueError("illegal answer structure: " + "; ".join(issues))
    return answers, decisive


def solve(instruction: str, dataset_dir: Path, ctx: dict) -> Solution:
    """Wrap the locked solver; every exceptional path returns its exact answer."""
    dataset = Path(dataset_dir)
    baseline = deterministic.solve(instruction, dataset, ctx, "resource_reason")
    if not os.getenv("FEATHERLESS_API_KEY"):
        return baseline
    candidates = deterministic.hierarchy_ordered(
        deterministic.generate_candidates(instruction, dataset))
    hypotheses, evidence = build_hypotheses(candidates)
    _apply_deterministic_overrides(baseline, hypotheses, evidence)
    required_count = failure_count(instruction)
    fallback_used = "## Stage 1 fallback" in baseline.evidence
    triggers = should_escalate(hypotheses, required_count, fallback_used)
    if not triggers:
        return baseline

    called, response, error = False, None, ""
    try:
        called = True
        response = call_glm(hypotheses, evidence, required_count)
        answers, decisive = _validated_selection(response, hypotheses, evidence,
                                                 required_count, dataset, instruction)
        prediction = format_prediction(answers)
    except Exception as exc:  # API and model output must never crash a case.
        prediction = baseline.prediction
        decisive = []
        error = f"{type(exc).__name__}: {exc}"

    if not called:
        return baseline
    result = response.get("result", {}) if response else {}
    selected = result.get("selected_hypotheses", []) if not error else []
    explanation = result.get("explanation", "") if not error else ""
    numeric_probe = str(explanation)
    for known_id in ([h["hypothesis_id"] for h in hypotheses]
                     + [item["evidence_id"] for item in evidence]):
        numeric_probe = numeric_probe.replace(known_id, "")
    if re.search(r"\d", numeric_probe):
        explanation = "omitted because it contained numeric text not copied from evidence"
    section = ["", "## GLM adjudication", "",
               f"- Why escalation triggered: {', '.join(triggers) or 'all-case evaluation mode'}",
               f"- Model used: {response['model'] if response else 'none'}",
               "- Hypotheses considered: " + ", ".join(h["hypothesis_id"] for h in hypotheses),
               "- Selected hypothesis IDs: " + (", ".join(selected) if selected else "none"),
               "- Decisive evidence IDs: " + (", ".join(decisive) if decisive else "none"),
               f"- Brief model explanation: {explanation or 'none'}"]
    if error:
        section.append(f"- Deterministic fallback retained: {error}")
    return Solution(prediction=prediction, evidence=baseline.evidence + "\n".join(section) + "\n",
                    usage=response.get("usage", {}) if response else {})
