import math

import pytest

from bmbench.metrics import answer_key, bootstrap_ci, pass_at_k, pass_hat_k, per_task, summarize, summarize_by_split


def recs(task, scores, answers, solvable="yes"):
    return [
        {"task_id": task, "trial": i + 1, "score": s, "normalized_answer": a, "answer": a,
         "human_solvable": solvable, "cost_usd": 1.0, "agent_seconds": 600, "num_turns": 10}
        for i, (s, a) in enumerate(zip(scores, answers, strict=True))
    ]


def test_pass_estimators():
    assert pass_at_k(5, 0, 1) == 0.0
    assert pass_at_k(5, 5, 5) == 1.0
    assert pass_at_k(5, 1, 1) == pytest.approx(0.2)
    assert pass_at_k(5, 1, 5) == 1.0
    assert pass_at_k(5, 2, 2) == pytest.approx(1 - math.comb(3, 2) / math.comb(5, 2))
    assert pass_hat_k(5, 4, 5) == 0.0
    assert pass_hat_k(5, 5, 5) == 1.0
    assert pass_hat_k(5, 4, 2) == pytest.approx(math.comb(4, 2) / math.comb(5, 2))
    with pytest.raises(ValueError):
        pass_at_k(3, 1, 4)


def test_answer_key_normalizes_trivia():
    assert answer_key("Examplea specia.") == answer_key("examplea  specia")
    assert answer_key("") == "(none)" and answer_key(None) == "(none)"
    assert answer_key("hsa-miR-0000-5p") == "hsa-mir-0000-5p"


def test_per_task_consistency():
    rows = per_task(recs("A", [1, 1, 1, 0, 0], ["GENEA", "genea", "GENEA", "GENEB", "GENEB"]))
    (t,) = rows
    assert t["correct"] == 3 and t["accuracy"] == pytest.approx(0.6)
    assert t["answer_agreement"] == pytest.approx(0.6) and t["distinct_answers"] == 2
    assert t["majority_correct"] == 1.0 and not t["outcome_consistent"]


def test_summarize_macro_average_and_buckets():
    records = (
        recs("A", [1, 1, 1, 1, 1], ["x"] * 5)  # reliable
        + recs("B", [1, 0, 0, 0, 0], ["y", "z", "z", "z", "w"], solvable="no")  # brittle, majority wrong
        + recs("C", [0, 0, 0, 0, 0], [""] * 5, solvable="no")  # unsolved, no answers
    )
    m = summarize(records, n_boot=500)
    assert m["n_tasks"] == 3 and m["n_trials"] == 15
    assert m["accuracy"] == pytest.approx((1 + 0.2 + 0) / 3)
    assert m["pass_at_k"][5] == pytest.approx(2 / 3)
    assert m["pass_hat_k"][5] == pytest.approx(1 / 3)
    assert m["majority_vote_accuracy"] == pytest.approx(1 / 3)
    assert m["reliable_tasks"] == 1 and m["brittle_tasks"] == 1
    assert m["reliable_share_of_solved"] == pytest.approx(0.5)
    assert m["outcome_consistency"] == pytest.approx(2 / 3)
    assert m["solve_histogram"] == {"0/5": 1, "1/5": 1, "5/5": 1}
    lo, hi = m["accuracy_ci95"]
    assert 0 <= lo <= m["accuracy"] <= hi <= 1
    split = summarize_by_split(records, n_boot=200)
    assert split["human_solvable=yes"]["n_tasks"] == 1 and split["human_solvable=no"]["n_tasks"] == 2


def test_bootstrap_degenerate():
    assert bootstrap_ci([[1.0, 1.0], [1.0]], n_boot=100) == (1.0, 1.0)
    lo, hi = bootstrap_ci([], n_boot=10)
    assert math.isnan(lo) and math.isnan(hi)
