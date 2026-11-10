"""Collect a run's attempts into metrics.json, attempts.csv and report.md."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from bmbench.metrics import per_problem, summarize_by_split
from bmbench.runner import fmt_memory

AGENT_NAMES = {"claude_code": "Claude Code", "codex": "Codex", "sheldon": "SHELDON"}

CSV_FIELDS = [
    "problem_id", "attempt", "human_solvable", "status", "score", "correct", "cheated",
    "answer", "normalized_answer", "cost_usd", "num_turns", "agent_seconds",
    "timed_out", "finalized_after_timeout", "out_of_memory", "nudges", "n_tool_calls",
    "output_tokens", "cost_incomplete", "graded", "rounds", "stop_reason",
]


def collect(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(graded records, every record). Ungraded attempts are reported but not scored."""
    graded, every = [], []
    for adir in sorted((run_dir / "problems").glob("*/attempt-*")):
        rp = adir / "result.json"
        if not rp.is_file():
            continue
        r = json.loads(rp.read_text(encoding="utf-8"))
        gp = adir / "grade.json"
        g = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else None
        rec = {
            **{k: r.get(k) for k in ("problem_id", "attempt", "human_solvable", "status", "answer", "cost_usd",
                                      "num_turns", "agent_seconds", "timed_out", "finalized_after_timeout", "nudges",
                                      "out_of_memory", "cost_incomplete")},
            "output_tokens": (r.get("tokens") or {}).get("output_tokens"),
            "rounds": (r.get("sheldon") or {}).get("rounds"),
            "stop_reason": (r.get("sheldon") or {}).get("stop_reason"),
            "n_tool_calls": sum((r.get("tool_counts") or {}).values()),
            "graded": g is not None,
        }
        if g is not None:
            rec.update(score=g.get("score"), correct=g.get("correct"), cheated=g.get("cheated"),
                       normalized_answer=g.get("normalized_answer"),
                       judge_model=(g.get("judge") or {}).get("model"))
            graded.append(rec)
        every.append(rec)
    return graded, every


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def _num(x: float | None, fmt: str = "{:.2f}") -> str:
    return "–" if x is None else fmt.format(x)


def write_report(run_dir: Path) -> dict[str, Any]:
    graded, every = collect(run_dir)
    metrics = summarize_by_split(graded)
    problems = per_problem(graded)
    (run_dir / "metrics.json").write_text(
        json.dumps({"metrics": metrics, "problems": problems}, indent=2, default=str), encoding="utf-8"
    )
    with open(run_dir / "attempts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for rec in every:
            w.writerow(rec)
    (run_dir / "report.md").write_text(_markdown(run_dir, metrics, problems, graded, every), encoding="utf-8")
    return metrics


def _setup_line(run_dir: Path, n_problems: int, judge: str) -> str:
    """One line saying what was tested, from the run's config and manifest."""
    cfg_path, man_path = run_dir / "config.yaml", run_dir / "manifest.json"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else None) or {}
    inv = json.loads(man_path.read_text(encoding="utf-8"))["invocations"][-1] if man_path.is_file() else {}
    agent, sb = cfg.get("agent") or {}, cfg.get("sandbox") or {}
    n = cfg.get("n_attempts", "?")
    kind = agent.get("type", "claude_code")
    found = re.search(r"SHELDON ([0-9a-f]{7})" if kind == "sheldon" else r"\d+\.\d+\.\d+",
                      inv.get("agent_version") or "")
    version = found.group(found.lastindex or 0) if found else ""
    name = AGENT_NAMES.get(kind, kind)
    details = ""
    if kind == "sheldon":
        sh = agent.get("sheldon") or {}
        params = sh.get("params") or {}
        parallel = (f"≤ {params.get('select_k', 5)} hypotheses tested at once"
                    if params.get("candidate_analysis_concurrent", True) else "one hypothesis tested at a time")
        adapted = agent.get("adaptations") or []
        details = (f", {params.get('mode', 'staged')} mode, {sh.get('max_rounds', '?')} rounds, {parallel}, "
                   "Responses API" + (f", adaptations: {', '.join(adapted)}" if adapted else ""))
    network = "internet limited to each problem's allowed sites" if sb.get("network") == "allowlist" else "open internet"
    return (
        f"**Setup:** BioMysteryBench {(cfg.get('dataset') or {}).get('name', '?')} · "
        f"{n_problems} problems × {n} attempts each · {name} {version} (`{agent.get('model', '?')}`, "
        f"effort `{agent.get('effort', 'default')}`{details}) · per attempt: {round((sb.get('timeout_s') or 0) / 60)} min, "
        f"{sb.get('cpus', '?')} CPUs, {fmt_memory(sb.get('memory', '?'))} RAM, {network}, no web search · "
        f"graded by `{judge}` against the answer key; an attempt scores 0 if it looked up the source dataset."
    )


