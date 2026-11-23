"""`golden_hypothesis`: an oracle ablation. Round 1 holds one hypothesis that states the answer.

The question: when the correct answer is among SHELDON's round-1 hypotheses from the start,
does the run keep it to the end? The baseline had the answer in round 1 in 18 of 30 attempts
and lost it in 3, and never had it in hb024, hb048 and recaikavdwoimjy3b.

After the round-1 explore turn submits its hypotheses, one isolated call writes a 4th
hypothesis that states the answer, in the style of the 3 the model wrote. It is inserted at a
random position among them, and the model's own `submit_exploration` call in the session's
history is rewritten to hold it, so for the rest of the run it reads as one the model
proposed. Nothing in the run marks it.

What the model must not be able to find, and how it is kept out:

  the answer        reaches the driver on stdin, after the prompt (see `SENTINEL`). The host
                    stdin file is not mounted into the sandbox, and a pipe once read is empty.
                    It is never in the environment (`run_shell` inherits it), the run's
                    config.json, task.json or the trace.
  that it is on     the adaptation is armed in-process by the driver, not named in
                    `adaptations` (which reach config.json and the environment).
  the writing call  runs with the trace switched off, so no prompt naming the answer is
                    written to the trace file in the sandbox.
  the rewrite       the rewritten reply loses its encrypted reasoning (the transport rebuilds
                    it from its text and tool calls), so no reasoning about "3 hypotheses"
                    sits next to a call that holds 4.

What is left: the trace keeps the original explore reply, with 3 hypotheses. A model that read
its own trace file could compare the two. Check the attempt's commands for reads under
~/.sheldon (scripts/audit_golden_hypothesis.py does).

Which hypothesis was injected, and where, is reported on the driver's result line (stdout,
read by the host), never written in the sandbox.

Session mode only: `round_session` below is SHELDON's, copied verbatim, with one marked hook.
"""

from __future__ import annotations

import copy
import json
import random
import traceback
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from sheldon.core.trace import reset_trace, use_trace
from sheldon.core.types import Usage
from sheldon.methods.sheldon import candidate_analysis, stages
from sheldon.methods.sheldon.run import Run, staged
from sheldon.methods.sheldon.schemas import SUBMIT_EXPLORATION, ExploreOut, HypothesisDraft, RegionDraft
from sheldon.methods.sheldon.session import Session
from sheldon.methods.sheldon.state import PENDING
from sheldon.methods.stage import split_source

from bmb_sheldon.transport import ITEMS_BLOCK

NAME = "golden_hypothesis"
TAG = "golden_hypothesis"
#: what separates the prompt from the secret on the driver's stdin
SENTINEL = "\n<<<bmb-golden-hypothesis>>>\n"

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)), undefined=StrictUndefined, autoescape=False,
                   trim_blocks=True, lstrip_blocks=True)  # the settings of SHELDON's own prompts.py

#: the answer to inject, set by `arm`; process memory only
_answer: str | None = None
#: what was done, for the driver's result line
record: dict[str, Any] = {}


def split_stdin(text: str) -> tuple[str, str | None]:
    """(prompt, answer or None) from what the driver read on stdin.

    A stdin file written on Windows has CRLF line ends, which the sentinel would not match:
    the answer would then stay in the prompt. Line ends are normalized first, and any trace
    of the sentinel left in the prompt is an error, so the run stops instead of leaking."""
    text = text.replace("\r\n", "\n")
    prompt, sep, secret = text.partition(SENTINEL)
    if SENTINEL.strip() in prompt:
        raise ValueError("malformed golden-hypothesis sentinel on stdin; refusing to run with it in the prompt")
    if not sep:
        return text, None
    answer = str(json.loads(secret).get("answer") or "").strip()
    return prompt, answer or None


def join_stdin(prompt: str, answer: str) -> str:
    return prompt + SENTINEL + json.dumps({"answer": answer}, ensure_ascii=False)


def arm(answer: str | None) -> None:
    global _answer
    _answer = (answer or "").strip() or None
    record.clear()


