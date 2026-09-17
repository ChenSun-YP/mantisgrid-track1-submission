"""B1: original heuristic with only the benchmark timebase repaired."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from run import Solution, format_prediction  # noqa: E402
from agents.heuristic import (Analysis, MONTHS, _load_day, component_of,  # noqa: E402
                              failure_count, reason_for)

TZ8 = timezone(timedelta(hours=8))


def parse_window(instruction: str) -> tuple[datetime, datetime] | None:
    m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4}).{0,40}?(\d{1,2}):(\d{2})"
                  r"\s*(?:to|-|and|until)\s*.{0,40}?(\d{1,2}):(\d{2})",
                  instruction, re.I | re.S)
    if not m:
        return None
    mon, day, year, h1, m1, h2, m2 = m.groups()
    if mon.lower() not in MONTHS:
        return None
    base = datetime(int(year), MONTHS[mon.lower()], int(day), tzinfo=TZ8)
    lo = base + timedelta(hours=int(h1), minutes=int(m1))
    hi = base + timedelta(hours=int(h2), minutes=int(m2))
    if hi <= lo:
        hi += timedelta(days=1)
    return lo, hi


def _load_interval(dataset: Path, lo: datetime, hi: datetime) -> pd.DataFrame:
    # The scoring interval is half-open.  An end time exactly at midnight does
    # not require the following day's file and must not change the baseline.
    last_included = hi - timedelta(microseconds=1)
    dates = [lo.strftime("%Y_%m_%d")]
    if last_included.date() != lo.date():
        dates.append(last_included.strftime("%Y_%m_%d"))
    frames = [_load_day(dataset, date) for date in dates]
    frames = [x for x in frames if not x.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["timestamp", "cmdb_id", "kpi_name", "value", "source"])


def analyse(instruction: str, dataset_dir: Path) -> Analysis | Solution:
    win = parse_window(instruction)
    if win is None:
        return Solution(prediction=format_prediction([{}]),
                        evidence="Could not parse a time window from the instruction.\n")
    lo, hi = win
    day = _load_interval(Path(dataset_dir), lo, hi)
    ev = [f"# Case\n\n**Window:** {lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} UTC+8  ",
          f"**Telemetry day(s):** `{lo:%Y_%m_%d}`"
          + (f", `{hi:%Y_%m_%d}`" if hi.date() != lo.date() else "") + "  ",
          f"**Rows loaded:** {len(day):,} (metric only -- no logs, no traces)\n"]
    if day.empty:
        return Solution(prediction=format_prediction([{}]),
                        evidence="\n".join(ev) + "\nNo metric data for the requested day(s).\n")
    lo_s, hi_s = lo.timestamp(), hi.timestamp()
    inw = day[(day.timestamp >= lo_s) & (day.timestamp < hi_s)]
    out = day[(day.timestamp < lo_s) | (day.timestamp >= hi_s)]
    if inw.empty:
        return Solution(prediction=format_prediction([{}]),
                        evidence="\n".join(ev) + "\nNo samples inside the UTC+8 window.\n")
    base = out.groupby(["cmdb_id", "kpi_name"]).value.agg(
        med="median", mad=lambda s: (s - s.median()).abs().median())
    peak = inw.groupby(["cmdb_id", "kpi_name"]).value.agg(["max", "min", "mean"])
    j = peak.join(base, how="inner").reset_index()
    j = j[j.mad > 0]
    if j.empty:
        return Solution(prediction=format_prediction([{}]),
                        evidence="\n".join(ev) + "\nNo series with non-zero variation.\n")
    j["z"] = np.maximum((j["max"] - j.med).abs(), (j["min"] - j.med).abs()) / (1.4826 * j.mad)
    j["component"] = j.cmdb_id.map(component_of)
    j = j.sort_values("z", ascending=False)
    ranked = j.groupby("component").z.max().sort_values(ascending=False)
    return Analysis(lo=lo, hi=hi, n=failure_count(instruction), ev=ev, j=j, inw=inw,
                    ranked=ranked)


def answer_for(a: Analysis, comp: str) -> dict:
    sub = a.j[a.j.component == comp]
    top, chosen = sub.iloc[0], None
    for _, row in sub.iterrows():
        reason = reason_for(row.kpi_name, comp)
        if reason:
            top, chosen = row, reason
            break
    if chosen is None:
        chosen = "node CPU load" if comp.startswith("node-") else "container CPU load"
    series = a.inw[(a.inw.cmdb_id == top.cmdb_id) & (a.inw.kpi_name == top.kpi_name)]
    when = a.lo
    if not series.empty:
        stamp = float(series.loc[(series.value - top.med).abs().idxmax(), "timestamp"])
        when = datetime.fromtimestamp(stamp, tz=TZ8)
    return {"datetime": when.strftime("%Y-%m-%d %H:%M:%S"), "component": comp,
            "reason": chosen}


def solve(instruction: str, dataset_dir: Path, ctx: dict) -> Solution:
    a = analyse(instruction, dataset_dir)
    if isinstance(a, Solution):
        return a

    return solution_from_analysis(a)


def solution_from_analysis(a: Analysis) -> Solution:
    ev, n = a.ev, a.n
    ev += [f"**Failures asked for:** {n}\n", "## Top components by strongest anomalous KPI\n",
           "| rank | component | peak z | KPI |", "|---|---|---|---|"]
    for i, (comp, z) in enumerate(a.ranked.head(max(8, n)).items(), 1):
        kpi = a.j[a.j.component == comp].iloc[0].kpi_name
        ev.append(f"| {i} | `{comp}` | {z:.1f} | `{kpi}` |")
    answers = [answer_for(a, comp) for comp in a.ranked.head(n).index]
    ev.append("\n## Answer\n")
    ev.extend(f"{i}. `{x['component']}` — {x['reason']} — {x['datetime']}"
              for i, x in enumerate(answers, 1))
    ev += ["\n## How much to trust this\n",
           "Not much. This is the original metric-only heuristic with only UTC+8 "
           "handling repaired. Reason remains a KPI keyword match and time remains "
           "the peak sample, not a causal onset estimate."]
    return Solution(prediction=format_prediction(answers), evidence="\n".join(ev) + "\n")
