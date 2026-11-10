"""`early_stop`: end the run at a round boundary once the case is closed.

SHELDON's own convergence rules need a score during the run, and BioMysteryBench has
none (the answer is graded on the host afterwards). This change asks, after round r's
exploit stage and only when another round would follow, whether the record already
closes the case the way a differential diagnosis is closed: the lead answers what was
asked, data evidence establishes it, every rival answer was tested and ruled out, and
nothing is left open.

The decision is split like SHELDON's scored tests, where the model proposes and the
harness measures:

  check   one tool-less call (`StopCheckOut`) reads the question, every hypothesis, every
          observation and the round's comparative analysis, and reports facts: the lead,
          its answer, the observations that establish it, which hypotheses are rival
          answers, and anything open.
  gate    fixed code decides from SHELDON's own record (`gate`). The model never says
          whether a rival was ruled out: that is read off the verdict counts.

A check that fails, or that errors, changes nothing: the run goes on as baseline SHELDON
would. A passing check ends the loop with stop reason `settled`, and SHELDON's unchanged
finalize stage writes the answer.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from sheldon.methods.sheldon.state import SHELVED, State
from sheldon.methods.stage import Prompt, split_source

NAME = "early_stop"
STOP_REASON = "settled"
#: the trace event each check writes, and the tag of its model call
EVENT, TAG = "early_stop.check", "early_stop"
#: verified observations the lead's `support` must cite: each a `supports` verdict on the lead
MIN_SUPPORT = 1

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)), undefined=StrictUndefined, autoescape=False,
                   trim_blocks=True, lstrip_blocks=True)  # the settings of SHELDON's own prompts.py


class StopCheckOut(BaseModel):
    lead: str = Field(default="", description="Id of the hypothesis that best answers the question, or empty")
    candidate_answer: str = Field(default="", description="The specific answer the lead states, in the requested "
                                                          "form, or empty")
    covers_question: bool = Field(default=False, description="Whether the candidate answers every part of the "
                                                             "question")
    support: list[str] = Field(default_factory=list, description="Ids of observations that establish the "
                                                                 "candidate directly")
    rivals: list[str] = Field(default_factory=list, description="Ids of hypotheses that state a different answer")
    untested_rivals: list[str] = Field(default_factory=list, description="Other specific answers the record "
                                                                         "mentions that no hypothesis states")
    open_issues: list[str] = Field(default_factory=list, description="Anything unresolved that could change the "
                                                                     "answer")
    notes: str = Field(default="", description="One or two sentences on where the case stands")


def render_prompt(question: str, state: State, summary: str) -> Prompt:
    """Every hypothesis (shelved ones too: rivals are usually shelved by now), rendered
    and ordered as SHELDON renders them, with every observation and its verdicts."""
    source = (PROMPT_DIR / "early_stop.prompt").read_text(encoding="utf-8")
    sections = {k: _env.from_string(v) for k, v in split_source(source).items()}
    context = dict(question=question.strip(),
                   hypotheses="\n".join(state.render_hypothesis(h) for h in state.hypotheses.values()) or "(none)",
                   observations=state.render_observations(), summary=(summary or "").strip())
    return Prompt(system=sections["system"].render(**context).strip(), user=sections["user"].render(**context).strip())


def _id(text: str) -> str:
    """`h3`, `[h3]`, ` H3 ` -> `h3`."""
    m = re.fullmatch(r"\[?\s*([hHoO]\d+)\s*\]?", str(text or "").strip())
    return m.group(1).lower() if m else str(text or "").strip()


def _lines(items: list[str]) -> list[str]:
    return [s.strip() for s in items if str(s).strip()]


@dataclass
class GateResult:
    passed: bool
    #: one line per reason it did not pass, by condition
    failed: list[str] = field(default_factory=list)
    #: SHELDON's (supporting, refuting) counts for the lead and each rival, as read
    evidence: dict[str, list[int]] = field(default_factory=dict)
    #: cited `support` ids that are not a `supports` verdict on the lead: not counted
    ignored: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failed": self.failed, "evidence": self.evidence, "ignored": self.ignored}


def gate(state: State, out: StopCheckOut | None, min_support: int = MIN_SUPPORT) -> GateResult:
    """All four conditions, checked against SHELDON's own state:

    1. answer    a candidate answer that covers the whole question
    2. support   the lead is a live hypothesis with no refuting observation, and `support`
                 cites at least `min_support` observations that carry a `supports` verdict on
                 it; a cited id that does not (it refutes a rival, say) is not counted
    3. rivals    at least one rival hypothesis, every rival ruled out (more refuting than
                 supporting observations: negative net, the count SHELDON ranks by), and no
                 untested rival answer
    4. open      no open issue
    """
    if out is None:
        return GateResult(False, ["check: no usable reply"])
    failed: list[str] = []
    evidence: dict[str, list[int]] = {}
    ignored: list[str] = []

    if not out.candidate_answer.strip():
        failed.append("answer: no candidate answer")
    elif not out.covers_question:
        failed.append("answer: the candidate does not cover the whole question")

    lead = state.hypotheses.get(_id(out.lead))
    if lead is None:
        failed.append(f"support: the lead {out.lead.strip() or '(none)'} is not a hypothesis")
    else:
        sup, ref = state.counts(lead.id)
        evidence[lead.id] = [sup, ref]
        if lead.status == SHELVED:
            failed.append(f"support: the lead {lead.id} is shelved")
        if ref:
            failed.append(f"support: the lead {lead.id} has {ref} refuting observation(s)")
        cited = list(dict.fromkeys(_id(o) for o in _lines(out.support)))
        verified = [o for o in cited if o in state.observations
                    and state.observations[o].verdicts.get(lead.id, {}).get("relation") == "supports"]
        ignored = [o for o in cited if o not in verified]
        if len(verified) < min_support:
            failed.append(f"support: {len(verified)} verified supporting observation(s) of {lead.id} cited, "
                          f"{min_support} needed")

    rivals = list(dict.fromkeys(_id(h) for h in _lines(out.rivals)))
    if not rivals:
        failed.append("rivals: no rival hypothesis")
    for hid in rivals:
        h = state.hypotheses.get(hid)
        if h is None:
            failed.append(f"rivals: {hid} is not a hypothesis")
            continue
        if lead is not None and h.id == lead.id:
            failed.append(f"rivals: {hid} is the lead")
            continue
        sup, ref = state.counts(h.id)
        evidence[h.id] = [sup, ref]
        if ref <= sup:
            failed.append(f"rivals: {h.id} not ruled out (+{sup} -{ref})")
    failed += [f"rivals: untested: {r}" for r in _lines(out.untested_rivals)]
    failed += [f"open: {r}" for r in _lines(out.open_issues)]
    return GateResult(not failed, failed, evidence, ignored)


def comparative_summary(trace: Any, round_i: int) -> str:
    """The round's comparative-analysis summary. SHELDON logs it (cut to 600 characters)
    but does not keep it in its state."""
    for e in reversed(getattr(trace, "events", None) or []):
        if e.get("kind") == "comparative_analysis.done" and e.get("round") == round_i:
            return str(e.get("summary") or "")
    return ""


async def check(run: Any) -> dict[str, Any]:
    """Ask, gate, and log one check for the round that just closed. Never raises: a check
    that fails to run is a check that did not pass."""
    ctx = run.ctx
    record: dict[str, Any] = {"round": run.round}
    try:
        prompt = render_prompt(run.task.question, run.state, comparative_summary(ctx.trace, run.round))
        res = await ctx.llm.complete(prompt.user, system=prompt.system, schema=StopCheckOut, tag=TAG)
        ctx.budget.charge(res.usage)
        out = res.parsed if isinstance(res.parsed, StopCheckOut) else None
        record["check"] = out.model_dump() if out is not None else None
        record.update(gate(run.state, out).to_dict())
    except Exception as e:  # noqa: BLE001 - the run goes on as if the check had not passed
        record.update(passed=False, failed=[f"check: {type(e).__name__}: {e}"], evidence={},
                      error=traceback.format_exc(limit=3))
    ctx.trace.log(EVENT, **record)
    return record
