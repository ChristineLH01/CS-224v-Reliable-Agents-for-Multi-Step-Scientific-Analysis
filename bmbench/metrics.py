"""Accuracy and consistency over repeated trials.

Input: one record per graded trial with at least task_id, human_solvable, score,
and normalized_answer. Tasks are weighted equally (mean of per-task means), so a
task with a missing trial does not count for less.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from math import comb
from typing import Any


def pass_at_k(n: int, c: int, k: int) -> float:
    """P(at least one of k draws without replacement is correct): 1 - C(n-c,k)/C(n,k)."""
    if k > n:
        raise ValueError("k > n")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """P(all k draws are correct): C(c,k)/C(n,k)."""
    if k > n:
        raise ValueError("k > n")
    return comb(c, k) / comb(n, k)


def answer_key(text: str | None) -> str:
    """Normalize an answer for agreement counting: case, punctuation, spacing."""
    t = (text or "").strip().lower()
    t = re.sub(r"[\s_]+", " ", t)
    t = re.sub(r"[^\w ,.+-]", "", t)
    return t.strip(" .") or "(none)"


def _mean(xs: list[float | None]) -> float | None:
    ys = [x for x in xs if x is not None]
    return sum(ys) / len(ys) if ys else None


def per_task(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_task[r["task_id"]].append(r)
    rows = []
    for task_id, rs in sorted(by_task.items()):
        rs = sorted(rs, key=lambda r: r.get("trial", 0))
        n = len(rs)
        scores = [float(r.get("score") or 0.0) for r in rs]
        c = int(sum(scores))
        counts = Counter(answer_key(r.get("normalized_answer") or r.get("answer")) for r in rs)
        rows.append({
            "task_id": task_id,
            "human_solvable": rs[0].get("human_solvable", ""),
            "n": n,
            "correct": c,
            "accuracy": c / n,
            "answers": [r.get("normalized_answer") or r.get("answer") or "" for r in rs],
            "scores": scores,
            "answer_agreement": counts.most_common(1)[0][1] / n,
            "timed_out": sum(1 for r in rs if r.get("timed_out")),
            "cheated": sum(1 for r in rs if r.get("cheated")),
            "mean_cost_usd": _mean([r.get("cost_usd") for r in rs]),
            "mean_minutes": _mean([(r.get("agent_seconds") or 0) / 60 for r in rs]),
        })
    return rows


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The key metrics. `k` is the number of trials per question (the smallest, if
    a question is missing some)."""
    tasks = per_task(records)
    if not tasks:
        return {"n_tasks": 0, "n_trials": 0}
    k = min(t["n"] for t in tasks)
    hist = Counter(f"{t['correct']}/{t['n']}" for t in tasks)
    return {
        "n_tasks": len(tasks),
        "n_trials": len(records),
        "k": k,
        # accuracy
        "accuracy": _mean([t["accuracy"] for t in tasks]),
        "pass_at_k": _mean([pass_at_k(t["n"], t["correct"], k) for t in tasks]),
        # consistency
        "pass_hat_k": _mean([pass_hat_k(t["n"], t["correct"], k) for t in tasks]),
        "answer_agreement": _mean([t["answer_agreement"] for t in tasks]),
        "solve_histogram": dict(sorted(hist.items(), key=lambda kv: -int(kv[0].split("/")[0]))),
        # cost and time per trial
        "cost_usd_mean": _mean([r.get("cost_usd") or 0.0 for r in records]),
        "minutes_mean": _mean([(r.get("agent_seconds") or 0) / 60 for r in records]),
        # context, not metrics: counts of trials
        "timed_out": sum(t["timed_out"] for t in tasks),
        "cheated": sum(t["cheated"] for t in tasks),
    }


def summarize_by_split(records: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": summarize(records)}
    for split in ("yes", "no"):
        rs = [r for r in records if r.get("human_solvable") == split]
        if rs:
            out[f"human_solvable={split}"] = summarize(rs)
    return out
