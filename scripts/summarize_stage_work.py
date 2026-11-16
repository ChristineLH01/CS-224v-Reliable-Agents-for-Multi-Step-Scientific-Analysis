"""Recompute native stage work for the user-labelled early-stop baseline and shared state.

Read-only over run artifacts; output derived measurements/report tables under docs.
Actual shell executions include the shared download helper, but not cache hits.
Native accepted observations/hypotheses are counted from application events,
including work completed in an attempt that later failed before finalization.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from bmbench.agents.sheldon import Recorded

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/trajectory-audit"
BASE = ROOT / "runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts"
SHARED = [ROOT / "runs" / name for name in (
    "adapted_shared_state_v2_hb035_3attempts_20261113",
    "adapted_shared_state_v2_hb010_3attempts_20261113",
    "adapted_shared_state_v2_remaining_preview_3attempts_20261113",
    "adapted_shared_state_v2_remaining_full_3attempts_20261113",
)]
STAGES = ("Propose and select", "Test", "Compare and revise", "Finalize")


def stage_of(tag: str) -> str:
    tag = tag.removeprefix("session.").split(".", 1)[0]
    if tag in ("plan", "merge", "explore", "select"):
        return STAGES[0]
    if tag == "candidate_analysis":
        return STAGES[1]
    if tag in ("comparative_analysis", "exploit"):
        return STAGES[2]
    if tag == "finalize":
        return STAGES[3]
    raise ValueError(f"Unmapped shell stage: {tag}")


def measure(version: str, directory: Path) -> dict:
    result = json.loads((directory / "result.json").read_text())
    trace = next(directory.rglob("traces/problem.jsonl.gz"))
    recorded = Recorded(trace)
    counts = {name: Counter(shell=0, hypotheses=0, observations=0) for name in STAGES}
    observation_ids = set()
    hypothesis_ids = set()

    def add_ids(stage: str, field: str, ids: list[str]) -> None:
        seen = observation_ids if field == "observations" else hypothesis_ids
        assert not (set(ids) & seen), (directory, field, ids)
        seen.update(ids)
        counts[stage][field] += len(ids)

    for event in recorded.events:
        kind = event.get("kind")
        if kind == "explore.done":
            add_ids(STAGES[0], "hypotheses", event.get("added", []))
        elif kind == "candidate_analysis.revision" and event.get("accepted"):
            assert event.get("child"), (directory, event)
            add_ids(STAGES[1], "hypotheses", [event["child"]])
        elif kind == "candidate_analysis.iteration":
            add_ids(STAGES[1], "observations", event.get("observations", []))
        elif kind == "comparative_analysis.done":
            add_ids(STAGES[2], "observations", event.get("observations", []))
        elif kind == "exploit.done":
            # refine contains parent IDs, so inspect the parent-to-child state
            # only through the accepted-operation count (native method demotes
            # unsuccessful refinements to keep).
            counts[STAGES[2]]["hypotheses"] += len(event.get("refine", []))

    final = next((e for e in reversed(recorded.events) if e.get("kind") == "sheldon.final"), None)
    if final is not None:
        assert sum(c["observations"] for c in counts.values()) == len(final["observations"]), directory
        assert sum(c["hypotheses"] for c in counts.values()) == len(final["hypotheses"]), directory

    if version == "early_stop_only":
        for call in recorded.calls():
            if call["kind"] == "llm":
                for use in call["uses"]:
                    if use["name"] == "run_shell":
                        assert use["retrieval"] is not None
                        counts[stage_of(call["tag"])]["shell"] += 1
        assert sum(c["shell"] for c in counts.values()) == result["tool_counts"]["Bash"]
    else:
        entries = json.loads((directory / "agent_session/sheldon/analysis/state.json").read_text())["entries"]
        for entry in entries:
            if entry["kind"] in ("command", "lookup"):
                counts[stage_of(entry["stage"])]["shell"] += 1

    return {"version": version, "problem": directory.parent.name,
            "attempt": int(directory.name.split("-")[-1]), "status": result["status"],
            "directory": str(directory.relative_to(ROOT)),
            "counts": {s: dict(c) for s, c in counts.items()}}


def main() -> None:
    rows = []
    for version, roots in (("early_stop_only", [BASE]), ("shared_state", SHARED)):
        seen = set()
        for root in roots:
            for directory in sorted((root / "problems").glob("*/attempt-*")):
                key = (directory.parent.name, directory.name)
                assert key not in seen
                seen.add(key)
                rows.append(measure(version, directory))
        assert len(seen) == 30

    summary = {}
    for version in ("early_stop_only", "shared_state"):
        attempts = [r for r in rows if r["version"] == version]
        totals = {s: {k: sum(r["counts"][s][k] for r in attempts)
                      for k in ("shell", "hypotheses", "observations")} for s in STAGES}
        all_shell = sum(c["shell"] for c in totals.values())
        summary[version] = {"n_attempts": len(attempts), "totals": totals,
                            "means": {s: {k: n / len(attempts) for k, n in c.items()}
                                      for s, c in totals.items()},
                            "shell_shares": {s: c["shell"] / all_shell for s, c in totals.items()}}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage-work.json").write_text(json.dumps({"summary": summary, "attempts": rows}, indent=2) + "\n")
    with (OUT / "stage-work-per-attempt.csv").open("w", newline="") as stream:
        fields = ["version", "problem", "attempt", "status", "stage", "hypotheses", "shell", "observations"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            for stage, counts in row["counts"].items():
                writer.writerow({**{k: row[k] for k in fields[:4]}, "stage": stage, **counts})

    lines = ["## Work by stage", "",
             "Both conditions include early stopping. Values are means over all 30 attempts; shares use total actual shell executions. Hypotheses count accepted additions: proposed by exploration, revised during testing, or refined during revision. Observations count native accepted observations, not shared-ledger copies.", ""]
    for version, title in (("early_stop_only", "Early-stop SHELDON"), ("shared_state", "Early-stop SHELDON + shared state")):
        info = summary[version]
        lines += [f"**{title}**", "", "| Stage | Hypotheses | Shell commands (share) | Observations |",
                  "|---|---:|---:|---:|"]
        for stage, verb in zip(STAGES, ("proposed", "revised", "refined", "")):
            mean = info["means"][stage]
            hypotheses = f"{mean['hypotheses']:.1f} {verb}" if verb else "—"
            lines.append(f"| {stage} | {hypotheses} | {mean['shell']:.1f} ({info['shell_shares'][stage]:.1%}) | {mean['observations']:.1f} |")
        means = {k: sum(c[k] for c in info["totals"].values()) / 30 for k in ("hypotheses", "shell", "observations")}
        lines += [f"| Total | {means['hypotheses']:.1f} | {means['shell']:.1f} (100%) | {means['observations']:.1f} |", ""]
    lines += ["Finalize is shown separately because it executed 9 shell commands in the baseline and 25 in the shared-state condition; omitting it would make the totals inconsistent. Means and percentages are rounded independently. Partial work from the shared-state attempt interrupted by an API error is included; final snapshots alone would omit its 12 already accepted observations.", "",
              "Testing remains approximately 83% of shell work. Shared state reduced absolute shell executions across all three main stages, while comparison-stage observations rose from 7.6 to 10.2 per attempt. Total native observations stayed close (46.3 vs 46.6); this does not establish equal or improved evidence quality. Lower hypothesis/round totals also reflect different stopping and the interrupted attempt, rather than a changed per-round proposal setting.", ""]
    (OUT / "stage-work-tables.md").write_text("\n".join(lines))
    report = ROOT / "docs/shared-analysis-state-report.md"
    text = report.read_text()
    heading = "## Work by stage" if "## Work by stage" in text else "## Effect on the stage-level analysis"
    start = text.index(heading)
    end = text.index("## Trajectory evidence", start)
    report.write_text(text[:start] + "\n".join(lines) + "\n" + text[end:])
    print("\n".join(lines))


if __name__ == "__main__":
    main()
