"""Oracle ablation for agents without hypothesis rounds (Codex, Claude Code): the same four
round-1 hypotheses a SHELDON golden_hypothesis run had (its model's three plus the injected
one that states the correct answer, in the same order), put into the problem prompt as
unverified candidates from a preliminary exploration. Nothing marks which one is injected.

Attempt i of a problem gets the hypotheses of attempt i of that problem in the source run
(`agent.golden_hypotheses_from`). The prompt with them goes only into a host-only stdin
file of the attempt; which one is the golden hypothesis is written next to it, in
golden_hint.json, which the sandbox never sees.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

from bmbench.config import resolve

#: the prompt section the candidates go in front of
ANCHOR = "\n## Data and environment\n"

HEADER = (
    "## Candidate hypotheses\n\n"
    "A preliminary exploration of this data proposed the hypotheses below. None of them has been "
    "verified: any, all or none of them may be right. Weigh them against the data as you see fit.\n"
)


def round1_hypotheses(source_run: str | Path, problem_id: str, attempt: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The four round-1 hypotheses one attempt of a SHELDON golden_hypothesis run showed its
    model, in order, and that attempt's golden_hypothesis record (statement, position).

    The final belief is not enough: SHELDON may fold a round-1 hypothesis into another as a
    duplicate. The trace keeps the model's original explore reply (its own three); the
    golden one goes back in at the position the driver reported."""
    adir = resolve(source_run) / "problems" / problem_id / f"attempt-{attempt}"
    record: dict[str, Any] = {}
    for line in (adir / "stream.main.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            record = event.get("golden_hypothesis") or {}
    if not record.get("injected"):
        raise ValueError(f"{adir}: no injected golden hypothesis in its result")
    traces = sorted(adir.glob("agent_session/sheldon/runs/*/traces/*.jsonl.gz"))
    if not traces:
        raise FileNotFoundError(f"no SHELDON trace under {adir}")
    own: list[dict[str, Any]] | None = None
    with gzip.open(traces[0], "rt", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            if event.get("kind") == "explore.done":
                break
            if event.get("kind") != "llm" or event.get("tag") != "session.explore":
                continue
            for call in event.get("tool_use") or []:
                if call.get("name") == "submit_exploration":
                    own = list((call.get("input") or {}).get("hypotheses") or [])  # the last one counts
    if not own:
        raise ValueError(f"{adir}: no round-1 submit_exploration in the trace")
    pos = int(record.get("position") or 0)
    if not 1 <= pos <= len(own) + 1 or int(record.get("of") or 0) != len(own) + 1:
        raise ValueError(f"{adir}: golden position {pos} of {record.get('of')} does not fit {len(own)} own hypotheses")
    golden = {"statement": record.get("statement"), "rationale": record.get("rationale"), "golden": True}
    return own[:pos - 1] + [golden] + own[pos - 1:], record


def render(hyps: list[dict[str, Any]]) -> str:
    items = []
    for i, h in enumerate(hyps, 1):
        items.append(f"{i}. {str(h.get('statement') or '').strip()}\n   Rationale: {str(h.get('rationale') or '').strip()}")
    return HEADER + "\n" + "\n\n".join(items) + "\n"


def with_hints(prompt: str, hyps: list[dict[str, Any]]) -> str:
    block = render(hyps)
    if ANCHOR in prompt:
        return prompt.replace(ANCHOR, "\n" + block + ANCHOR, 1)
    return prompt.rstrip("\n") + "\n\n" + block


def main_stdin(source_run: str | Path, problem_id: str, prompt_path: Path, attempt_dir: Path) -> Path:
    """The attempt's stdin file: the prompt with that attempt's four candidates."""
    m = re.fullmatch(r"attempt-(\d+)", attempt_dir.name)
    if not m:
        raise ValueError(f"cannot tell the attempt number from {attempt_dir}")
    hyps, record = round1_hypotheses(source_run, problem_id, int(m.group(1)))
    path = attempt_dir / "stdin.main.txt"
    path.write_text(with_hints(prompt_path.read_text(encoding="utf-8"), hyps), encoding="utf-8", newline="\n")
    (attempt_dir / "golden_hint.json").write_text(json.dumps({
        "source_run": str(source_run), "source_attempt": int(m.group(1)), "position": record.get("position"),
        "of": len(hyps), "statement": record.get("statement"), "sheldon_id": record.get("id"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
