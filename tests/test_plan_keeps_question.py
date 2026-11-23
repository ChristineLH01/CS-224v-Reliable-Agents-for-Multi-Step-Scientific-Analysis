"""`plan_keeps_question`: a plan split that moved the question out of `situation` is undone.

The statements below have the shape bmbench renders from prompts/problem.md. The splits
are the ones SHELDON's plan stage produced in `runs 2/preview_sheldon_*`: hb024 kept the
background sentence but moved the question to `deliverable`; hb020 kept the question and
moved only the instruction on the answer's form, which is a correct split.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sheldon")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bmbench" / "agents" / "sheldon_runtime"))

from sheldon.core.llm import FakeLLM  # noqa: E402
from sheldon.methods.sheldon.schemas import SUBMIT_SCHEMAS, ExploreOut, PlanOut  # noqa: E402

from bmb_sheldon import driver  # noqa: E402
from bmb_sheldon import selftest as selftest_script  # noqa: E402
from bmb_sheldon.adapted import ALL, plan_keeps_question, resolve  # noqa: E402

ENV = ("## Data and environment\n\n"
       "- The input data for this question is in `/workspace/data` (18 files). Your working directory is "
       "`/workspace`; put any intermediate files there.\n"
       "- You are in a Linux container with 4 CPUs and 3.5 GB of RAM (no GPU).\n\n"
       "## Rules\n\n- Looking up GEO/SRA/ENA/BioProject accession IDs is **not allowed**.")
FORMAT = ("## Answer format\n\nWhen you are done, end your final message with your answer inside answer tags, "
          "like this: `<answer>your answer</answer>`.")
HB024_Q = ("This dataset involves microbial communities from tissues of non-human species. How many tissue "
           "groups are collected, and from how many different species? Name the host species (scientific "
           "names) and the tissues. Provide counts as integers.")
HB020_Q = ("What organism does this crystal structure belong to? Provide the answer using binomial "
           "nomenclature (e.g. Canis lupus, Mus musculus, Danio rerio, etc).")
INTRO = "Answer the following bioinformatics question by analyzing the data provided."


def statement(question: str) -> str:
    return f"{INTRO}\n\n## Question\n\n{question}\n\n{ENV}\n\n{FORMAT}"


def test_the_asked_sentences_are_the_questions_or_the_first_sentence():
    assert plan_keeps_question.question_of(statement(HB024_Q)) == HB024_Q
    assert plan_keeps_question.asked(HB024_Q) == [
        "How many tissue groups are collected, and from how many different species?"]
    assert plan_keeps_question.asked(HB020_Q) == ["What organism does this crystal structure belong to?"]
    tf = "Identify the transcription factor behind these peaks. Provide its gene symbol."
    assert plan_keeps_question.asked(tf) == ["Identify the transcription factor behind these peaks."]
    assert plan_keeps_question.question_of("Which organism? No sections here.") == ""


def test_a_split_that_kept_the_question_is_kept():
    # hb020 attempt 3: the question stays, the form of the answer moves to deliverable
    situation = f"## Question\n\nWhat organism does this crystal structure belong to?\n\n{ENV}"
    assert plan_keeps_question.missing(situation, HB020_Q) == []
    # copied without backticks or bold, and rewrapped: still the question
    loose = "What organism does\nthis *crystal structure* belong to?  " + ENV.replace("`", "")
    assert plan_keeps_question.missing(loose, HB020_Q) == []


def test_a_split_that_kept_only_the_background_lost_the_question():
    # hb024 attempt 2: the background sentence is in situation, the question is not
    situation = f"{INTRO}\n\nThis dataset involves microbial communities from tissues of non-human species.\n\n{ENV}"
    assert plan_keeps_question.missing(situation, HB024_Q) == [
        "How many tissue groups are collected, and from how many different species?"]


def run_with(plan: PlanOut | None, question: str = HB024_Q):
    logged = []
    run = SimpleNamespace(task=SimpleNamespace(question=statement(question)), plan=plan,
                          ctx=SimpleNamespace(trace=SimpleNamespace(log=lambda k, **f: logged.append((k, f)))))
    return run, logged


def test_apply_restores_the_whole_statement_and_keeps_the_requirements():
    lost = PlanOut(situation=f"{INTRO}\n\n{ENV}", deliverable=f"{HB024_Q}\n\n{FORMAT}",
                   requirements=["Give the number of tissue groups."])
    run, logged = run_with(lost)
    rec = plan_keeps_question.apply(run)
    assert rec["action"] == "restored" and rec["missing"] == [
        "How many tissue groups are collected, and from how many different species?"]
    assert run.plan.situation == statement(HB024_Q) and run.plan.deliverable == ""
    assert run.plan.requirements == ["Give the number of tissue groups."]
    assert lost.situation == f"{INTRO}\n\n{ENV}"  # the plan SHELDON accepted is not edited in place
    assert logged == [(plan_keeps_question.EVENT, rec)]


def test_apply_leaves_a_good_split_a_rejected_plan_and_an_unsectioned_statement_alone():
    good = PlanOut(situation=f"## Question\n\n{HB024_Q}\n\n{ENV}", deliverable=FORMAT)
    run, _ = run_with(good)
    assert plan_keeps_question.apply(run) == {"action": "kept"} and run.plan is good
    run, _ = run_with(None)
    assert plan_keeps_question.apply(run) == {"action": "no_plan"} and run.plan is None
    run, _ = run_with(good)
    run.task.question = "Which organism do these reads come from?"
    assert plan_keeps_question.apply(run) == {"action": "no_question"} and run.plan is good


def test_it_is_switched_on_by_name_and_not_by_all():
    assert resolve(["plan_keeps_question"]) == ["plan_keeps_question"]
    assert "plan_keeps_question" not in ALL and "plan_keeps_question" not in resolve(["all"])


# -- the whole loop, offline ------------------------------------------------------------


def splitting_llm(prompts: list[tuple[str, str]]) -> FakeLLM:
    """The selftest's scripted model, with a plan stage that moves the question to
    `deliverable` (as in hb024), recording the prompt of every structured call."""

    def responder(prompt, schema):
        prompts.append((schema.__name__ if isinstance(schema, type) else str(schema), prompt))
        if schema is PlanOut:
            return PlanOut(situation=f"{INTRO}\n\n{ENV}", deliverable=f"{HB024_Q}\n\n{FORMAT}",
                           requirements=["Give the number of tissue groups."])
        return selftest_script.responder(prompt, schema)

    def tool_responder(prompt, tools):
        names = {t.get("name") for t in tools}
        for name, schema in SUBMIT_SCHEMAS.items():
            if name in names:
                return {"name": name, "input": responder(prompt, schema).model_dump()}
        return None

    return FakeLLM(responder=responder, tool_responder=tool_responder, model="gpt-6-luna")


def run_split(monkeypatch, adaptations):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    prompts: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "workspace"
        (work / "data").mkdir(parents=True)
        conf = {"sheldon": {"model": "gpt-6-luna", "effort": "high", "max_rounds": 3, "max_calls": None,
                            "max_usd": None, "params": {"mode": "session", "rounds": 3, "n_initial": 3,
                                                        "select_k": 3, "probes": ["run_shell"]}},
                "adaptations": adaptations, "workdir": str(work), "home": str(Path(tmp) / "home")}
        result = asyncio.run(driver.run_sheldon(statement(HB024_Q), conf, 300, io.StringIO(),
                                                llm=splitting_llm(prompts)))
    explore = [p for name, p in prompts if name == ExploreOut.__name__]
    return result, explore


ASKED = "How many tissue groups are collected, and from how many different species?"


def test_without_the_change_explore_never_sees_the_question(monkeypatch):
    # the bug, reproduced: SHELDON accepts the split and explore works from situation alone
    result, explore = run_split(monkeypatch, [])
    assert not result["error"] and explore
    assert not any(ASKED in p for p in explore)
    assert "plan_keeps_question" not in result


def test_with_the_change_explore_sees_the_question(monkeypatch):
    result, explore = run_split(monkeypatch, ["plan_keeps_question"])
    assert not result["error"] and explore
    assert all(ASKED in p for p in explore)
    assert result["plan_keeps_question"] == [{"action": "restored", "missing": [ASKED]}]
    assert result["answer"] == selftest_script.ANSWER


def test_question_found_in_a_statement_written_on_windows():
    assert plan_keeps_question.question_of(statement(HB024_Q).replace("\n", "\r\n")) == HB024_Q
