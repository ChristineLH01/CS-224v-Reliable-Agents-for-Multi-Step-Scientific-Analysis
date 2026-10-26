"""Accuracy and consistency over repeated trials.

Input: one record per graded trial with at least task_id, human_solvable, score,
and normalized_answer. Tasks are weighted equally (mean of per-task means), so a
task with a missing trial does not count for less.
"""

from __future__ import annotations

import random
import re
import statistics
from collections import Counter, defaultdict
from math import comb
from typing import Any

RELIABLE = 0.8  # >= 4/5
BRITTLE = 0.4  # 1-2/5


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


def bootstrap_ci(per_task_scores: list[list[float]], n_boot: int = 5000, seed: int = 0,
                 alpha: float = 0.05) -> tuple[float, float]:
    """Two-level bootstrap: resample tasks, then trials within each sampled task."""
    rng = random.Random(seed)
    tasks = [s for s in per_task_scores if s]
    if not tasks:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_boot):
        total = 0.0
        for _t in range(len(tasks)):
            scores = tasks[rng.randrange(len(tasks))]
            total += sum(scores[rng.randrange(len(scores))] for _ in scores) / len(scores)
        means.append(total / len(tasks))
    means.sort()
    lo = means[int((alpha / 2) * (n_boot - 1))]
    hi = means[int((1 - alpha / 2) * (n_boot - 1))]
    return lo, hi


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


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
        keys = [answer_key(r.get("normalized_answer") or r.get("answer")) for r in rs]
        counts = Counter(keys)
        modal, modal_n = counts.most_common(1)[0]
        # majority vote: the modal answer is graded by the trials that gave it
        modal_scores = [s for s, k in zip(scores, keys, strict=True) if k == modal]
        rows.append({
            "task_id": task_id,
            "human_solvable": rs[0].get("human_solvable", ""),
            "n": n,
            "correct": c,
            "accuracy": c / n,
            "answers": [r.get("normalized_answer") or r.get("answer") or "" for r in rs],
            "scores": scores,
            "modal_answer": modal,
            "answer_agreement": modal_n / n,
            "distinct_answers": len(counts),
            "majority_correct": 1.0 if modal != "(none)" and sum(modal_scores) / len(modal_scores) >= 0.5 else 0.0,
            "outcome_consistent": len(set(scores)) == 1,
            "cheated": sum(1 for r in rs if r.get("cheated")),
            "mean_cost_usd": _mean([r.get("cost_usd") for r in rs]),
            "mean_minutes": _mean([(r.get("agent_seconds") or 0) / 60 for r in rs]),
            "mean_turns": _mean([r.get("num_turns") for r in rs]),
        })
    return rows


def summarize(records: list[dict[str, Any]], n_boot: int = 5000) -> dict[str, Any]:
    tasks = per_task(records)
    if not tasks:
        return {"n_tasks": 0, "n_trials": 0}
    n_min = min(t["n"] for t in tasks)
    acc = _mean([t["accuracy"] for t in tasks])
    lo, hi = bootstrap_ci([t["scores"] for t in tasks], n_boot=n_boot)
    solved = [t for t in tasks if t["correct"] > 0]
    hist = Counter(f"{t['correct']}/{t['n']}" for t in tasks)
    costs = [r.get("cost_usd") or 0.0 for r in records]
    minutes = [(r.get("agent_seconds") or 0) / 60 for r in records]
    statuses = Counter(r.get("status", "") for r in records)
    return {
        "n_tasks": len(tasks),
        "n_trials": len(records),
        "trials_per_task_min": n_min,
        "accuracy": acc,
        "accuracy_ci95": [lo, hi],
        "pass_at_k": {k: _mean([pass_at_k(t["n"], t["correct"], k) for t in tasks]) for k in range(1, n_min + 1)},
        "pass_hat_k": {k: _mean([pass_hat_k(t["n"], t["correct"], k) for t in tasks]) for k in range(1, n_min + 1)},
        "majority_vote_accuracy": _mean([t["majority_correct"] for t in tasks]),
        "solve_histogram": dict(sorted(hist.items(), key=lambda kv: int(kv[0].split("/")[0]))),
        "reliable_tasks": sum(1 for t in tasks if t["accuracy"] >= RELIABLE),
        "brittle_tasks": sum(1 for t in tasks if 0 < t["accuracy"] <= BRITTLE),
        "reliable_share_of_solved": (sum(1 for t in solved if t["accuracy"] >= RELIABLE) / len(solved)) if solved else None,
        "answer_agreement": _mean([t["answer_agreement"] for t in tasks]),
        "outcome_consistency": _mean([1.0 if t["outcome_consistent"] else 0.0 for t in tasks]),
        "mean_distinct_answers": _mean([t["distinct_answers"] for t in tasks]),
        "cost_usd_mean": _mean(costs),
        "cost_usd_median": statistics.median(costs) if costs else None,
        "cost_usd_total": sum(costs),
        "minutes_mean": _mean(minutes),
        "minutes_median": statistics.median(minutes) if minutes else None,
        "turns_mean": _mean([r.get("num_turns") for r in records]),
        "rates": {
            "timed_out": _mean([1.0 if r.get("timed_out") else 0.0 for r in records]),
            "finalized_after_timeout": _mean([1.0 if r.get("finalized_after_timeout") else 0.0 for r in records]),
            "nudged": _mean([1.0 if r.get("nudges") else 0.0 for r in records]),
            "no_answer": _mean([0.0 if r.get("answer") else 1.0 for r in records]),
            "cheated": _mean([1.0 if r.get("cheated") else 0.0 for r in records]),
            "agent_error": _mean([1.0 if r.get("status") == "agent_error" else 0.0 for r in records]),
        },
        "statuses": dict(statuses),
    }


def summarize_by_split(records: list[dict[str, Any]], n_boot: int = 5000) -> dict[str, Any]:
    out = {"all": summarize(records, n_boot)}
    for split in ("yes", "no"):
        rs = [r for r in records if r.get("human_solvable") == split]
        if rs:
            out[f"human_solvable={split}"] = summarize(rs, n_boot)
    return out
