"""Stage 1 deterministic resource candidates and local-onset estimation.

Only container and node metrics are read.  No labels, logs, traces, mesh data,
models, or APIs are used by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

import numpy as np
import pandas as pd

from agents import heuristic_contract as b2
from agents import heuristic_timezone as b1
from agents.heuristic import failure_count, reason_for
from run import Solution, format_prediction

HALF_WIDTHS = (3, 5)
MIN_PERSISTENT = 2
MIN_PERSISTENCE_FRACTION = 0.60
RESOURCE_REASONS = (
    "container CPU load", "container memory load", "container read I/O load",
    "container write I/O load", "node CPU load", "node CPU spike",
    "node memory consumption", "node disk read I/O consumption",
    "node disk write I/O consumption", "node disk space consumption",
)


@dataclass(frozen=True)
class Event:
    source_component: str
    kpi: str
    reason_weights: tuple[tuple[str, float], ...]
    score: float
    onset: int
    peak: int
    direction: str
    persistence: float
    duration_samples: int
    delta: float
    scale: float
    zero_mad_state_change: bool


@dataclass(frozen=True)
class Candidate:
    component: str
    level: str
    score: float
    event: Event
    resource_reason: str
    reason_scores: tuple[tuple[str, float], ...]


@dataclass
class DayData:
    frame: pd.DataFrame
    stats: pd.DataFrame
    replicas: dict[str, tuple[str, ...]]


_DAY_CACHE: dict[tuple[str, str], DayData] = {}


def service_of(component: str) -> str | None:
    if component.startswith("node-"):
        return None
    match = re.fullmatch(r"(?P<service>.+)-\d+", component)
    return match.group("service") if match else None


def reason_only_request(instruction: str) -> bool:
    """Whether the final request asks for reasons without components or times."""
    sentences = [part.strip().lower() for part in re.split(r"[.!?]+", instruction)
                 if part.strip()]
    request = sentences[-1] if sentences else ""
    return ("reason" in request and "component" not in request
            and "occurrence" not in request and "time" not in request)


def process_restart_detected(instruction: str, dataset: Path) -> bool:
    """Detect a container start-time reset inside a reason-only query window."""
    if not reason_only_request(instruction):
        return False
    window = b1.parse_window(instruction)
    if window is None:
        return False
    lo, hi = window
    day = b1._load_interval(dataset, lo, hi)
    state = day[day.kpi_name.eq("container_start_time_seconds")]
    state = state[(state.timestamp >= lo.timestamp()) & (state.timestamp < hi.timestamp())]
    return any(group.sort_values("timestamp").value.nunique() > 1
               for _, group in state.groupby("cmdb_id"))


def kpi_reason_weights(kpi: str, component: str) -> tuple[tuple[str, float], ...]:
    """Map observed KPI semantics to soft resource-reason evidence."""
    k = kpi.lower()
    node = component.startswith("node-")
    if node:
        if k.startswith("system.cpu.") or k.startswith("system.load."):
            return (("node CPU load", 1.0), ("node CPU spike", 0.8))
        if k.startswith("system.mem.") or k.startswith("system.swap."):
            if k.endswith(".total"):
                return ()
            return (("node memory consumption", 1.0),)
        if k in {"system.disk.used", "system.disk.pct_usage", "system.disk.free",
                 "system.fs.inodes.free", "system.fs.inodes.used",
                 "system.fs.inodes.in_use"}:
            return (("node disk space consumption", 1.0),)
        if re.search(r"system\.io\.(?:r_s|rkb_s|r_await)$", k):
            return (("node disk read I/O consumption", 1.0),)
        if re.search(r"system\.io\.(?:w_s|w_await)$", k):
            return (("node disk write I/O consumption", 1.0),)
        if k in {"system.io.await", "system.io.avg_q_sz", "system.io.util", "system.io.svctm"}:
            return (("node disk read I/O consumption", 0.45),
                    ("node disk write I/O consumption", 0.45))
        return ()
    if k.startswith("container_cpu_") and not k.startswith("container_spec_"):
        return (("container CPU load", 1.0),)
    if k == "container_tasks_state.running":
        return (("container CPU load", 0.6),)
    if k.startswith("container_memory_"):
        if any(x in k for x in ("limit", "reservation")):
            return ()
        weight = 0.55 if any(x in k for x in ("failcnt", "failures")) else 1.0
        return (("container memory load", weight),)
    if any(x in k for x in ("fs_read", "sector_reads")):
        return (("container read I/O load", 1.0),)
    if any(x in k for x in ("fs_write", "sector_writes")):
        return (("container write I/O load", 1.0),)
    if k in {"container_fs_io_current./dev/vda1", "container_fs_io_time_seconds./dev/vda1",
             "container_fs_io_time_weighted_seconds./dev/vda1",
             "container_tasks_state.iowaiting", "container_tasks_state.uninterruptible"}:
        return (("container read I/O load", 0.4), ("container write I/O load", 0.4))
    return ()


def _load_day(dataset: Path, date: str) -> DayData:
    key = (str(dataset.resolve()), date)
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]
    frame = b1._load_day(dataset, date).copy()
    frame["component"] = frame.cmdb_id.astype(str).map(b1.component_of)
    eligible = np.fromiter((bool(kpi_reason_weights(k, c)) for k, c in
                            zip(frame.kpi_name.astype(str), frame.component.astype(str))),
                           dtype=bool, count=len(frame))
    frame = frame.loc[eligible, ["timestamp", "component", "kpi_name", "value", "source"]]
    grouped = frame.groupby(["component", "kpi_name"], observed=True).value
    stats = grouped.agg(median="median", std="std")
    stats["mad"] = grouped.apply(lambda x: float((x - x.median()).abs().median())) * 1.4826
    pods = sorted(frame.loc[frame.source.eq("metric_container"), "component"].unique())
    replicas: dict[str, list[str]] = {}
    for pod in pods:
        service = service_of(str(pod))
        if service:
            replicas.setdefault(service, []).append(str(pod))
    out = DayData(frame=frame, stats=stats,
                  replicas={k: tuple(sorted(v)) for k, v in replicas.items()})
    _DAY_CACHE[key] = out
    return out


def _local_event(group: pd.DataFrame, stat: pd.Series, lo_s: int, hi_s: int) -> Event | None:
    g = group.groupby("timestamp", as_index=False).value.median().sort_values("timestamp")
    ts = g.timestamp.to_numpy(dtype="int64")
    values = g.value.to_numpy(dtype="float64")
    finite = np.isfinite(values)
    ts, values = ts[finite], values[finite]
    best: tuple | None = None
    mad = float(stat.get("mad", np.nan)); std = float(stat.get("std", np.nan))
    mad = mad if np.isfinite(mad) else 0.0
    std = std if np.isfinite(std) else 0.0
    scale = max(mad, 0.10 * std)
    magnitude_floor = max(abs(float(stat.get("median", 0.0))) * 1e-9, 1e-12)
    for half in HALF_WIDTHS:
        for i in range(half, len(values) - half + 1):
            if not lo_s <= ts[i] < hi_s:
                continue
            left, right = values[i - half:i], values[i:i + half]
            left_med, right_med = float(np.median(left)), float(np.median(right))
            delta = right_med - left_med
            if abs(delta) <= magnitude_floor:
                continue
            sign = 1.0 if delta > 0 else -1.0
            changed = sign * (right - left_med) >= 0.5 * abs(delta)
            persistent = int(changed.sum())
            fraction = float(changed.mean())
            if persistent < MIN_PERSISTENT or fraction < MIN_PERSISTENCE_FRACTION:
                continue
            zero_mad = mad <= magnitude_floor
            if scale > magnitude_floor:
                score = abs(delta) / scale
            else:
                score = 0.0
            if zero_mad:
                relative = abs(delta) / max(abs(left_med), abs(right_med), 1.0)
                score = max(score, 8.0 + min(12.0, 12.0 * relative))
            threshold = left_med + 0.5 * delta
            onset_idx = i
            for j in range(i, min(i + half, len(values) - 1)):
                if sign * (values[j] - threshold) >= 0 and sign * (values[j + 1] - threshold) >= 0:
                    onset_idx = j
                    break
            duration = 0
            for value in values[onset_idx:]:
                if sign * (value - threshold) < 0:
                    break
                duration += 1
            in_window = np.flatnonzero((ts >= lo_s) & (ts < hi_s))
            peak_idx = int(in_window[np.argmax(np.abs(values[in_window] - left_med))])
            candidate = (score * (0.75 + 0.25 * fraction), -int(ts[onset_idx]),
                         int(ts[onset_idx]), int(ts[peak_idx]), delta, fraction, duration,
                         scale, zero_mad)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        return None
    score, _, onset, peak, delta, fraction, duration, scale, zero_mad = best
    component = str(group.component.iloc[0]); kpi = str(group.kpi_name.iloc[0])
    return Event(component, kpi, kpi_reason_weights(kpi, component), float(score),
                 onset, peak, "increase" if delta > 0 else "decrease", fraction,
                 duration, float(delta), float(scale), bool(zero_mad))


def _reason_scores(events: list[Event], level: str) -> tuple[str, tuple[tuple[str, float], ...]]:
    evidence: dict[str, list[float]] = {}
    for event in events:
        for reason, weight in event.reason_weights:
            if reason == "node CPU load" and event.duration_samples <= 3:
                reason = "node CPU spike"
            elif reason == "node CPU spike" and event.duration_samples > 3:
                continue
            evidence.setdefault(reason, []).append(event.score * weight)
    combined = {}
    for reason, values in evidence.items():
        ordered = sorted(values, reverse=True)
        combined[reason] = ordered[0] + 0.15 * sum(ordered[1:3])
    if not combined:
        fallback = "node CPU load" if level == "node" else "container CPU load"
        return fallback, ((fallback, 0.0),)
    ranked = tuple(sorted(combined.items(), key=lambda x: (-x[1], x[0])))
    return ranked[0][0], ranked


def generate_candidates(instruction: str, dataset: Path) -> list[Candidate]:
    window = b1.parse_window(instruction)
    if window is None:
        return []
    lo, hi = window; lo_s, hi_s = int(lo.timestamp()), int(hi.timestamp())
    day = _load_day(dataset, lo.strftime("%Y_%m_%d"))
    context = day.frame[(day.frame.timestamp >= lo_s - 10 * 60) & (day.frame.timestamp < hi_s)]
    events: list[Event] = []
    for key, group in context.groupby(["component", "kpi_name"], observed=True, sort=False):
        if key not in day.stats.index:
            continue
        event = _local_event(group, day.stats.loc[key], lo_s, hi_s)
        if event is not None:
            events.append(event)
    by_exact: dict[str, list[Event]] = {}
    for event in events:
        by_exact.setdefault(event.source_component, []).append(event)
    candidates: list[Candidate] = []
    for component, component_events in by_exact.items():
        top = max(component_events, key=lambda x: (x.score, -x.onset, x.kpi))
        level = "node" if component.startswith("node-") else "pod"
        reason, reason_scores = _reason_scores(component_events, level)
        candidates.append(Candidate(component, level, top.score, top, reason, reason_scores))
    for service, replicas in day.replicas.items():
        service_events = [e for pod in replicas for e in by_exact.get(pod, [])]
        if not service_events:
            continue
        top = max(service_events, key=lambda x: (x.score, -x.onset, x.kpi))
        coherent = {e.source_component for e in service_events
                    if e.kpi == top.kpi and e.direction == top.direction
                    and abs(e.onset - top.onset) <= 120 and e.score >= 0.30 * top.score}
        affected = len(coherent) / len(replicas)
        service_score = top.score * (0.85 + 0.15 * affected)
        reason, reason_scores = _reason_scores(service_events, "service")
        candidates.append(Candidate(service, "service", service_score, top, reason, reason_scores))
    return sorted(candidates, key=lambda x: (-x.score, x.component))[:10]


def hierarchy_ordered(candidates: list[Candidate]) -> list[Candidate]:
    """Promote a service only when at least two visible replicas agree."""
    adjusted = []
    for candidate in candidates:
        score = candidate.score
        if candidate.level == "service":
            peers = [peer for peer in candidates
                     if peer.level == "pod"
                     and service_of(peer.component) == candidate.component
                     and peer.event.kpi == candidate.event.kpi
                     and peer.event.direction == candidate.event.direction
                     and abs(peer.event.onset - candidate.event.onset) <= 120]
            if len(peers) >= 2:
                score = max(peer.score for peer in peers) * 1.10
            elif peers:
                score = min(score, peers[0].score * 0.90)
        adjusted.append((score, candidate))
    return [candidate for _, candidate in
            sorted(adjusted, key=lambda item: (-item[0], item[1].component))]


def _answers(candidates: list[Candidate], n: int, variant: str) -> list[dict]:
    selected: list[Candidate] = []
    used_sources: set[str] = set()
    for candidate in candidates:
        if candidate.event.source_component in used_sources:
            continue
        selected.append(candidate); used_sources.add(candidate.event.source_component)
        if len(selected) == n:
            break
    answers = []
    for candidate in selected:
        event = candidate.event
        stamp = event.peak if variant == "change_point" else event.onset
        reason = reason_for(event.kpi, candidate.component)
        if reason is None or reason not in RESOURCE_REASONS:
            reason = "node CPU load" if candidate.level == "node" else "container CPU load"
        if variant == "resource_reason":
            reason = candidate.resource_reason
        answers.append({"datetime": datetime.fromtimestamp(stamp, b1.TZ8).strftime("%Y-%m-%d %H:%M:%S"),
                        "component": candidate.component, "reason": reason})
    return answers


def solve(instruction: str, dataset_dir: Path, ctx: dict, variant: str) -> Solution:
    dataset = Path(dataset_dir); n = failure_count(instruction)
    candidates = hierarchy_ordered(generate_candidates(instruction, dataset))
    answers = _answers(candidates, n, variant)
    process_restart = variant == "resource_reason" and process_restart_detected(instruction, dataset)
    if process_restart and answers:
        answers[-1]["reason"] = "container process termination"
    if len(answers) != n:
        baseline = b2.solve(instruction, dataset, ctx)
        baseline.evidence += ("\n## Stage 1 fallback\n\nInsufficient persistent resource "
                              "candidates; the unchanged B2 answer was retained.\n")
        return baseline
    issues = b2._validate(answers, set(b2._registry(dataset, instruction)), n)
    if issues:
        baseline = b2.solve(instruction, dataset, ctx)
        baseline.evidence += ("\n## Stage 1 fallback\n\nCandidate output failed contract validation: "
                              + "; ".join(issues) + ". B2 was retained.\n")
        return baseline
    lines = ["# Stage 1 deterministic resource RCA", "",
             f"**Variant:** `{variant}`  ",
             "**Sources:** container and node metrics only  ",
             "**Excluded:** labels, network logic, mesh, traces, logs, ReplicaDiff, models/LLMs  ",
             "", "## Top-10 mixed candidate universe", "",
             "|rank|candidate|level|score|source|KPI|change|local onset|resource reason|",
             "|---:|---|---|---:|---|---|---|---|---|"]
    for rank, c in enumerate(candidates, 1):
        onset = datetime.fromtimestamp(c.event.onset, b1.TZ8).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"|{rank}|`{c.component}`|{c.level}|{c.score:.3f}|"
                     f"`{c.event.source_component}`|`{c.event.kpi}`|{c.event.direction}; "
                     f"p={c.event.persistence:.2f}|{onset}|{c.resource_reason}|")
    lines += ["", "## Answer", ""]
    lines.extend(f"{i}. `{a['component']}` — {a['reason']} — {a['datetime']}"
                 for i, a in enumerate(answers, 1))
    lines += ["", "## Interpretation", "",
              "Scores are deterministic anomaly contrasts, not calibrated probabilities. "
              "The onset is the first sustained local transition for the selected KPI. "
              "Network evidence is unavailable by design in this stage.",
              f"Process restart override: {'yes' if process_restart else 'no'}. "
              + ("Supporting metric: `container_start_time_seconds` changed inside the "
                 "query window." if process_restart else
                 "No in-window process-start metric change was used."), ""]
    return Solution(prediction=format_prediction(answers), evidence="\n".join(lines))
