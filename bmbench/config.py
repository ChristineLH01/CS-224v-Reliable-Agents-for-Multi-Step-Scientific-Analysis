"""Run configuration: YAML file + dotted command-line overrides.

Every field has a default, so a config file only states what differs. Unknown keys
are an error rather than silently ignored: a typo in `timeout_s` must not quietly
run every attempt with the default limit.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: bmbench's changes to SHELDON, in the order they were added; `all` means these. The
#: same names as bmb_sheldon.adapted.ADAPTATIONS (the runtime's registry; a test keeps
#: the two equal).
SHELDON_ADAPTATIONS = ("early_stop",)

#: Auth variables forwarded (by name, never by value on a command line) into the sandbox.
SECRET_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
#: The OpenAI-compatible LiteLLM proxy: the Codex agent's model provider (the key is passed
#: into Codex sandboxes only), and the judge's route for non-Claude judge models.
LITELLM_URL_ENV, LITELLM_KEY_ENV = "LITELLM_BASE_URL", "LITELLM_API_KEY"


@dataclass
class DatasetConfig:
    name: str = "preview"  # preview | full
    root: str = "data"  # data/<name>/{meta,problems}
    problem_ids: list[str] | None = None  # None = every problem in problems.csv
    human_solvable: str | None = None  # "yes" | "no" | None (both)
    # A problem's data is downloaded when its first attempt starts and, with this on,
    # deleted again once all its attempts have finished.
    delete_after_problem: bool = True


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
    # The closing window after the time limit, the same for every agent: the limit of that
    # resumed turn, or, for an agent that closes out on its own (SHELDON), how long it may
    # run past the limit to give its answer, with its commands stopped.
    finalize_timeout_s: int = 300
    # When a run ends with no <answer> tag: resumed turns asking it to finish.
    max_nudges: int = 1
    # (An agent process killed by the kernel for memory ends the attempt: no resume, no
    # closing turn; it is scored incorrect and counted as out of memory.)
    # SHELDON only: its own Config fields (method, engine, max_rounds, max_calls, max_usd,
    # max_tokens, trace_prompts, params, ...), passed through unchanged; see
    # configs/sheldon_preview.yaml. `model` and `effort` come from above.
    sheldon: dict[str, Any] = field(default_factory=dict)
    # SHELDON only: bmbench's changes to it, by name (SHELDON_ADAPTATIONS), or [all] for
    # every one. Empty: baseline SHELDON. Resolved to the explicit list when the config is
    # loaded, so the run folder and its config.yaml name exactly what ran.
    adaptations: list[str] = field(default_factory=list)


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
    # copy: clone the problem's data into each attempt's workspace (copy-on-write where the
    # filesystem allows). readonly: bind-mount the prepared data read-only instead.
    input_mode: str = "copy"
    # The workspace (inputs, downloads, intermediate files) is deleted after each
    # attempt; workspace_files.tsv keeps the list of files. The trajectory already
    # holds every command and every output the agent saw.
    keep_workspace: bool = False
    keep_input_data: bool = False  # with keep_workspace: keep the cloned inputs too


@dataclass
class GraderConfig:
    auto: bool = True  # grade each attempt as soon as it finishes
    # claude-* runs as `claude -p` on the Claude subscription; any other model goes
    # through the LiteLLM proxy in LITELLM_BASE_URL (e.g. gpt-6.1-sol)
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"
    # LiteLLM only: (input, output) USD per million tokens, for the cost estimate
    # when the proxy does not report one
    usd_per_mtok: list[float] = field(default_factory=lambda: [2.0, 10.0])
    timeout_s: int = 600
    max_action_log_chars: int = 150_000
    max_final_message_chars: int = 6_000


@dataclass
class RunConfig:
    run_name: str | None = None  # None: built from the settings, see auto_run_name()
    runs_dir: str = "runs"
    n_attempts: int = 3  # attempts per problem
    concurrency: int = 1
    prompt_template: str = "prompts/problem.md"
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    grader: GraderConfig = field(default_factory=GraderConfig)

    def __post_init__(self) -> None:
        if not self.run_name:
            self.run_name = auto_run_name(self)

    @property
    def run_dir(self) -> Path:
        return resolve(self.runs_dir) / self.run_name

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def auto_run_name(cfg: RunConfig) -> str:
    """<dataset>_<agent>_<model>_<effort>_timeout-<limit>_<n>-attempts, e.g.
    preview_claude-code_opus-5-5_high_timeout-1h_3-attempts: a change to any of these
    settings starts a new run folder instead of mixing into an old one. SHELDON adds its
    round budget and any adaptations: ..._3-rounds_early-stop_timeout-1h_3-attempts."""
    a, t = cfg.agent, cfg.sandbox.timeout_s
    limit = f"{t // 3600}h" if t % 3600 == 0 else f"{round(t / 60)}min"
    attempts = f"{cfg.n_attempts}-attempt" + ("" if cfg.n_attempts == 1 else "s")
    model = a.model.removeprefix("claude-").replace(".", "-")
    parts = [cfg.dataset.name, a.type.replace("_", "-"), model, a.effort or "default"]
    if a.type == "sheldon":  # its round budget changes what it does as much as the time limit
        rounds = (a.sheldon or {}).get("max_rounds")
        parts.append(f"{rounds}-rounds" if rounds else "no-round-cap")
        adaptations = resolve_adaptations(a.adaptations)
        if adaptations:  # every change named, so a later [all] with more changes gets its own folder
            parts.append("+".join(n.replace("_", "-") for n in adaptations))
    return "_".join([*parts, f"timeout-{limit}", attempts])


def resolve_adaptations(names: Any) -> list[str]:
    """The explicit list `agent.adaptations` means: `all` expanded, unknown names refused,
    duplicates dropped, in the order of SHELDON_ADAPTATIONS."""
    if isinstance(names, str):
        names = [names]
    given = [str(n).strip() for n in (names or []) if str(n).strip()]
    unknown = [n for n in given if n != "all" and n not in SHELDON_ADAPTATIONS]
    if unknown:
        raise ValueError(f"unknown SHELDON adaptation(s): {', '.join(unknown)}; "
                         f"known: {', '.join(SHELDON_ADAPTATIONS)} (or all)")
    wanted = set(SHELDON_ADAPTATIONS) if "all" in given else set(given)
    return [n for n in SHELDON_ADAPTATIONS if n in wanted]


def resolve(path: str | Path) -> Path:
    """Relative paths mean the repository, whatever directory the CLI runs from."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