def _by_problem(rows: list[dict[str, Any]], key: str) -> str:
    """'hb024 ×2, hb035 ×1' for the problems where `key` is non-zero."""
    return ", ".join(f"{t['problem_id']} ×{t[key]}" for t in rows if t[key])


def _markdown(run_dir: Path, metrics: dict[str, Any], problems: list[dict[str, Any]],
              graded: list[dict[str, Any]], every: list[dict[str, Any]]) -> str:
    judges = Counter(r.get("judge_model") for r in graded if r.get("judge_model"))
    judge = judges.most_common(1)[0][0] if judges else "?"
    lines = [f"# Report: `{run_dir.name}`", "", _setup_line(run_dir, len({r["problem_id"] for r in every}), judge), ""]
    ungraded = sum(1 for r in every if not r["graded"])
    lines += [f"Attempts found: {len(every)} ({ungraded} not graded yet).", ""]

    k = (metrics.get("all") or {}).get("k", "k")
    lines += ["## Summary", "",
              f"| split | problems | attempts | accuracy | pass@{k} | pass^{k} | answer agreement | mean cost | mean min |",
              "|---|---|---|---|---|---|---|---|---|"]
    names = {"all": "all", "human_solvable=yes": "human-solvable", "human_solvable=no": "human-hard"}
    for split, m in metrics.items():
        if not m.get("n_problems"):
            continue
        lines.append(
            f"| {names.get(split, split)} | {m['n_problems']} | {m['n_attempts']} | {_pct(m['accuracy'])} | "
            f"{_pct(m['pass_at_k'])} | {_pct(m['pass_hat_k'])} | {_pct(m['answer_agreement'])} | "
            f"${_num(m['cost_usd_mean'])} | {_num(m['minutes_mean'], '{:.1f}')} |"
        )
    if metrics.get("all", {}).get("n_problems"):
        m = metrics["all"]
        hist = ", ".join(f"{c} ({n} problem{'s' if n != 1 else ''})" for c, n in m["solve_histogram"].items())
        context = [f"timed out {m['timed_out']} of {m['n_attempts']} attempts"
                   + (f" ({_by_problem(problems, 'timed_out')})" if m["timed_out"] else ""),
                   f"ran out of memory {m['out_of_memory']}"
                   + (f" ({_by_problem(problems, 'out_of_memory')})" if m["out_of_memory"] else ""),
                   f"flagged for cheating {m['cheated']}" + (f" ({_by_problem(problems, 'cheated')})" if m["cheated"] else "")]
        incomplete = sum(1 for rec in graded if rec.get("cost_incomplete"))
        lines += ["", f"Correct attempts per problem: {hist}", "", "Context: " + " · ".join(context)]
        rounds = Counter(rec["rounds"] for rec in every if rec.get("rounds") is not None)
        if rounds:
            lines += ["", "Rounds reached per attempt: " + ", ".join(
                f"{r} ({c} attempt{'s' if c != 1 else ''})" for r, c in sorted(rounds.items(), reverse=True))]
            stops = Counter(str(rec["stop_reason"]).split("=")[0] for rec in every if rec.get("stop_reason"))
            lines += ["", "Stop reasons: " + ", ".join(
                f"{r} ({c} attempt{'s' if c != 1 else ''})" for r, c in stops.most_common())]
        if incomplete:
            lines += ["", f"Mean cost is a lower bound: {incomplete} attempt(s) were stopped before reporting their cost."]

    lines += ["", "## Per problem", "", "| problem | human-solvable | correct | agreement | answers (✓/✗ per attempt) | "
              "notes | mean cost | mean min |", "|---|---|---|---|---|---|---|---|"]
    for t in problems:
        cells = " · ".join(f"{'✓' if s else '✗'} {(a or '(none)')[:40]}" for a, s in zip(t["answers"], t["scores"], strict=True))
        notes = ", ".join(f"{t[key]} {label}" for key, label in (("timed_out", "timed out"),
                                                                 ("out_of_memory", "out of memory"), ("cheated", "cheated"))
                          if t[key])
        lines.append(f"| {t['problem_id']} | {t['human_solvable']} | {t['correct']}/{t['n']} | "
                     f"{_pct(t['answer_agreement'])} | {cells.replace('|', '/')} | {notes or '–'} | "
                     f"${_num(t['mean_cost_usd'])} | {_num(t['mean_minutes'], '{:.1f}')} |")

    lines += ["", "**Columns:** accuracy = share of attempts that were correct (each problem weighs the same) · "
              f"pass@{k} = share of problems solved in at least one of the {k} attempts · pass^{k} = share solved "
              f"in all {k} · answer agreement = share of attempts giving the problem's most common answer, right or "
              "wrong · cost = API-price estimate per attempt (not what a subscription is billed) · "
              "timed out = hit the time limit (its running commands were stopped, and it then had up to 5 min "
              "to give its best answer) · out of memory = the agent process itself was killed for going over the "
              "memory limit; the attempt ends there and is scored incorrect (commands killed for memory do not "
              "count: the agent sees the error and carries on)."]
    return "\n".join(lines) + "\n"
