import math

import pytest

from bmbench.metrics import answer_key, pass_at_k, pass_hat_k, per_problem, summarize, summarize_by_split


def recs(pid, scores, answers, solvable="yes", **extra):
    return [
        {"problem_id": pid, "attempt": i + 1, "score": s, "normalized_answer": a, "answer": a,
         "human_solvable": solvable, "cost_usd": 1.0, "agent_seconds": 600, "num_turns": 10, **extra}
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


def test_per_problem_agreement_and_flags():
    rows = per_problem(recs("A", [1, 1, 1, 0, 0], ["GENEA", "genea", "GENEA", "GENEB", "GENEB"]))
    (t,) = rows
    assert t["correct"] == 3 and t["accuracy"] == pytest.approx(0.6)
    assert t["answer_agreement"] == pytest.approx(0.6)
    assert t["timed_out"] == t["cheated"] == 0


def test_summarize_key_metrics():
    records = (
        recs("A", [1, 1, 1, 1, 1], ["x"] * 5)
        + recs("B", [1, 0, 0, 0, 0], ["y", "z", "z", "z", "w"], solvable="no", timed_out=True)
        + recs("C", [0, 0, 0, 0, 0], [""] * 5, solvable="no")
    )
    records[5]["cheated"] = True
    m = summarize(records)
    assert m["n_problems"] == 3 and m["n_attempts"] == 15 and m["k"] == 5
    assert m["accuracy"] == pytest.approx((1 + 0.2 + 0) / 3)
    assert m["pass_at_k"] == pytest.approx(2 / 3)
    assert m["pass_hat_k"] == pytest.approx(1 / 3)
    assert m["answer_agreement"] == pytest.approx((1 + 0.6 + 1) / 3)
    assert list(m["solve_histogram"].items()) == [("5/5", 1), ("1/5", 1), ("0/5", 1)]
    assert m["cost_usd_mean"] == pytest.approx(1.0) and m["minutes_mean"] == pytest.approx(10.0)
    assert (m["timed_out"], m["cheated"]) == (5, 1)
    assert "accuracy_ci95" not in m and "majority_vote_accuracy" not in m
    split = summarize_by_split(records)
    assert split["human_solvable=yes"]["n_problems"] == 1 and split["human_solvable=no"]["n_problems"] == 2


def test_k_is_the_smallest_trial_count():
    m = summarize(recs("A", [1, 0, 1], ["x", "y", "x"]) + recs("B", [1, 1], ["z", "z"]))
    assert m["k"] == 2 and m["pass_hat_k"] == pytest.approx((1 / 3 + 1) / 2)


def test_report_table_and_context(tmp_path):
    import json

    from bmbench.report import write_report

    for k, (score, timed_out) in enumerate([(1.0, False), (1.0, False), (0.0, True)], start=1):
        d = tmp_path / "problems" / "A" / f"attempt-{k}"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "problem_id": "A", "attempt": k, "human_solvable": "yes", "status": "completed", "answer": "GENEA",
            "cost_usd": 0.5, "agent_seconds": 60, "timed_out": timed_out}))
        (d / "grade.json").write_text(json.dumps({"score": score, "correct": bool(score), "cheated": False,
                                                  "normalized_answer": "GENEA", "judge": {"model": "gpt-6-sol"}}))
    m = write_report(tmp_path)["all"]
    assert m["accuracy"] == pytest.approx(2 / 3) and m["pass_at_k"] == 1.0 and m["pass_hat_k"] == 0.0
    report = (tmp_path / "report.md").read_text()
    assert "| all | 1 | 3 | 67% | 100% | 0% | 100% | $0.50 | 1.0 |" in report
    assert "Correct attempts per problem: 2/3 (1 problem)" in report
    assert "timed out 1 of 3 attempts (A ×1)" in report and "graded by `gpt-6-sol`" in report
