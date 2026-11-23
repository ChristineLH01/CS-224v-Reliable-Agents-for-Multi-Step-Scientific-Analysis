"""`keep_answer_rivals` and `keep_neutral_hypotheses` against the pinned SHELDON."""
import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sheldon")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bmbench/agents/sheldon_runtime"))

from sheldon.core.llm import FakeLLM  # noqa: E402
from sheldon.core.trace import Trace  # noqa: E402
from sheldon.core.types import Usage  # noqa: E402
from sheldon.methods.sheldon.schemas import LocalOut, ObservationDraft, ReviseOp, ReviseOut, VerdictDraft  # noqa: E402
from sheldon.methods.sheldon.state import SHELVED, TESTED, State  # noqa: E402

from bmb_sheldon import driver, selftest  # noqa: E402
from bmb_sheldon.adapted import keep_answer_rivals as kar  # noqa: E402
from bmb_sheldon.adapted import keep_neutral_hypotheses as knh  # noqa: E402


def make_tested(s: State, text: str, verdicts=()):
    h = s.add(text, "", "", 1)
    s.tested(h, 1)
    for relation in verdicts:
        s.observe(f"{relation} on {h.id} {len(s.observations)}", "", [], 1, "local",
                  [{"hypothesis": h.id, "relation": relation, "why": ""}])
    return h


def fake_run(s: State, round_i=1, llm=None):
    budget = SimpleNamespace(charge=lambda usage: None)
    return SimpleNamespace(state=s, round=round_i, task=SimpleNamespace(question="Which signature?"),
                           ctx=SimpleNamespace(trace=Trace("t"), llm=llm, budget=budget))


# -- keep_neutral_hypotheses ---------------------------------------------------------


def test_only_exploits_evidence_free_shelvings_are_undone():
    s = State()
    neutral = make_tested(s, "The answer is let-7a.", ["neutral", "neutral"])
    refuted = make_tested(s, "The answer is miR-21.", ["supports", "refutes"])
    by_select = make_tested(s, "The answer is miR-16.")
    earlier = make_tested(s, "The answer is miR-155.")
    for h in (neutral, refuted, by_select):
        s.shelve(h, "shelved by revise" if h is not by_select else "not selected in round 1", 1)
    s.shelve(earlier, "no evidence", 1)
    run = fake_run(s)
    run.ctx.trace.log("exploit.done", round=0, shelve=[earlier.id])  # another round's shelving
    run.ctx.trace.log("exploit.done", round=1, keep=[], shelve=[neutral.id, refuted.id])
    assert knh.apply(run) == [neutral.id]
    assert neutral.status == TESTED and neutral.reason == "" and neutral.touched == 1
    assert refuted.status == by_select.status == earlier.status == SHELVED
    assert run.ctx.trace.of_kind(knh.EVENT)[0]["kept"] == [{"hypothesis": neutral.id,
                                                             "exploit_reason": "shelved by revise"}]
    assert s.tested_by_round[1].count(neutral.id) == 1  # not counted as tested again


def test_nothing_shelved_logs_nothing():
    run = fake_run(State())
    run.ctx.trace.log("exploit.done", round=1, keep=[], shelve=[])
    assert knh.apply(run) == [] and not run.ctx.trace.of_kind(knh.EVENT)


# -- keep_answer_rivals ---------------------------------------------------------------


def lineage():
    """recsvhviava5okg19 attempt 2 in miniature: h1 (SBS3) superseded by its revision h2 (SBS5);
    h3 refined into h4 with the same answer; h5 superseded in an earlier round."""
    s = State()
    h1 = make_tested(s, "SBS3 has the highest contribution.", ["supports", "refutes"])
    h2 = s.refine(h1, "SBS5 has the highest per-sample mean contribution.", "", 1)
    h3 = make_tested(s, "The organism is Bacillus licheniformis.")
    h4 = s.refine(h3, "The organism is B. licheniformis, by ANI.", "", 1)
    h5 = make_tested(s, "SBS1 is highest.")
    s.shelve(h1, f"superseded in its local loop by {h2.id}", 2)
    s.shelve(h3, f"refined into {h4.id}", 2)
    s.shelve(h5, f"superseded in its local loop by {h2.id}", 1)
    make_tested(s, "SBS2 is highest.").status = SHELVED  # no replacement reason
    return s


def test_pairs_are_this_rounds_replacements_only():
    s = lineage()
    assert [(a.id, b.id) for a, b in kar.replaced_pairs(s, 2)] == [("h1", "h2"), ("h3", "h4")]
    prompt = kar.render_prompt("Which signature?", kar.replaced_pairs(s, 2))
    assert "`h1`, replaced by `h2`" in prompt.user and "SBS5 has the highest" in prompt.user
    assert "same_answer" in prompt.system


@pytest.mark.parametrize(("reply", "revive", "why"), [
    (dict(shelved_answer="SBS3", successor_answer="SBS5", same_answer=False), True, "different answer"),
    (dict(shelved_answer="SBS3", successor_answer="SBS3", same_answer=True), False, "same answer"),
    (dict(shelved_answer="", successor_answer="SBS5", same_answer=False), False, "names no answer"),
    (dict(shelved_answer="SBS3.", successor_answer=" sbs3", same_answer=False), False, "read the same"),
])
def test_decide(reply, revive, why):
    s = lineage()
    pairs = kar.replaced_pairs(s, 2)[:1]
    rows = kar.decide(pairs, kar.CompareOut(pairs=[kar.PairOut(shelved="[H1]", successor="h2", **reply)]))
    assert rows[0]["revive"] is revive and why in rows[0]["why"]
    assert kar.decide(pairs, None)[0]["why"] == "no verdict for this pair"


