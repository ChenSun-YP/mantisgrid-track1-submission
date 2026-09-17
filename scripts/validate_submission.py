#!/usr/bin/env python3
"""Run two cases and reject output that the official evaluator cannot consume."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--cases", type=int, default=2)
    args = parser.parse_args()

    submission = Path(args.submission).resolve()
    output = Path(tempfile.mkdtemp(prefix="mantisgrid-validate-"))
    command = [sys.executable, "run.py", "--dataset", str(Path(args.dataset).resolve()),
               "--queries", str(Path(args.queries).resolve()), "--out", str(output),
               "--limit", str(args.cases), "--agent",
               os.environ.get("VAL_AGENT", "agents.stage1_resource")]
    result = subprocess.run(command, cwd=submission, capture_output=True, text=True,
                            timeout=1800)
    if result.returncode:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    query = pd.read_csv(args.queries).head(args.cases)
    predictions = pd.read_csv(output / "predictions.csv")
    assert len(predictions) == len(query) == predictions.row_id.nunique()
    assert set(predictions.row_id) == set(query.row_id)
    pattern = re.compile(
        r'{\s*(?:"root cause occurrence datetime":\s*"(.*?)")?,?\s*'
        r'(?:"root cause component":\s*"(.*?)")?,?\s*'
        r'(?:"root cause reason":\s*"(.*?)")?\s*}')
    assert all(pattern.search(str(value)) for value in predictions.prediction)
    evidence = output / "evidence"
    assert all((evidence / f"{row_id}.md").is_file() for row_id in query.row_id)
    print(f"validated {len(query)} case(s) in {output}")


if __name__ == "__main__":
    main()
