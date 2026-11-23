"""`keep_answer_rivals`: a claim replaced by one with a different answer stays live as a rival.

SHELDON keeps one live hypothesis per line. When a local loop revises its claim, the claims
it revised from are shelved as "superseded in its local loop by hN"; when exploit refines a
claim, the parent is shelved as "refined into hN". That is right when the revision sharpens
the same answer, and wrong when it changes the answer: in recsvhviava5okg19 the local loop
turned "SBS3 is highest" into "SBS5 has the highest per-sample mean", SBS3 was shelved, and
the remaining rounds only confirmed SBS5. SBS3 was the answer.

After each round, every hypothesis shelved in that round for one of those two reasons is
paired with the claim that replaced it. One tool-less call (`CompareOut`) reports, per pair,
the answer each claim gives and whether they are the same. Code decides: a shelved claim that
names a specific answer, different from its successor's, is put back as a kept (tested)
hypothesis, a live rival in later rounds and in finalize. A call that fails changes nothing.
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from sheldon.core.types import Usage
from sheldon.methods.sheldon.state import SHELVED, TESTED, Hypothesis, State
from sheldon.methods.stage import Prompt, split_source

NAME = "keep_answer_rivals"
TAG = "keep_answer_rivals"
EVENT = "keep_answer_rivals.check"
#: the reasons SHELDON writes when a claim is replaced by another (candidate_analysis._consolidate
#: and stages.apply_exploit)
REPLACED = (re.compile(r"^superseded in its local loop by (h\d+)$"), re.compile(r"^refined into (h\d+)$"))

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)), undefined=StrictUndefined, autoescape=False,
                   trim_blocks=True, lstrip_blocks=True)  # the settings of SHELDON's own prompts.py


class PairOut(BaseModel):
    shelved: str = Field(default="", description="Id of the shelved claim, as given")
    successor: str = Field(default="", description="Id of the claim that replaced it, as given")
    shelved_answer: str = Field(default="", description="The answer the shelved claim gives, or empty")
    successor_answer: str = Field(default="", description="The answer the replacing claim gives, or empty")
    same_answer: bool = Field(default=True, description="Whether both commit to the same single answer")


class CompareOut(BaseModel):
    pairs: list[PairOut] = Field(default_factory=list)


def replaced_pairs(state: State, round_i: int) -> list[tuple[Hypothesis, Hypothesis]]:
    """(shelved claim, its replacement) for each claim shelved this round as replaced."""
    out = []
    for h in state.hypotheses.values():
        if h.status != SHELVED or h.touched != round_i:
            continue
        for pattern in REPLACED:
            m = pattern.match(h.reason.strip())
            successor = state.hypotheses.get(m.group(1)) if m else None
            if successor is not None and successor.id != h.id:
                out.append((h, successor))
                break
    return out


def render_prompt(question: str, pairs: list[tuple[Hypothesis, Hypothesis]]) -> Prompt:
    source = (PROMPT_DIR / "keep_answer_rivals.prompt").read_text(encoding="utf-8")
    sections = {k: _env.from_string(v) for k, v in split_source(source).items()}
    context = dict(question=question.strip(), pairs=[{"shelved": a, "successor": b} for a, b in pairs])
    return Prompt(system=sections["system"].render(**context).strip(),
                  user=sections["user"].render(**context).strip())


def _id(text: str) -> str:
    m = re.fullmatch(r"\W*([hH]\d+)\W*", str(text or "").strip())
    return m.group(1).lower() if m else str(text or "").strip()


def _norm(answer: str) -> str:
    return re.sub(r"[\s.]+", " ", answer).strip().lower()


def decide(pairs: list[tuple[Hypothesis, Hypothesis]], out: CompareOut | None) -> list[dict[str, Any]]:
    """One row per pair. `revive` only for a shelved claim that names an answer, which the
    reply says differs from its successor's and which does not read the same once case and
    spacing are ignored. A pair the reply leaves out is not revived."""
    replies = {(_id(p.shelved), _id(p.successor)): p for p in (out.pairs if out else [])}
    rows = []
    for shelved, successor in pairs:
        p = replies.get((shelved.id, successor.id))
        row: dict[str, Any] = {"shelved": shelved.id, "successor": successor.id, "revive": False}
        if p is None:
            row["why"] = "no verdict for this pair"
        else:
            row.update(shelved_answer=p.shelved_answer.strip(), successor_answer=p.successor_answer.strip(),
                       same_answer=p.same_answer)
            if p.same_answer:
                row["why"] = "same answer"
            elif not p.shelved_answer.strip():
                row["why"] = "the shelved claim names no answer"
            elif _norm(p.shelved_answer) == _norm(p.successor_answer):
                row["why"] = "the two answers read the same"
            else:
                row.update(revive=True, why="different answer")
        rows.append(row)
    return rows


async def apply(run: Any) -> Usage:
    """Check the round's replacements and put back the ones that changed the answer. Never
    raises: a check that fails leaves SHELDON's shelving as it was."""
    ctx, s, r = run.ctx, run.state, run.round
    pairs = replaced_pairs(s, r)
    if not pairs:
        return Usage()
    record: dict[str, Any] = {"round": r}
    usage = Usage()
    try:
        prompt = render_prompt(run.task.question, pairs)
        res = await ctx.llm.complete(prompt.user, system=prompt.system, schema=CompareOut, tag=TAG)
        ctx.budget.charge(res.usage)
        usage = res.usage
        out = res.parsed if isinstance(res.parsed, CompareOut) else None
        rows = decide(pairs, out)
        for row in rows:
            if row["revive"]:
                h = s.hypotheses[row["shelved"]]
                row["reason_was"] = h.reason
                # Kept, not re-tested: it was tested when it was replaced.
                h.status, h.touched, h.reason = TESTED, r, ""
        record.update(pairs=rows, revived=[row["shelved"] for row in rows if row["revive"]])
    except Exception as e:  # noqa: BLE001 - SHELDON's own shelving stands
        record.update(pairs=[{"shelved": a.id, "successor": b.id, "revive": False} for a, b in pairs], revived=[],
                      error=f"{type(e).__name__}: {e}", trace=traceback.format_exc(limit=3))
    ctx.trace.log(EVENT, **record)
    return usage
