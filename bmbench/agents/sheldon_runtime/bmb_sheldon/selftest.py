"""A scripted model for `driver --selftest`: the whole SHELDON loop, offline.

SHELDON ships `FakeLLM` for exactly this ("a wiring bug caught here is free"). This
script drives it through one real round on a BioMysteryBench-shaped task: plan,
explore (two hypotheses), a candidate analysis per hypothesis that runs one real
`run_shell` command in the workspace, comparative analysis, exploit, then a second
round with nothing new (the loop stops on its own rule), and finalize.
"""

from __future__ import annotations

import re
from typing import Any

from sheldon.core.llm import FakeLLM
from sheldon.methods.sheldon.schemas import (
    SUBMIT_SCHEMAS,
    EvidenceOut,
    ExploreOut,
    FinalOut,
    HypothesisDraft,
    LocalOut,
    MergeOut,
    ObservationDraft,
    PlanOut,
    RegionDraft,
    ReviseOut,
    SelectOut,
    VerdictDraft,
)

MARK = "BMB_SELFTEST_MARK"
ANSWER = "<answer>selftest-answer</answer>"
COMMAND = (f"echo {MARK} $(ls data | wc -l | tr -d ' ') files in $(pwd); "
           "echo oom_score_adj=$(cat /proc/self/oom_score_adj 2>/dev/null || echo none)")
QUESTION = "Which organism do the sequences in the data folder come from? (bmbench selftest)"

_PLAN = re.compile(r"^---\n(.*?)\n---$", re.M | re.S)


def responder(prompt: str, schema: Any) -> Any:
    if schema is PlanOut:
        m = _PLAN.search(prompt)
        return PlanOut(situation=m.group(1) if m else prompt)
    if schema is MergeOut:
        return MergeOut()
    if schema is ExploreOut:
        if "**Current hypotheses**" in prompt:
            return ExploreOut()  # round 2: nothing new, so the loop stops on its own rule
        return ExploreOut(
            hypothesis_regions=[RegionDraft(axis="taxon", name="bacteria", description="a bacterial genome"),
                                RegionDraft(axis="taxon", name="eukaryotes", description="a eukaryotic genome")],
            observation_regions=[RegionDraft(axis="evidence", name="file contents",
                                             description="what the data files hold")],
            hypotheses=[HypothesisDraft(statement="The sequences are bacterial.", rationale="selftest",
                                        region="bacteria"),
                        HypothesisDraft(statement="The sequences are human.", rationale="selftest",
                                        region="eukaryotes")],
        )
    if schema is LocalOut:
        # a verdict naming no hypothesis is filed on the one under test
        return LocalOut(notes="listed the data", done=True, observations=[ObservationDraft(
            content="The data folder holds one file.", region="file contents",
            verdicts=[VerdictDraft(hypothesis="", relation="neutral", why="selftest")])])
    if schema is EvidenceOut:
        return EvidenceOut(summary="selftest")
    if schema is SelectOut:
        return SelectOut()
    if schema is ReviseOut:
        return ReviseOut()
    if schema is FinalOut:
        return FinalOut(answer=ANSWER, confidence=0.5)
    return "ok"


def tool_responder(prompt: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    names = {t.get("name") for t in tools}
    if MARK not in prompt and "run_shell" in names and "**Hypothesis under test**" in prompt:
        return {"name": "run_shell", "input": {"command": COMMAND}}
    for name, schema in SUBMIT_SCHEMAS.items():
        if name in names:
            return {"name": name, "input": responder(prompt, schema).model_dump()}
    return None


def fake_llm(**kwargs: Any) -> FakeLLM:
    return FakeLLM(responder=responder, tool_responder=tool_responder, **kwargs)
