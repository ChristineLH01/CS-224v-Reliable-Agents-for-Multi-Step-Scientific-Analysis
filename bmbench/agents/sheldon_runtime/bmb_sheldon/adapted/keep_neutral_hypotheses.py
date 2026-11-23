"""`keep_neutral_hypotheses`: exploit may not shelve a hypothesis it has no evidence on.

SHELDON's exploit stage shelves whatever it decides to, including a hypothesis whose every
observation is `neutral`: one that could not be checked (a database out of reach, say), not
one the evidence went against. In hb035 that is how a correct round-1 answer (let-7a) was
lost: "no evidence for any part of the claim, shelve it".

After each round, every hypothesis exploit shelved in that round that has no supporting and
no refuting observation, and no score, is put back as a kept (tested) hypothesis. It carries
into later rounds and into finalize like any kept claim, and exploit can still repair it or,
once evidence arrives, shelve it. Shelving by select (not chosen) and by a local loop's
consolidation are left alone: those are not judgements on the claim's evidence.
"""

from __future__ import annotations

from typing import Any

from sheldon.methods.sheldon.state import SHELVED, TESTED

NAME = "keep_neutral_hypotheses"
EVENT = "keep_neutral_hypotheses.kept"


def shelved_by_exploit(trace: Any, round_i: int) -> list[str]:
    """The ids the round's exploit stage shelved, from its `exploit.done` event."""
    ids: list[str] = []
    for e in trace.of_kind("exploit.done"):
        if e.get("round") == round_i:
            ids = list(e.get("shelve") or [])
    return ids


def apply(run: Any) -> list[str]:
    """Put back the round's evidence-free shelvings; return the ids put back."""
    s, r = run.state, run.round
    kept = []
    for hid in shelved_by_exploit(run.ctx.trace, r):
        h = s.hypotheses.get(hid)
        if h is None or h.status != SHELVED or h.scores or s.counts(h.id) != (0, 0):
            continue
        kept.append({"hypothesis": h.id, "exploit_reason": h.reason[:400]})
        # Kept, not re-tested: `tested_by_round` is left as it was, so explore is not told
        # this claim was tested again.
        h.status, h.touched, h.reason = TESTED, r, ""
    if kept:
        run.ctx.trace.log(EVENT, round=r, kept=kept)
    return [k["hypothesis"] for k in kept]
