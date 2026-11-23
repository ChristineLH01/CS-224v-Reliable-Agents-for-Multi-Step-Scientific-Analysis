"""`plan_keeps_question`: a plan split that moved the question out of `situation` is undone.

SHELDON's plan stage splits the statement into `situation` (the problem) and
`deliverable` (the form of the answer). Only `situation` reaches explore, select,
candidate analysis, comparative analysis and exploit (`Run.base_context`); `deliverable`
is joined back in at finalize alone. The plan prompt does not say where the question
itself belongs, and its acceptance test only checks that `situation` is at least 120
characters long, which a BioMysteryBench statement passes on its environment section
alone. So in 7 of the 30 attempts of `runs 2/preview_sheldon_*` the question sentence
went to `deliverable`, and every stage before finalize worked without knowing what was
asked (all of hb024, recaikavdwoimjy3b attempt 3; see
docs/trajectory-audit/runs2-sheldon-loss-analysis.md §3.1).

After the plan stage, this change checks that what the statement asks is still in the
accepted `situation`: every sentence of the `## Question` section that ends in a question
mark, or the section's first sentence if none does ("Identify the transcription factor
..."). Instructions on the answer's form ("Provide the answer as ...") may go to
`deliverable`, as the plan prompt intends. Text is compared after normalizing whitespace,
case and markdown emphasis. If an asked sentence is missing, the split's `situation`
becomes the whole statement
and its `deliverable` is emptied, which is SHELDON's own behavior when it rejects a plan.
The split's `requirements` are kept for finalize. A statement with no `## Question`
heading, or a plan SHELDON already rejected, is left as it is. Every check is logged as
a trace event (EVENT).
"""

from __future__ import annotations

import re
from typing import Any

NAME = "plan_keeps_question"
#: the trace event each check writes
EVENT = "plan_keeps_question.check"

_QUESTION = re.compile(r"^##[ \t]*Question[ \t]*\n(.*?)(?=^##[ \t]|\Z)", re.M | re.S)
_EMPHASIS = re.compile(r"[`*_]")
_SPACE = re.compile(r"\s+")
#: a sentence ends at . ? or ! followed by whitespace; "e.g. Canis" splits too, harmlessly
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


def question_of(statement: str) -> str:
    """The body of the statement's `## Question` section, or "" if it has none."""
    m = _QUESTION.search((statement or "").replace("\r\n", "\n"))  # CRLF: a prompt written on Windows
    return m.group(1).strip() if m else ""


def asked(question: str) -> list[str]:
    """The sentences that say what is asked: those ending in "?", else the first one."""
    sentences = [s.strip() for s in _SENTENCE_END.split(question.strip()) if s.strip()]
    return [s for s in sentences if s.endswith("?")] or sentences[:1]


def normalize(text: str) -> str:
    """Text as compared: markdown emphasis dropped, whitespace collapsed, lowercased. A
    plan that copies the question but drops backticks or rewraps lines still matches."""
    return _SPACE.sub(" ", _EMPHASIS.sub("", text or "")).strip().lower()


def missing(situation: str, question: str) -> list[str]:
    """The asked sentences of `question` that `situation` does not contain."""
    text = normalize(situation)
    return [s for s in asked(question) if normalize(s) not in text]


def apply(run: Any) -> dict[str, Any]:
    """Check the accepted plan after `stages.plan(run)`, and undo the split if `situation`
    lost what is asked. Returns the logged record: `action` is `kept` (the split keeps
    the question), `restored` (the split was undone; `missing` lists the lost sentences),
    `no_plan` (SHELDON already rejected the split) or `no_question` (no `## Question`
    section to check)."""
    statement = run.task.question
    question = question_of(statement)
    plan = run.plan
    lost: list[str] = []
    if plan is None:
        action = "no_plan"
    elif not question:
        action = "no_question"
    else:
        lost = missing(plan.situation, question)
        action = "restored" if lost else "kept"
    record: dict[str, Any] = {"action": action}
    if lost:
        run.plan = plan.model_copy(update={"situation": statement, "deliverable": ""})
        record["missing"] = lost
    run.ctx.trace.log(EVENT, **record)
    return record
