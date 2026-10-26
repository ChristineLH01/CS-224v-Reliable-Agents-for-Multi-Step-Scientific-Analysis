"""Collect a run's trials into metrics.json, trials.csv and report.md."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from bmbench.metrics import per_task, summarize_by_split
from bmbench.runner import fmt_memory

CSV_FIELDS = [
    "task_id", "trial", "human_solvable", "status", "score", "correct", "cheated",
    "answer", "normalized_answer", "cost_usd", "num_turns", "agent_seconds",
    "timed_out", "finalized_after_timeout", "nudges", "n_tool_calls", "output_tokens", "cost_incomplete", "graded",
]


def collect(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(graded records, every record). Ungraded trials are reported but not scored."""
    graded, every = [], []
    for tdir in sorted((run_dir / "trials").glob("*/r*")):
        rp = tdir / "result.json"
        if not rp.is_file():
            continue
        r = json.loads(rp.read_text(encoding="utf-8"))
        gp = tdir / "grade.json"
        g = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else None
        rec = {
            **{k: r.get(k) for k in ("task_id", "trial", "human_solvable", "status", "answer", "cost_usd",
                                      "num_turns", "agent_seconds", "timed_out", "finalized_after_timeout", "nudges",
                                      "cost_incomplete")},
            "output_tokens": (r.get("tokens") or {}).get("output_tokens"),
            "n_tool_calls": sum((r.get("tool_counts") or {}).values()),
            "graded": g is not None,
        }
        if g is not None:
            rec.update(score=g.get("score"), correct=g.get("correct"), cheated=g.get("cheated"),
                       normalized_answer=g.get("normalized_answer"),
                       denied=sum(((g.get("network") or {}).get("denied") or {}).values()))
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
    tasks = per_task(graded)
    (run_dir / "metrics.json").write_text(
        json.dumps({"metrics": metrics, "tasks": tasks}, indent=2, default=str), encoding="utf-8"
    )
    with open(run_dir / "trials.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for rec in every:
            w.writerow(rec)
    (run_dir / "report.md").write_text(_markdown(run_dir, metrics, tasks, graded, every), encoding="utf-8")
    return metrics


def _setup_line(run_dir: Path, n_questions: int) -> str:
    """One line saying what was tested, from the run's config and manifest."""
    cfg_path, man_path = run_dir / "config.yaml", run_dir / "manifest.json"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else None) or {}
    inv = json.loads(man_path.read_text(encoding="utf-8"))["invocations"][-1] if man_path.is_file() else {}
    agent, sb = cfg.get("agent") or {}, cfg.get("sandbox") or {}
    n = cfg.get("n_trials", "?")
    version = (inv.get("agent_version") or "").replace("(Claude Code)", "").strip()
    network = "internet limited to each question's allowed sites" if sb.get("network") == "allowlist" else "open internet"
    return (
        f"**Setup:** BioMysteryBench {(cfg.get('dataset') or {}).get('name', '?')} · "
        f"{n_questions} questions × {n} trials each · Claude Code {version} (`{agent.get('model', '?')}`, "
        f"effort `{agent.get('effort', 'default')}`) · per trial: {round((sb.get('timeout_s') or 0) / 60)} min, "
        f"{sb.get('cpus', '?')} CPUs, {fmt_memory(sb.get('memory', '?'))} RAM, {network}, no web search · "
        "graded by a Claude judge against the answer key; a trial scores 0 if it looked up the source dataset."
    )


def _markdown(run_dir: Path, metrics: dict[str, Any], tasks: list[dict[str, Any]],
              graded: list[dict[str, Any]], every: list[dict[str, Any]]) -> str:
    lines = [f"# Report: `{run_dir.name}`", "", _setup_line(run_dir, len({r["task_id"] for r in every})), ""]
    ungraded = sum(1 for r in every if not r["graded"])
    lines += [f"Trials found: {len(every)} ({ungraded} not graded yet).", ""]

    k = (metrics.get("all") or {}).get("trials_per_task_min", "k")
    lines += ["## Summary", "",
              f"| split | questions | trials | accuracy (95% CI) | pass@{k} | pass^{k} | majority | answer agreement | "
              "outcome consistency | reliable / brittle | mean cost | mean min |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    names = {"all": "all", "human_solvable=yes": "human-solvable", "human_solvable=no": "human-hard"}
    for split, m in metrics.items():
        if not m.get("n_tasks"):
            continue
        kk = m["trials_per_task_min"]
        lo, hi = m["accuracy_ci95"]
        lines.append(
            f"| {names.get(split, split)} | {m['n_tasks']} | {m['n_trials']} | {_pct(m['accuracy'])} ({_pct(lo)}–{_pct(hi)}) | "
            f"{_pct(m['pass_at_k'].get(kk))} | {_pct(m['pass_hat_k'].get(kk))} | "
            f"{_pct(m['majority_vote_accuracy'])} | {_pct(m['answer_agreement'])} | "
            f"{_pct(m['outcome_consistency'])} | {m['reliable_tasks']} / {m['brittle_tasks']} | "
            f"${_num(m['cost_usd_mean'])} | {_num(m['minutes_mean'], '{:.1f}')} |"
        )
    if metrics.get("all", {}).get("n_tasks"):
        m = metrics["all"]
        r = m["rates"]
        blocked = sum(rec.get("denied") or 0 for rec in graded)
        statuses = ", ".join(f"{s} {n}" for s, n in Counter(rec.get("status") for rec in every).items())
        incomplete = sum(1 for rec in every if rec.get("cost_incomplete"))
        hist = ", ".join(f"{c} ({n} question{'s' if n != 1 else ''})"
                         for c, n in sorted(m["solve_histogram"].items(), key=lambda kv: -int(kv[0].split("/")[0])))
        lines += ["",
                  f"Correct trials per question: {hist} · statuses: {statuses}",
                  f"Rates: timed out {_pct(r['timed_out'])}, nudged {_pct(r['nudged'])}, no answer {_pct(r['no_answer'])}, "
                  f"cheated {_pct(r['cheated'])}, agent error {_pct(r['agent_error'])} · blocked network requests: {blocked}",
                  f"Total cost (API-price estimate): ${_num(m['cost_usd_total'])}"
                  + (f" (lower bound: {incomplete} timed-out trial(s) did not report their cost)" if incomplete else "")]

    lines += ["", "## Per question", "", "| question | human-solvable | correct | agreement | answers (✓/✗ per trial) | "
              "mean cost | mean min |", "|---|---|---|---|---|---|---|"]
    for t in tasks:
        cells = " · ".join(f"{'✓' if s else '✗'} {(a or '(none)')[:40]}" for a, s in zip(t["answers"], t["scores"], strict=True))
        lines.append(f"| {t['task_id']} | {t['human_solvable']} | {t['correct']}/{t['n']} | "
                     f"{_pct(t['answer_agreement'])} | {cells.replace('|', '/')} | "
                     f"${_num(t['mean_cost_usd'])} | {_num(t['mean_minutes'], '{:.1f}')} |")

    lines += ["", "**Columns:** accuracy = share of trials that were correct (each question weighs the same) · "
              f"pass@{k} = solved in at least one of the {k} trials · pass^{k} = solved in all {k} · "
              "majority = the most common answer is correct · answer agreement = share of trials giving the "
              "question's most common answer · outcome consistency = share of questions whose trials are all right "
              "or all wrong · reliable = questions solved in at least 4 of 5 trials, brittle = in only 1–2 · cost = Claude Code's "
              "API-price estimate (not what a subscription is billed)."]
    return "\n".join(lines) + "\n"