#: Keys older run folders' config.yaml may still hold, accepted and ignored so that
#: `grade` and `report` keep working on them.
RETIRED = {"agent": {"max_oom_resumes"}}


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    known = {f.name: f for f in dataclasses.fields(cls)}
    data = {k: v for k, v in data.items() if k not in RETIRED.get(where, set())}
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
    """`agent.effort=high`, `n_attempts=3`, `dataset.problem_ids=[hb035,hb020]` (YAML values)."""
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
    agent = data.get("agent")
    if isinstance(agent, dict) and agent.get("adaptations"):
        if agent.get("type", AgentConfig.type) != "sheldon":
            raise ValueError("agent.adaptations is for the SHELDON agent only")
        agent["adaptations"] = resolve_adaptations(agent["adaptations"])
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


def litellm_base_url() -> str:
    """The LiteLLM proxy's OpenAI-compatible base URL (…/v1), from LITELLM_BASE_URL."""
    base = os.environ.get(LITELLM_URL_ENV, "").strip().rstrip("/")
    if not base:
        raise RuntimeError(f"set {LITELLM_URL_ENV} (and {LITELLM_KEY_ENV}) in .env")
    return base.removesuffix("/chat/completions")


def secret_values() -> list[str]:
    """Values to scrub from anything the harness saves."""
    names = (*SECRET_ENV, LITELLM_KEY_ENV, "HF_TOKEN")
    return [v for n in names if (v := os.environ.get(n)) and len(v) >= 8]
