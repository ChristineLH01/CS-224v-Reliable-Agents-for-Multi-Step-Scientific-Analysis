"""Claude Code (`claude -p`) as the agent under test, run unchanged inside the sandbox.

The invocation is plain headless Claude Code: its own system prompt, tools,
context compaction and subagents. The harness adds only what headless operation
requires (no permission prompts, machine-readable output, a session id so a
timed-out run can be resumed for a final answer) and a few evaluation controls
(model/effort, WebSearch off, Bash timeouts).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bmbench.agents.base import Agent
from bmbench.config import SECRET_ENV
from bmbench.trajectory import Segment, Step

#: exit status of coreutils `timeout` when the limit fired (137 if it had to SIGKILL; an
#: out-of-memory kill also ends in 137, and the runner tells the two apart)
TIMEOUT_EXIT_CODES = (124, 137)
#: system events that carry no information for a transcript (progress ticks, the init header)
_QUIET_SYSTEM = {None, "init", "thinking_tokens", "status"}


class ClaudeCodeAgent(Agent):
    name = "claude_code"
    session_dirs = ("/home/agent/.claude/projects",)  # the session logs, not settings or backups
    secret_env = SECRET_ENV

    @property
    def infra_domains(self) -> tuple[str, ...]:
        return ("api.anthropic.com",)

    def command(self, *, timeout_s: int, session_id: str, resume: bool = False,
                budget_usd: float | None = None) -> list[str]:
        c = self.cfg
        argv = [
            "timeout", "-k", "30", str(max(1, int(timeout_s))),
            "claude", "-p",
            "--model", c.model,
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", c.permission_mode,
        ]
        if c.effort:
            argv += ["--effort", c.effort]
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
        budget = budget_usd if budget_usd is not None else c.max_budget_usd
        if budget is not None:
            argv += ["--max-budget-usd", f"{max(0.01, budget):.2f}"]
        argv += list(c.extra_args)
        if c.disallowed_tools:
            # Variadic option: keep it last so it cannot swallow anything after it.
            argv += ["--disallowedTools", ",".join(c.disallowed_tools)]
        return argv

    def env(self) -> dict[str, str]:
        env = {
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            # The Bash tool defaults to a 2 min timeout (10 min max); alignments and
            # assemblies routinely run longer.
            "BASH_DEFAULT_TIMEOUT_MS": "600000",
            "BASH_MAX_TIMEOUT_MS": "3600000",
            # Each Bash-tool command goes first to the OOM killer, before Claude Code
            # itself (the wrapper is in the sandbox image).
            "CLAUDE_CODE_SHELL_PREFIX": "/usr/local/libexec/bmbench/oom-first",
        }
        env.update(self.cfg.env)
        return env

    def version_command(self) -> list[str]:
        return ["claude", "--version"]

    def session_usage(self, session_root: Path) -> dict[str, int] | None:
        """Sum final per-message usage over every session file (main + subagents).

        stream-json only carries each message's *first-chunk* usage, so a run killed
        before its `result` event would otherwise report no output tokens at all.
        Messages repeat once per content block; the message id deduplicates them.
        """
        per_message: dict[str, dict[str, Any]] = {}
        for path in sorted(session_root.rglob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                if '"usage"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = event.get("message") or {}
                if event.get("type") == "assistant" and msg.get("id") and msg.get("usage"):
                    per_message[msg["id"]] = msg["usage"]
        if not per_message:
            return None
        keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        totals = {k: sum(int(u.get(k) or 0) for u in per_message.values()) for k in keys}
        totals["api_messages"] = len(per_message)
        return totals

    def parse(self, stdout_path: Path, stderr_path: Path, segment: Segment) -> Segment:
        result: dict[str, Any] | None = None
        # A background job finishing after the turn ends starts another turn, and with
        # it another result event: num_turns is per event, total_cost_usd cumulative.
        turns = 0
        try:
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            segment.session_id = event.get("session_id") or segment.session_id
            if kind == "assistant":
                segment.steps += _assistant_steps(event)
            elif kind == "user":
                segment.steps += _user_steps(event)
            elif kind == "system":
                sub = event.get("subtype")
                if sub == "compact_boundary":
                    meta = event.get("compact_metadata") or {}
                    segment.steps.append(Step(kind="system", text=f"context compacted ({meta.get('trigger', '')}, "
                                                                     f"{meta.get('pre_tokens', '?')} tokens before)"))
                elif sub in ("task_started", "task_notification"):
                    what = event.get("description") or event.get("summary") or ""
                    status = f" [{event['status']}]" if event.get("status") else ""
                    segment.steps.append(Step(kind="system", text=f"{sub}{status}: {what[:200]}"))
                elif sub not in _QUIET_SYSTEM:
                    segment.steps.append(Step(kind="system", text=str(sub)))
            elif kind == "rate_limit_event":
                segment.rate_limit = event.get("rate_limit_info") or {}
            elif kind == "result":
                result = event
                turns += int(event.get("num_turns") or 0)
        if result is not None:
            segment.result_subtype = result.get("subtype")
            segment.is_error = bool(result.get("is_error"))
            segment.terminal_reason = result.get("terminal_reason")
            segment.api_error_status = result.get("api_error_status")
            text = result.get("result")
            segment.final_text = text if isinstance(text, str) else ""
            segment.cost_usd = float(result.get("total_cost_usd") or 0.0)
            segment.num_turns = turns
            segment.usage = result.get("usage") or {}
            segment.model_usage = result.get("modelUsage") or {}
            if segment.is_error:
                segment.error_text = segment.final_text or str(result.get("errors") or "")
        if segment.exit_code in TIMEOUT_EXIT_CODES:
            segment.timed_out = True
        if result is None or segment.is_error or segment.exit_code not in (0, *TIMEOUT_EXIT_CODES):
            try:
                tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except FileNotFoundError:
                tail = ""
            if tail.strip():
                segment.error_text = (segment.error_text + "\n" + tail).strip()
        return segment


def _assistant_steps(event: dict[str, Any]) -> list[Step]:
    parent = event.get("parent_tool_use_id")
    content = (event.get("message") or {}).get("content") or []
    if isinstance(content, str):
        return [Step(kind="text", text=content, parent=parent)]
    steps = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            steps.append(Step(kind="text", text=block.get("text", ""), parent=parent))
        elif btype in ("thinking", "redacted_thinking"):
            steps.append(Step(kind="thinking", text=block.get("thinking", "") or "", parent=parent))
        elif btype in ("tool_use", "server_tool_use"):
            steps.append(Step(kind="tool_use", tool=block.get("name"), tool_input=block.get("input"),
                              tool_use_id=block.get("id"), parent=parent))
    return steps


def _user_steps(event: dict[str, Any]) -> list[Step]:
    parent = event.get("parent_tool_use_id")
    content = (event.get("message") or {}).get("content") or []
    if isinstance(content, str):
        return []  # the prompt itself, or a resume message
    steps = []
    for block in content:
        if block.get("type") != "tool_result":
            continue
        steps.append(Step(kind="tool_result", text=_result_text(block.get("content")),
                          tool_use_id=block.get("tool_use_id"), is_error=bool(block.get("is_error")),
                          parent=parent))
    return steps


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(f"[{item.get('type', 'content')}]")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, ensure_ascii=False)
