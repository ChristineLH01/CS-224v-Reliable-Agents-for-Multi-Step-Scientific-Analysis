"""What an agent adapter provides. The runner, sandbox, grader and metrics are shared.

An adapter only knows how to *invoke* its CLI inside the sandbox and how to read
what the CLI printed. It never runs a model loop of its own: the point of the
harness is to measure each agent's own framework as shipped.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from bmbench.config import AgentConfig
from bmbench.trajectory import Segment

_AUTH_PAT = re.compile(
    r"invalid api key|please run /login|not logged in|authentication[_ ]error|oauth token|"
    r"\b401\b|invalid x-api-key",
    re.IGNORECASE,
)
_LIMIT_PAT = re.compile(
    r"rate[_ ]limit|usage limit|limit reached|overloaded|\b429\b|\b529\b|credit balance is too low|"
    r"budget has been exceeded|exceeded budget|ExceededBudget",
    re.IGNORECASE,
)


def classify_error(text: str) -> str | None:
    """'auth' | 'rate_limit' | None, from an error message or stderr tail."""
    if not text:
        return None
    if _LIMIT_PAT.search(text):
        return "rate_limit"
    if _AUTH_PAT.search(text):
        return "auth"
    return None


class Agent(ABC):
    #: short identifier used in run names and reports
    name: str = ""
    #: directories inside the sandbox holding the agent's own session records,
    #: copied into the attempt directory after the run
    session_dirs: tuple[str, ...] = ()
    #: host environment variables holding this agent's credentials, passed into the
    #: sandbox by name only (never another agent's)
    secret_env: tuple[str, ...] = ()

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg

    @property
    def infra_domains(self) -> tuple[str, ...]:
        """Hosts the agent itself must reach (its model API), admitted by the proxy."""
        return ()

    @abstractmethod
    def command(self, *, timeout_s: int, session_id: str, resume: bool = False,
                budget_usd: float | None = None) -> list[str]:
        """argv to run inside the sandbox; the prompt arrives on stdin."""

    @abstractmethod
    def parse(self, stdout_path: Path, stderr_path: Path, segment: Segment) -> Segment:
        """Fill `segment` (steps, final text, cost, turns, errors) from the CLI's output."""

    def env(self) -> dict[str, str]:
        """Non-secret environment for the agent process."""
        return dict(self.cfg.env)

    def session_usage(self, session_root: Path) -> dict[str, int] | None:
        """Token totals from the agent's own session records (copied out of the
        sandbox), which survive a killed invocation that never printed its totals."""
        return None

    def cost_from_tokens(self, tokens: dict[str, int] | None) -> float | None:
        """Dollar cost for agents that do not report one; None = use the reported cost."""
        return None

    def subagent_segments(self, session_root: Path) -> list[Segment]:
        """Subagent work the CLI's own output stream leaves out, recovered from its
        session records, so the transcript and the cheating audit see every tool call."""
        return []

    def version_command(self) -> list[str]:
        return []


def make_agent(cfg: AgentConfig) -> Agent:
    if cfg.type == "claude_code":
        from bmbench.agents.claude_code import ClaudeCodeAgent  # noqa: PLC0415

        return ClaudeCodeAgent(cfg)
    if cfg.type == "codex":
        from bmbench.agents.codex import CodexAgent  # noqa: PLC0415

        return CodexAgent(cfg)
    raise ValueError(f"unknown agent type {cfg.type!r} (available: claude_code, codex)")
