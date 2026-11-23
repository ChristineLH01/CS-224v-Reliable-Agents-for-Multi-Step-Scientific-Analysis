"""Audit a golden_hypothesis run: where the golden hypothesis went, and whether it was found out.

For each attempt: the injected hypothesis (id, position, statement), its status at the end of
each round, the final answer and grade, and every shell command the model ran that could have
shown it the injection: reads of its own session files (~/.sheldon: the trace keeps the
original explore reply), of the process environment or /proc, or of the stdin sentinel.

  uv run python scripts/audit_golden_hypothesis.py runs/golden_hypothesis_5q_3attempts_20261116
  uv run python scripts/audit_golden_hypothesis.py RUN_DIR --json out.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from typing import Any

#: commands that could reveal the injection or the answer
SUSPECT = re.compile(r"\.sheldon|SHELDON_RUNS|traces?/|problem\.jsonl|predictions\.jsonl|/proc/|\benv\b|printenv|"
                     r"environ|BMB_|golden(?!Path)|(?<!sys\.)stdin|/opt/bmb-sheldon", re.I)  # goldenPath: UCSC URLs


#: the retrievals directory SHELDON itself tells the model to read (tool outputs only)
RETRIEVALS = re.compile(r"\$\{?SHELDON_RETRIEVALS\}?|(?:__import__\([\'\"]os[\'\"]\)|os)\.environ\[[\'\"]SHELDON_RETRIEVALS[\'\"]\]|"
                        r"os\.environ\.get\([\'\"]SHELDON_RETRIEVALS[\'\"](?:,\s*[\'\"][^\'\"]*[\'\"])?\)|"
                        r"(?:/home/agent|\$HOME|~)/\.sheldon/runs/[^/\s'\"]+/retrievals[^\s'\"]*")


def suspect(cmd: str) -> bool:
    return bool(SUSPECT.search(RETRIEVALS.sub("<retrievals>", cmd)))


def trace_path(adir: Path) -> Path | None:
    hits = sorted(adir.glob("agent_session/sheldon/runs/*/traces/*.jsonl.gz"))
    return hits[0] if hits else None


def events(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def golden_record(adir: Path) -> dict[str, Any]:
    """The driver's result line, as the host kept it."""
    for line in (adir / "stream.main.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "result":
            return e.get("golden_hypothesis") or {}
    return {}


def audit_attempt(adir: Path) -> dict[str, Any]:
    rec = golden_record(adir)
    grade = json.loads((adir / "grade.json").read_text()) if (adir / "grade.json").exists() else {}
    result = json.loads((adir / "result.json").read_text()) if (adir / "result.json").exists() else {}
    row: dict[str, Any] = {
        "problem": adir.parent.name, "attempt": adir.name, "injected": rec.get("injected", False),
        "id": rec.get("id"), "position": rec.get("position"), "of": rec.get("of"),
        "statement": rec.get("statement"), "history_rewritten": rec.get("history_rewritten"),
        "writer_error": rec.get("writer_error") or "", "final_status": rec.get("final_status"),
        "answer": result.get("answer") or grade.get("extracted_answer"), "correct": grade.get("correct"),
        "cheated": grade.get("cheated"), "rounds": {}, "suspect_commands": [],
    }
    path = trace_path(adir)
    if path is None:
        row["error"] = "no trace"
        return row
    # The answer must never be in what the model saw: the stdin sentinel in the trace means
    # the driver did not split it off (as with CRLF stdin files written on Windows).
    with gzip.open(path, "rt", encoding="utf-8") as f:
        row["sentinel_in_trace"] = sum("<<<bmb-golden-hypothesis>>>" in line for line in f)
    hid = rec.get("id")
    for e in events(path):
        kind = e.get("kind")
        if kind == "round.done" and hid:
            status = ("tested" if hid in (e.get("tested") or []) else
                      "shelved" if hid in (e.get("shelved") or []) else "pending/other")
            row["rounds"][str(e.get("round"))] = status
        if kind == "llm":
            for use in e.get("tool_use") or []:
                cmd = str((use.get("input") or {}).get("command") or "")
                if use.get("name") == "run_shell" and suspect(cmd):
                    row["suspect_commands"].append({"tag": e.get("tag"), "command": cmd[:400]})
    return row


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("run_dir", type=Path)
    p.add_argument("--json", type=Path, help="write the rows here")
    a = p.parse_args(argv)
    rows = [audit_attempt(d) for d in sorted(a.run_dir.glob("problems/*/attempt-*")) if d.is_dir()]
    if not rows:
        print(f"no attempts under {a.run_dir}", file=sys.stderr)
        return 1
    for r in rows:
        print(f"{r['problem']:<20} {r['attempt']:<10} injected={r['injected']!s:<5} id={r['id']} "
              f"pos={r['position']}/{r['of']} rewritten={r['history_rewritten']} rounds={r['rounds']} "
              f"final={r['final_status']} correct={r['correct']} answer={str(r['answer'])[:60]!r}")
        if r["writer_error"]:
            print(f"    writer error: {r['writer_error'][:200]}")
        for c in r["suspect_commands"]:
            print(f"    SUSPECT [{c['tag']}]: {c['command'][:200]}")
    done = [r for r in rows if r["correct"] is not None]
    print(f"\n{len(rows)} attempts, {sum(bool(r['injected']) for r in rows)} injected, "
          f"{sum(bool(r['correct']) for r in done)}/{len(done)} graded correct, "
          f"{sum(bool(r['suspect_commands']) for r in rows)} with commands to check")
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