def armed() -> bool:
    return _answer is not None


class NewRegion(BaseModel):
    axis: str = Field(default="", description="An axis already on the map")
    name: str = Field(default="", description="Short unique name, in the style of the existing ones")
    description: str = Field(default="", description="What falls in this region")


class WriteOut(BaseModel):
    statement: str = Field(default="", description="The claim, stated so it can be tested")
    rationale: str = Field(default="", description="Why it could be true, and what would show it false")
    region: str = Field(default="", description="An existing hypothesis region's name, or new_region.name")
    new_region: NewRegion | None = Field(default=None, description="Only if no existing region fits")


def render_prompt(question: str, out: ExploreOut, answer: str) -> tuple[str, str]:
    source = (PROMPT_DIR / "golden_hypothesis.prompt").read_text(encoding="utf-8")
    sections = {k: _env.from_string(v) for k, v in split_source(source).items()}
    context = dict(question=question.strip(), answer=answer,
                   regions=json.dumps([r.model_dump() for r in out.hypothesis_regions], indent=1, ensure_ascii=False),
                   hypotheses=json.dumps([h.model_dump() for h in out.hypotheses], indent=1, ensure_ascii=False))
    return sections["system"].render(**context).strip(), sections["user"].render(**context).strip()


def fallback(answer: str) -> WriteOut:
    return WriteOut(statement=f"The answer is {answer}.",
                    rationale="A specific candidate answer; it is refuted if the data point to a different one.",
                    region="", new_region=None)


async def write(run: Any, out: ExploreOut, answer: str) -> tuple[WriteOut, Usage, str]:
    """The hypothesis, written with the trace off. Never raises: a failed call gives the
    plain fallback statement."""
    ctx = run.ctx
    token = use_trace(None)
    try:
        system, user = render_prompt(run.task.question, out, answer)
        res = await ctx.llm.complete(user, system=system, schema=WriteOut, tag=TAG)
        ctx.budget.charge(res.usage)
        w = res.parsed if isinstance(res.parsed, WriteOut) else None
        if w is None or not w.statement.strip():
            return fallback(answer), res.usage, "empty reply"
        return w, res.usage, ""
    except Exception as e:  # noqa: BLE001 - the injection still happens, in its plain form
        return fallback(answer), Usage(), f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2)}"
    finally:
        reset_trace(token)


def place(out: ExploreOut, w: WriteOut, rng: random.Random) -> tuple[ExploreOut, int, dict[str, Any] | None]:
    """`out` with the hypothesis inserted at a random position (and its region, if new)."""
    out = out.model_copy(deep=True)
    names = {r.name.strip() for r in out.hypothesis_regions}
    region = w.region.strip()
    new_region = None
    if region not in names:
        nr = w.new_region
        name = (nr.name.strip() if nr and nr.name.strip() else region) or "Other_candidate_answers"
        axis = (nr.axis.strip() if nr and nr.axis.strip() else "") or next(
            (r.axis for r in out.hypothesis_regions if r.axis.strip()), "answer")
        desc = (nr.description.strip() if nr else "") or f"Answers of the kind: {w.statement.strip()}"
        if name not in names:
            new_region = RegionDraft(axis=axis, name=name, description=desc).model_dump()
            out.hypothesis_regions.insert(rng.randrange(len(out.hypothesis_regions) + 1),
                                          RegionDraft(**new_region))
        region = name
    pos = rng.randrange(len(out.hypotheses) + 1)
    out.hypotheses.insert(pos, HypothesisDraft(statement=w.statement.strip(), rationale=w.rationale.strip(),
                                               region=region))
    return out, pos, new_region