class Replies:
    def __init__(self, parsed=None, error=None):
        self.parsed, self.error, self.calls = parsed, error, []

    async def complete(self, prompt, *, system, schema, tag):
        self.calls.append(tag)
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=self.parsed, usage=Usage(calls=1))


def test_apply_revives_a_changed_answer_only():
    s = lineage()
    llm = Replies(kar.CompareOut(pairs=[
        kar.PairOut(shelved="h1", successor="h2", shelved_answer="SBS3", successor_answer="SBS5", same_answer=False),
        kar.PairOut(shelved="h3", successor="h4", shelved_answer="B. licheniformis",
                    successor_answer="B. licheniformis", same_answer=True)]))
    run = fake_run(s, 2, llm)
    usage = asyncio.run(kar.apply(run))
    assert usage.calls == 1 and llm.calls == [kar.TAG]
    assert s.hypotheses["h1"].status == TESTED and s.hypotheses["h1"].reason == ""
    assert s.hypotheses["h2"].status != SHELVED  # the successor is left as it was, beside its rival
    assert s.hypotheses["h3"].status == SHELVED
    event = run.ctx.trace.of_kind(kar.EVENT)[0]
    assert event["revived"] == ["h1"] and event["pairs"][0]["reason_was"].startswith("superseded")


def test_apply_without_replacements_asks_nothing_and_a_failed_call_changes_nothing():
    llm = Replies()
    assert asyncio.run(kar.apply(fake_run(State(), 1, llm))).calls == 0 and llm.calls == []
    s = lineage()
    run = fake_run(s, 2, Replies(error=RuntimeError("down")))
    asyncio.run(kar.apply(run))
    assert s.hypotheses["h1"].status == SHELVED
    assert run.ctx.trace.of_kind(kar.EVENT)[0]["error"] == "RuntimeError: down"


# -- both, through the driver -----------------------------------------------------------


def scripted(prompt, schema):
    """Round 1: the bacterial claim (h1) is revised to an archaeal one (h3) in its local loop;
    the human claim (h2) gets only a neutral observation and exploit shelves it."""
    if schema is LocalOut:
        under_test = prompt.split("**Hypothesis under test**", 1)[-1][:300]
        neutral = [ObservationDraft(content=f"Looked at {under_test[:40]!r}.",
                                    verdicts=[VerdictDraft(relation="neutral", why="scripted")])]
        if "bacterial" in under_test:
            return LocalOut(notes="16S looks archaeal", done=True, observations=neutral,
                            statement="The sequences are archaeal.", revision_basis="evidence",
                            rationale="the 16S hit is archaeal", predicts_improvement=True)
        return LocalOut(notes="checked", done=True, observations=neutral)
    if schema is ReviseOut:
        return ReviseOut(ops=[ReviseOp(hypothesis="h2", op="shelve", rationale="no evidence for it")])
    if schema is kar.CompareOut:
        return kar.CompareOut(pairs=[kar.PairOut(shelved="h1", successor="h3", shelved_answer="bacteria",
                                                 successor_answer="archaea", same_answer=False)])
    return selftest.responder(prompt, schema)


def scripted_tools(prompt, tools):
    for t in tools:
        if t.get("name") == "submit_local":
            return {"name": "submit_local", "input": scripted(prompt, LocalOut).model_dump()}
        if t.get("name") == "submit_revision":  # only round 1 tests anything, so only it revises
            return {"name": "submit_revision", "input": scripted(prompt, ReviseOut).model_dump()}
    return selftest.tool_responder(prompt, tools)


@pytest.mark.parametrize("mode", ["staged", "session"])
def test_driver_keeps_the_rival_and_the_unevidenced_claim(tmp_path, monkeypatch, mode):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, "")
    work = tmp_path / "workspace"
    (work / "data").mkdir(parents=True)
    (work / "data/sample.fa").write_text(">seq\nACGT\n")
    conf = {"sheldon": {"model": "fake", "max_rounds": 3, "max_calls": None, "max_usd": None,
                        "params": {"mode": mode, "rounds": 3, "n_initial": 2, "select_k": 3,
                                   "probes": ["run_shell"]}},
            "adaptations": ["keep_answer_rivals", "keep_neutral_hypotheses"],
            "workdir": str(work), "home": str(tmp_path / "home")}
    model = FakeLLM(responder=scripted, tool_responder=scripted_tools, model="fake")
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))
    assert not result["error"], result["error"]
    assert result["keep_neutral_hypotheses"] == [{"round": 1, "kept": ["h2"]}]
    rivals = [e for e in result["keep_answer_rivals"] if e["revived"]]
    assert rivals and rivals[0]["round"] == 1 and rivals[0]["revived"] == ["h1"]
    assert result["hypotheses"] == {"tested": 3}  # h1 (rival), h2 (kept), h3 (the revision)
