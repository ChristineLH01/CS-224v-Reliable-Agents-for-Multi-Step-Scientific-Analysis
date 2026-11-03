"""Codex CLI (`codex exec`) as the agent under test, run unchanged inside the sandbox.

The invocation is plain headless Codex: its own system prompt, tools and
subagents. The model is reached through the OpenAI-compatible LiteLLM proxy in
LITELLM_BASE_URL, as a custom model provider keyed by LITELLM_API_KEY (passed into
the sandbox by name). A ChatGPT sign-in would bring its workspace's managed rules,
which force Codex's own sandbox and command approvals; with an API-key provider
Codex runs with its sandbox off, like Claude Code, and the container plus the
egress proxy isolate it.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bmbench.agents.base import Agent
from bmbench.config import LITELLM_KEY_ENV, LITELLM_URL_ENV
from bmbench.trajectory import Segment, Step

#: exit status of coreutils `timeout` when the limit fired (137 if it had to SIGKILL; an
#: out-of-memory kill also ends in 137, and the runner tells the two apart)
TIMEOUT_EXIT_CODES = (124, 137)
PROVIDER = "bmbench_litellm"


class CodexAgent(Agent):
    name = "codex"
    session_dirs = ("/home/agent/.codex/sessions",)  # rollouts only, never the rest of CODEX_HOME
    secret_env = (LITELLM_KEY_ENV,)

    @staticmethod
    def base_url() -> str:
        base = os.environ.get(LITELLM_URL_ENV, "").strip().rstrip("/")
        if not base:
            raise RuntimeError(f"Codex needs {LITELLM_URL_ENV} (and {LITELLM_KEY_ENV}) in .env")
        return base.removesuffix("/chat/completions")

    @property
    def infra_domains(self) -> tuple[str, ...]:
        try:
            host = urlparse(self.base_url()).hostname
        except RuntimeError:
            return ()
        return (host,) if host else ()

    def command(self, *, timeout_s: int, session_id: str, resume: bool = False,
                budget_usd: float | None = None) -> list[str]:
        c = self.cfg
        argv = ["timeout", "-k", "30", str(max(1, int(timeout_s))), "codex", "exec"]
        if resume:
            argv.append("resume")
        argv += ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-m", c.model]
        if c.effort:
            argv += ["-c", f"model_reasoning_effort={c.effort}"]
        argv += [
            "-c", 'web_search="disabled"',
            "-c", "check_for_update_on_startup=false",
            "-c", f'model_provider="{PROVIDER}"',
            "-c", f'model_providers.{PROVIDER}.name="LiteLLM"',
            "-c", f'model_providers.{PROVIDER}.base_url="{self.base_url()}"',
            "-c", f'model_providers.{PROVIDER}.env_key="{LITELLM_KEY_ENV}"',
            "-c", f'model_providers.{PROVIDER}.wire_api="responses"',
            *c.extra_args,
        ]
        # Each attempt has a fresh CODEX_HOME, so the latest session is this attempt's.
        argv += ["--last", "-"] if resume else ["-"]
        return argv

    def version_command(self) -> list[str]:
        return ["codex", "--version"]

    def session_usage(self, session_root: Path) -> dict[str, int] | None:
        """Token totals from Codex's rollout files: each session (the main thread and
        any subagent) records cumulative totals, so take its largest and sum those."""
        best: dict[str, dict[str, Any]] = {}
        calls = 0
        for path in sorted(session_root.rglob("*.jsonl")):
            session = path.stem
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                if '"token_count"' not in line and '"session_meta"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") or {}
                if event.get("type") == "session_meta":
                    session = payload.get("id") or session
                elif payload.get("type") == "token_count":
                    total = ((payload.get("info") or {}).get("total_token_usage")) or {}
                    calls += 1
                    if int(total.get("total_tokens") or 0) >= int((best.get(session) or {}).get("total_tokens") or 0):
                        best[session] = total
        if not best:
            return None
        totals = _claude_style({k: sum(int(u.get(k) or 0) for u in best.values())
                                for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                          "output_tokens", "reasoning_output_tokens")})
        totals["api_messages"] = calls
        return totals

    def subagent_segments(self, session_root: Path) -> list[Segment]:
        """`codex exec --json` reports only the main thread; a subagent's commands are
        only in its own rollout. That rollout starts with a copy of the parent's
        history, so calls already seen in an earlier session are skipped."""
        seen: set[str] = set()
        segments = []
        for path in sorted(session_root.rglob("*.jsonl")):  # names start with the start time
            try:
                events = [json.loads(x) for x in path.read_text(encoding="utf-8", errors="replace").splitlines()
                          if x.strip().startswith("{")]
            except (OSError, json.JSONDecodeError):
                continue
            meta = next(((e.get("payload") or {}) for e in events if e.get("type") == "session_meta"), {})
            spawn = ((meta.get("source") or {}).get("subagent") or {}).get("thread_spawn") if isinstance(
                meta.get("source"), dict) else None
            label = (spawn or {}).get("agent_nickname") or (spawn or {}).get("agent_path") or meta.get("id", "")
            steps = []
            for e in events:
                p = e.get("payload") or {}
                kind, call = p.get("type"), p.get("call_id")
                if kind in ("custom_tool_call", "function_call") and call and call not in seen:
                    seen.add(call)
                    if spawn is not None:
                        steps.append(Step(kind="tool_use", tool=p.get("name"), tool_input=_call_input(p),
                                          tool_use_id=call, parent=label))
                elif kind in ("custom_tool_call_output", "function_call_output") and spawn is not None \
                        and any(s.tool_use_id == call for s in steps):
                    steps.append(Step(kind="tool_result", text=_output_text(p.get("output")), tool_use_id=call,
                                      parent=label))
            if spawn is not None and steps:
                segments.append(Segment(kind=f"subagent:{label}", session_id=meta.get("id"), steps=steps))
        return segments

    def cost_from_tokens(self, tokens: dict[str, int] | None) -> float | None:
        if not tokens or not self.cfg.usd_per_mtok:
            return None
        usd_in, usd_cached, usd_out = self.cfg.usd_per_mtok
        return (
            (tokens.get("input_tokens", 0) + tokens.get("cache_creation_input_tokens", 0)) * usd_in
            + tokens.get("cache_read_input_tokens", 0) * usd_cached
            + tokens.get("output_tokens", 0) * usd_out
        ) / 1e6

    def parse(self, stdout_path: Path, stderr_path: Path, segment: Segment) -> Segment:
        usage: dict[str, int] = {}
        completed = failed = False
        errors: list[str] = []
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
            if kind == "thread.started":
                segment.session_id = event.get("thread_id") or segment.session_id
            elif kind == "item.completed":
                item = event.get("item") or {}
                segment.steps += _item_steps(item)
                if item.get("type") == "agent_message" and (item.get("text") or "").strip():
                    segment.final_text = item["text"]
            elif kind == "turn.completed":
                completed = True
                for k, v in (event.get("usage") or {}).items():
                    usage[k] = usage.get(k, 0) + int(v or 0)
            elif kind == "turn.failed":
                failed = True
                errors.append(str((event.get("error") or {}).get("message") or "turn failed"))
            elif kind == "error":
                # transient (a retried stream) unless the turn then fails
                errors.append(str(event.get("message") or ""))
                segment.steps.append(Step(kind="system", text=f"error: {event.get('message', '')}"[:300]))
        if completed or failed:
            segment.result_subtype = "error" if failed else "success"
            segment.is_error = failed
        segment.usage = _claude_style(usage)
        segment.cost_usd = self.cost_from_tokens(segment.usage) or 0.0
        segment.num_turns = sum(1 for s in segment.steps if s.kind == "tool_use") + (1 if completed else 0)
        if segment.exit_code in TIMEOUT_EXIT_CODES:
            segment.timed_out = True
        if not completed or failed:
            segment.error_text = "\n".join(e for e in errors if e)
            if segment.exit_code not in (0, *TIMEOUT_EXIT_CODES) or not completed:
                try:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                except FileNotFoundError:
                    tail = ""
                if tail.strip():
                    segment.error_text = (segment.error_text + "\n" + tail).strip()
        return segment


def _claude_style(u: dict[str, int]) -> dict[str, int]:
    """Codex usage (input includes cached and cache-write tokens) in the token keys
    the rest of the harness uses (input = uncached only)."""
    if not u:
        return {}
    cached, written = int(u.get("cached_input_tokens") or 0), int(u.get("cache_write_input_tokens") or 0)
    return {
        "input_tokens": max(0, int(u.get("input_tokens") or 0) - cached - written),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
        "output_tokens": int(u.get("output_tokens") or 0),  # includes reasoning
        "reasoning_output_tokens": int(u.get("reasoning_output_tokens") or 0),
    }


def _call_input(payload: dict[str, Any]) -> Any:
    """A rollout tool call's input: parsed JSON arguments, or the code of a code-mode `exec`."""
    if payload.get("type") == "custom_tool_call":
        return {"code": payload.get("input") or ""}
    try:
        return json.loads(payload.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {"arguments": payload.get("arguments")}


def _output_text(output: Any) -> str:
    if isinstance(output, list):
        return "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in output)
    return output if isinstance(output, str) else json.dumps(output)


def _unwrap(command: str) -> str:
    """`/bin/bash -lc '<script>'` -> `<script>` (what the model wrote)."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) == 3 and parts[0].endswith(("bash", "sh")) and parts[1] in ("-lc", "-c"):
        return parts[2]
    return command


def _item_steps(item: dict[str, Any]) -> list[Step]:
    kind, iid = item.get("type"), item.get("id")
    if kind == "agent_message":
        return [Step(kind="text", text=item.get("text") or "")]
    if kind == "reasoning":
        return [Step(kind="thinking", text=item.get("text") or "")]
    if kind == "command_execution":
        code = item.get("exit_code")
        out = item.get("aggregated_output") or ""
        if code not in (0, None):
            out += f"\n[exit code {code}]"
        return [
            Step(kind="tool_use", tool="Bash", tool_input={"command": _unwrap(item.get("command") or "")},
                 tool_use_id=iid),
            Step(kind="tool_result", text=out, tool_use_id=iid,
                 is_error=item.get("status") != "completed" or code not in (0, None)),
        ]
    if kind == "error":
        return [Step(kind="system", text=f"error: {item.get('message', '')}"[:300])]
    if kind == "todo_list":
        todo = "; ".join(f"[{'x' if t.get('completed') else ' '}] {t.get('text', '')}" for t in item.get("items") or [])
        return [Step(kind="system", text=f"plan: {todo}"[:600])]
    # file_change, mcp_tool_call, web_search, subagent calls, …: kept whole for the audit
    tool = {"file_change": "FileChange", "web_search": "WebSearch"}.get(kind or "", kind or "tool")
    if kind == "mcp_tool_call":
        tool = f"mcp:{item.get('server')}/{item.get('tool')}"
    body = {k: v for k, v in item.items() if k not in ("id", "type", "result", "error", "status")}
    steps = [Step(kind="tool_use", tool=tool, tool_input=body, tool_use_id=iid)]
    result = item.get("result") or item.get("error") or item.get("status")
    if result is not None:
        steps.append(Step(kind="tool_result", text=result if isinstance(result, str) else json.dumps(result)[:4000],
                          tool_use_id=iid, is_error=item.get("status") == "failed" or bool(item.get("error"))))
    return steps
