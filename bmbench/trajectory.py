"""Agent-agnostic trajectory model: answer extraction, readable transcript, action log.

An agent adapter turns its own output format (Claude Code's stream-json, later
Codex's JSONL) into a list of `Step`s per invocation ("segment"). Everything
downstream -- the transcript, the cheating audit, the report -- reads only this.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)


@dataclass
class Step:
    kind: str  # text | thinking | tool_use | tool_result | system | result
    text: str = ""
    tool: str | None = None
    tool_input: Any = None
    tool_use_id: str | None = None
    is_error: bool = False
    parent: str | None = None  # tool_use id of the subagent call this belongs to, if any


@dataclass
class Segment:
    """One invocation of the agent: the main run, a nudge, or the finalize turn."""

    kind: str  # main | nudge<k> | finalize
    exit_code: int = 0
    timed_out: bool = False
    seconds: float = 0.0
    session_id: str | None = None
    result_subtype: str | None = None
    is_error: bool | None = None
    terminal_reason: str | None = None
    api_error_status: Any = None
    final_text: str = ""
    cost_usd: float = 0.0
    num_turns: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] = field(default_factory=dict)
    rate_limit: dict[str, Any] = field(default_factory=dict)  # last rate-limit status seen
    error_text: str = ""
    steps: list[Step] = field(default_factory=list)

    @property
    def got_result(self) -> bool:
        return self.result_subtype is not None

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("steps")
        d["n_steps"] = len(self.steps)
        d["n_tool_calls"] = sum(1 for s in self.steps if s.kind == "tool_use")
        return d


def extract_answer(text: str | None) -> str | None:
    """The last <answer>…</answer> in `text`, stripped; None if there is none."""
    if not text:
        return None
    found = ANSWER_RE.findall(text)
    if not found:
        return None
    answer = found[-1].strip()
    return answer or None


def final_answer(segments: list[Segment]) -> tuple[str | None, str]:
    """(answer, final message). The latest segment's final text wins; if it has no
    tag, fall back to the latest tagged main-thread text anywhere in the run."""
    final_text = ""
    for seg in reversed(segments):
        text = seg.final_text or _last_text(seg.steps)
        if text:
            final_text = text
            break
    answer = extract_answer(final_text)
    if answer is None:
        for seg in reversed(segments):
            for step in reversed(seg.steps):
                if step.kind == "text" and step.parent is None:
                    answer = extract_answer(step.text)
                    if answer:
                        return answer, final_text
    return answer, final_text


def _last_text(steps: list[Step]) -> str:
    for step in reversed(steps):
        if step.kind == "text" and step.parent is None and step.text.strip():
            return step.text
    return ""


# -- rendering -----------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more chars]"


def describe_tool_input(tool: str | None, data: Any) -> str:
    """One readable string per tool call: the command, the URL, the path…"""
    if not isinstance(data, dict):
        return _clip(json.dumps(data, ensure_ascii=False), 4000)
    if tool == "Bash" and "command" in data:
        extra = " [background]" if data.get("run_in_background") else ""
        return f"{data['command']}{extra}"
    if tool in ("WebFetch",) and "url" in data:
        return f"{data['url']}  (prompt: {data.get('prompt', '')})"
    if tool in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit") and (
        p := data.get("file_path") or data.get("notebook_path")
    ):
        if tool == "Write":
            return f"{p}\n{_clip(str(data.get('content', '')), 3000)}"
        if tool in ("Edit", "MultiEdit"):
            return f"{p}\n{_clip(json.dumps({k: v for k, v in data.items() if k != 'file_path'}, ensure_ascii=False), 3000)}"
        return p
    if tool in ("Task", "Agent"):
        return f"[{data.get('subagent_type', 'agent')}] {data.get('description', '')}\n{data.get('prompt', '')}"
    return json.dumps(data, ensure_ascii=False)


def render_transcript(task_id: str, trial: int, segments: list[Segment], tool_result_chars: int = 2500) -> str:
    lines = [f"# Transcript: {task_id} / trial {trial}", ""]
    for seg in segments:
        lines += [
            f"## Segment `{seg.kind}`: exit {seg.exit_code}"
            + (" (timed out)" if seg.timed_out else "")
            + f", {seg.seconds:.0f}s, {seg.num_turns} turns, ${seg.cost_usd:.2f}",
            "",
        ]
        for step in seg.steps:
            who = f" (subagent {step.parent[:8]})" if step.parent else ""
            if step.kind == "text":
                lines += [f"**assistant{who}:**", "", step.text.strip(), ""]
            elif step.kind == "thinking":
                if step.text.strip():
                    lines += [f"<details><summary>thinking{who}</summary>", "", step.text.strip(), "", "</details>", ""]
            elif step.kind == "tool_use":
                body = describe_tool_input(step.tool, step.tool_input)
                lines += [f"**→ {step.tool}{who}**", "", "```", _clip(body, 6000), "```", ""]
            elif step.kind == "tool_result":
                tag = "error" if step.is_error else "result"
                lines += [f"<details><summary>← {tag}{who}</summary>", "", "```", _clip(step.text, tool_result_chars), "```", "", "</details>", ""]
            elif step.kind == "system" and step.text:
                lines += [f"_system: {step.text}_", ""]
        if seg.error_text:
            lines += ["**error:**", "", "```", _clip(seg.error_text, 3000), "```", ""]
    return "\n".join(lines)


def action_log(segments: list[Segment], max_chars: int, result_chars: int = 300) -> str:
    """Every tool call, in order, with a short excerpt of its result (for the audit)."""
    results: dict[str, Step] = {}
    for seg in segments:
        for step in seg.steps:
            if step.kind == "tool_result" and step.tool_use_id:
                results[step.tool_use_id] = step
    entries: list[str] = []
    n = 0
    for seg in segments:
        for step in seg.steps:
            if step.kind != "tool_use":
                continue
            n += 1
            who = " (subagent)" if step.parent else ""
            body = _clip(describe_tool_input(step.tool, step.tool_input), 1500)
            res = results.get(step.tool_use_id or "")
            excerpt = _clip(res.text, result_chars) if res is not None else ""
            entries.append(f"[{n}] {step.tool}{who}: {body}" + (f"\n    ⇒ {excerpt}" if excerpt else ""))
    text = "\n".join(entries)
    if len(text) > max_chars:
        head, tail = text[: max_chars * 2 // 3], text[-max_chars // 3 :]
        text = f"{head}\n… [{len(text) - max_chars} chars of the middle omitted] …\n{tail}"
    return text or "(no tool calls)"


def tool_inputs_text(segments: list[Segment]) -> str:
    """Concatenated tool inputs only (what the agent *did*), for regex flagging."""
    return "\n".join(
        describe_tool_input(s.tool, s.tool_input)
        for seg in segments
        for s in seg.steps
        if s.kind == "tool_use"
    )


def tool_counts(segments: list[Segment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for seg in segments:
        for s in seg.steps:
            if s.kind == "tool_use" and s.tool:
                counts[s.tool] = counts.get(s.tool, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