def rewrite_history(messages: list[dict[str, Any]], out: ExploreOut) -> bool:
    """Make the last `submit_exploration` call in the conversation carry `out`, and drop the
    reasoning of that reply. False when the conversation holds no such call (the turn was
    recovered outside it)."""
    name = SUBMIT_EXPLORATION["name"]
    for message in reversed(messages):
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            continue
        blocks = message["content"]
        uses = [b for b in blocks if b.get("type") == "tool_use" and b.get("name") == name]
        if not uses:
            continue
        use = uses[-1]
        new_input = copy.deepcopy(use.get("input") or {})
        new_input["hypotheses"] = [h.model_dump() for h in out.hypotheses]
        new_input["hypothesis_regions"] = [r.model_dump() for r in out.hypothesis_regions]
        use["input"] = new_input
        message["content"] = [b for b in blocks if b.get("type") != ITEMS_BLOCK]
        return True
    return False


async def inject(run: Any, session: Session, asked: Any, rng: random.Random | None = None) -> Usage:
    """Put the golden hypothesis into round 1's explore output and the conversation."""
    out = asked.parsed if isinstance(asked.parsed, ExploreOut) else None
    if out is None or not out.hypotheses or _answer is None:
        record.update(injected=False, why="explore submitted no hypotheses" if _answer else "not armed")
        return Usage()
    rng = rng or random.Random()
    w, usage, error = await write(run, out, _answer)
    placed, pos, new_region = place(out, w, rng)
    asked.parsed = placed
    rewritten = rewrite_history(session.messages, placed)
    record.update(injected=True, position=pos + 1, of=len(placed.hypotheses), statement=w.statement.strip(),
                  rationale=w.rationale.strip(), region=placed.hypotheses[pos].region, new_region=new_region,
                  history_rewritten=rewritten, writer_error=error, writer_usd=round(usage.cost_usd, 6))
    return usage


def report(belief: dict[str, Any] | None) -> dict[str, Any]:
    """`record`, plus the id the hypothesis got and where it ended up."""
    out = dict(record)
    if out.get("injected"):
        for h in (belief or {}).get("hypotheses") or []:
            if str(h.get("statement", "")).strip() == out.get("statement"):
                out.update(id=h.get("id"), final_status=h.get("status"))
                break
    return out


async def round_session(run: Run) -> tuple[Usage, bool]:
    engine = run.ctx.get_engine()
    if getattr(engine, "brings_own_tools", False) or not getattr(engine, "uses_tools", True):
        raise ValueError(
            f"sheldon mode=session needs a native tool-loop engine, not {engine.name!r}; "
            "use candidate_analysis_engine to put the local loops on it instead"
        )
    session = Session(run)
    usage = Usage()
    # A full map is settled before explore proposes anything: one call with
    # the model's whole attention on which regions to fold, outside the
    # session's conversation in both modes, so the merge is one decision.
    for spaces in ([stages.merge_needed(run)] if stages.merge_needed(run) else []):
        asked = await staged(run, stages.merge_step(run, spaces))
        usage += asked.usage
        stages.apply_merge(run, asked.parsed, spaces)
    step = stages.explore_step(run)
    session.seen_all()  # the explore prompt carries the whole state
    asked = await session.turn(step)
    usage += asked.usage
    # >>> adaptation: golden_hypothesis
    if run.round == 1 and armed():
        usage += await inject(run, session, asked)
    # <<< adaptation: golden_hypothesis
    stages.apply_explore(run, asked.parsed, asked.retrievals)
    if stages.select_needed(run):
        asked = await session.turn(session.select_step())
        usage += asked.usage
        chosen = stages.apply_select(run, asked.parsed)
    else:
        chosen = [h.id for h in run.state.of(PENDING)]
    chosen = chosen or stages.resumed_targets(run)
    if not chosen:
        return usage, False
    usage += await candidate_analysis.run_all(run, chosen)
    asked = await session.turn(session.comparative_analysis_step())
    usage += asked.usage
    stages.apply_comparative_analysis(run, asked.parsed, asked.retrievals)
    asked = await session.turn(session.exploit_step())
    usage += asked.usage
    stages.apply_exploit(run, asked.parsed)
    return usage, True


def without_hook(source: str) -> str:
    """`round_session`'s source with the marked block taken out: what must equal SHELDON's."""
    from bmb_sheldon.adapted.method import loop_without_adaptations  # noqa: PLC0415

    return loop_without_adaptations(source)
