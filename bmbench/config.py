"""Run configuration: YAML file + dotted command-line overrides.

Every field has a default, so a config file only states what differs. Unknown keys
are an error rather than silently ignored: a typo in `timeout_s` must not quietly
run every trial with the default limit.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Auth variables forwarded (by name, never by value on a command line) into the sandbox.
SECRET_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
#: The OpenAI-compatible LiteLLM proxy: the judge calls it from the host, and the
#: Codex agent uses it as its model provider (the key is passed into Codex sandboxes only).
LITELLM_URL_ENV, LITELLM_KEY_ENV = "LITELLM_BASE_URL", "LITELLM_API_KEY"


@dataclass
class DatasetConfig:
    name: str = "preview"  # preview | full
    root: str = "data"  # data/<name>/{meta,tasks}
    task_ids: list[str] | None = None  # None = every problem in problems.csv
    human_solvable: str | None = None  # "yes" | "no" | None (both)
    # A question's data is downloaded when its first trial starts and, with this on,
    # deleted again once all its trials have finished.
    delete_after_task: bool = True


@dataclass
class AgentConfig:
    type: str = "claude_code"  # claude_code | codex
    model: str = "claude-opus-5-5"
    effort: str | None = "high"
    # Claude Code only. WebSearch runs on Anthropic's servers, so the egress proxy
    # cannot hold it to the problem's allowed domains. AskUserQuestion has nobody to answer it.
    permission_mode: str = "bypassPermissions"
    disallowed_tools: list[str] = field(default_factory=lambda: ["WebSearch", "AskUserQuestion"])
    max_budget_usd: float | None = None
    # (input, cached input, output) USD per million tokens, for agents that do not
    # report a cost themselves (Codex)
    usd_per_mtok: list[float] | None = None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # on top of the adapter's own defaults
    # After a wall-clock timeout: one resumed turn asking for the best answer so far.
    finalize_on_timeout: bool = True
    finalize_timeout_s: int = 180
    # When a run ends with no <answer> tag: resumed turns asking it to finish.
    max_nudges: int = 1


@dataclass
class SandboxConfig:
    image: str = "bmbench-sandbox:latest"
    proxy_image: str = "bmbench-proxy:latest"
    platform: str | None = None  # e.g. linux/amd64; None = native
    cpus: float = 4
    memory: str = "3.5g"
    timeout_s: int = 3600  # wall clock for the main agent call
    network: str = "allowlist"  # allowlist | open
    # Hosts every agent needs on top of the problem's allowed_domains (pip downloads
    # wheels from files.pythonhosted.org). Each adapter adds its own model API host.
    infra_domains: list[str] = field(default_factory=lambda: ["files.pythonhosted.org"])
    extra_allowed_domains: list[str] = field(default_factory=list)
    # Replace the problem's allowed_domains entirely (e.g. [] for an offline ablation).
    override_allowed_domains: list[str] | None = None
    # copy: clone the task data into each trial's workspace (copy-on-write where the
    # filesystem allows). readonly: bind-mount the prepared data read-only instead.
    input_mode: str = "copy"
    # The workspace (inputs, downloads, intermediate files) is deleted after each
    # trial; workspace_files.tsv keeps the list of files. The trajectory already
    # holds every command and every output the agent saw.
    keep_workspace: bool = False
    keep_input_data: bool = False  # with keep_workspace: keep the cloned inputs too


@dataclass
class GraderConfig:
    auto: bool = True  # grade each trial as soon as it finishes
    model: str = "gpt-6-sol"  # served by the LiteLLM proxy in LITELLM_BASE_URL
    effort: str | None = "medium"  # sent as reasoning_effort; None = the model's default
    # (input, output) USD per million tokens, for the cost estimate when the proxy
    # does not report one
    usd_per_mtok: list[float] = field(default_factory=lambda: [2.0, 10.0])
    timeout_s: int = 600
    max_action_log_chars: int = 150_000
    max_final_message_chars: int = 6_000


@dataclass
class RunConfig:
    run_name: str = "preview-data_claude-code_opus-5-5_high"
    runs_dir: str = "runs"
    n_trials: int = 5
    concurrency: int = 1
    prompt_template: str = "prompts/task.md"
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    grader: GraderConfig = field(default_factory=GraderConfig)

    @property
    def run_dir(self) -> Path:
        return resolve(self.runs_dir) / self.run_name

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def resolve(path: str | Path) -> Path:
    """Relative paths mean the repository, whatever directory the CLI runs from."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"unknown config key(s) in {where or 'top level'}: {', '.join(unknown)}")
    kwargs = {}
    for name, value in data.items():
        sub = _SECTIONS.get(name) if cls is RunConfig else None
        if sub is not None:
            kwargs[name] = _build(sub, value or {}, name)
        else:
            kwargs[name] = value
    return cls(**kwargs)


_SECTIONS = {
    "dataset": DatasetConfig,
    "agent": AgentConfig,
    "sandbox": SandboxConfig,
    "grader": GraderConfig,
}


def apply_overrides(data: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """`agent.effort=high`, `n_trials=3`, `dataset.task_ids=[hb035,hb020]` (YAML values)."""
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"override must look like key=value, got {item!r}")
        key, raw = item.split("=", 1)
        node = data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"cannot set {key!r}: {part!r} is not a section")
        node[parts[-1]] = yaml.safe_load(raw)
    return data


def load_config(path: str | Path | None, overrides: list[str] | None = None) -> RunConfig:
    data: dict[str, Any] = {}
    if path is not None:
        with open(resolve(path), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data = apply_overrides(data, overrides or [])
    return _build(RunConfig, data, "")


def load_dotenv(path: str | Path | None = None) -> None:
    """Minimal .env reader (KEY=VALUE lines). Never overrides the real environment."""
    p = resolve(path or ".env")
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def secret_values() -> list[str]:
    """Values to scrub from anything the harness saves."""
    names = (*SECRET_ENV, LITELLM_KEY_ENV, "HF_TOKEN")
    return [v for n in names if (v := os.environ.get(n)) and len(v) >= 8]
