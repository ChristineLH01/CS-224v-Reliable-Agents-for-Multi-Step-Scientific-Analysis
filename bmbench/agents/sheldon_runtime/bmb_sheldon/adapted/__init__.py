"""Adapted SHELDON: baseline SHELDON plus the changes switched on by name.

Each change to SHELDON has a name in ADAPTATIONS and can be switched on alone (to measure
that change) or together with the others (`all`, the adapted SHELDON as a whole). With
none on, the driver runs SHELDON's own method, so the baseline never goes through this
package.

  method.py       the adapted method: SHELDON's run loop, copied verbatim, with one marked
                  hook per change
  early_stop.py   `early_stop`: end the run at a round boundary once the case is closed
  shared_analysis_state.py: attempt-local ledger, handoffs and private test directories
  candidate_scan.py  `candidate_scan`: list the candidate answers the data supports before
                  round 1 (only its offline recall mode, `candidate_scan_only`, so far)
  plan_keeps_question.py  `plan_keeps_question`: undo a plan split whose `situation` lost
                  the question (only `situation` reaches the stages before finalize)
  keep_answer_rivals.py  `keep_answer_rivals`: a claim replaced by one with a different answer
                  stays live as a rival
  keep_neutral_hypotheses.py  `keep_neutral_hypotheses`: exploit may not shelve a claim it has
                  no evidence on
  golden_hypothesis.py  an oracle ablation, not in ADAPTATIONS: armed by the driver from
                  its stdin (agent.golden_answers on the host), so nothing the sandbox can
                  read names it

This module imports nothing from SHELDON, so the registry can be read without it.
bmbench keeps its own copy of the names (bmbench.config.SHELDON_ADAPTATIONS); a test
keeps the two equal.
"""

from __future__ import annotations

from typing import Any

#: Every change implemented so far, in the order they were added.
ADAPTATIONS = ("early_stop", "shared_analysis_state", "candidate_scan", "plan_keeps_question",
               "keep_answer_rivals", "keep_neutral_hypotheses")
#: What `all` means: kept to the changes it meant when the existing `all` configs ran, so
#: they keep their meaning. `candidate_scan` (offline recall only so far) and
#: `plan_keeps_question`, `keep_answer_rivals` and `keep_neutral_hypotheses` are switched on by name.
ALL = ("early_stop", "shared_analysis_state")
#: The SHELDON method name the adapted method is registered under.
METHOD = "sheldon_adapted"


def resolve(names: Any) -> list[str]:
    """The explicit list a config's `adaptations` means: `all` expanded, unknown names
    refused, duplicates dropped, in registry order (so the same set always reads the same)."""
    if isinstance(names, str):
        names = [names]
    given = [str(n).strip() for n in (names or []) if str(n).strip()]
    unknown = [n for n in given if n != "all" and n not in ADAPTATIONS]
    if unknown:
        raise ValueError(f"unknown SHELDON adaptation(s): {', '.join(unknown)}; "
                         f"known: {', '.join(ADAPTATIONS)} (or all)")
    wanted = set(given) - {"all"} | (set(ALL) if "all" in given else set())
    return [n for n in ADAPTATIONS if n in wanted]
