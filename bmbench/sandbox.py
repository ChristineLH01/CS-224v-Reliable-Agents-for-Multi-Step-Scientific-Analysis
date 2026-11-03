"""One Docker sandbox per attempt: an agent container and, in `allowlist` mode, its proxy.

    allowlist:  agent ── <name>-net (internal: no route out) ── <name>-proxy ── bridge ── internet
    open:       agent ── default bridge ── internet

The agent container's PID 1 (the image's tini entrypoint + `sleep infinity`) runs as root, and everything
the agent starts runs as the unprivileged `agent` user via `docker exec -u agent`.
That split lets the harness kill every agent process after a timeout
(`pkill -u agent`) without taking the container down, so a resumed "give your
best answer" turn can still run in the same environment.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path

from bmbench.config import SandboxConfig

LABEL = "bmbench"
WORKDIR = "/workspace"
DATA_DIR = "/workspace/data"
AGENT_USER = "agent"
PROXY_PORT = 3128
#: The proxy's name on the attempt's own network. Container names can exceed the
#: 63-character DNS label limit (long run names and problem ids), aliases need not.
PROXY_ALIAS = "bmb-proxy"


@dataclass
class ExecResult:
    code: int
    timed_out: bool  # the harness's outer deadline fired (inner `timeout` is code 124)
    seconds: float


async def run_cmd(
    *args: str, check: bool = True, timeout: float | None = None, env: dict | None = None
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    code = proc.returncode or 0
    if check and code != 0:
        raise RuntimeError(
            f"command failed ({code}): {' '.join(args[:6])}...\n{err.decode(errors='replace')[-2000:]}"
        )
    return code, out.decode(errors="replace"), err.decode(errors="replace")


def sanitize_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", text).strip("-.").lower()[:120]


def minimal_domains(domains: list[str]) -> list[str]:
    """Drop entries already covered by a parent domain (squid warns on overlaps)."""
    uniq = sorted({d.strip().lower().strip(".") for d in domains if d.strip()}, key=len)
    kept: list[str] = []
    for d in uniq:
        if not any(d == k or d.endswith("." + k) for k in kept):
            kept.append(d)
    return sorted(kept)


@dataclass
class DockerSandbox:
    name: str
    cfg: SandboxConfig
    workspace: Path
    allowed_domains: list[str] = field(default_factory=list)
    readonly_data: Path | None = None  # input_mode=readonly: mount this at /workspace/data:ro
    run_label: str = ""

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        self.proxy = f"{self.name}-proxy"
        self.network = f"{self.name}-net"
        self._started: list[str] = []  # what to tear down, in order

    # -- lifecycle ----------------------------------------------------------

    def _labels(self) -> list[str]:
        out = ["--label", f"{LABEL}=1"]
        if self.run_label:
            out += ["--label", f"{LABEL}.run={self.run_label}"]
        return out

    def proxy_env(self) -> dict[str, str]:
        if self.cfg.network != "allowlist":
            return {}
        url = f"http://{PROXY_ALIAS}:{PROXY_PORT}"
        no_proxy = "localhost,127.0.0.1,::1"
        return {
            "HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url,
            "FTP_PROXY": url, "ftp_proxy": url, "NO_PROXY": no_proxy, "no_proxy": no_proxy,
        }

    async def start(self) -> None:
        await self.stop()  # leftovers from an interrupted attempt with the same name
        net = "bridge"
        if self.cfg.network == "allowlist":
            await run_cmd("docker", "network", "create", "--internal", *self._labels(), self.network)
            self._started.append("network")
            domains = ",".join(minimal_domains(self.allowed_domains))
            await run_cmd(
                "docker", "run", "-d", "--name", self.proxy, *self._labels(),
                "--network", "bridge", "-e", f"ALLOWED_DOMAINS={domains}",
                self.cfg.proxy_image,
            )
            self._started.append("proxy")
            await run_cmd("docker", "network", "connect", "--alias", PROXY_ALIAS, self.network, self.proxy)
            net = self.network
        elif self.cfg.network != "open":
            raise ValueError(f"sandbox.network must be 'allowlist' or 'open', not {self.cfg.network!r}")

        args = [
            "docker", "run", "-d", "--name", self.name, *self._labels(),
            "--user", "0:0",  # PID 1 (the image's tini) only; agent work runs as `agent` via exec
            "--network", net, "--hostname", "sandbox",
            "--cpus", str(self.cfg.cpus), "--memory", str(self.cfg.memory),
            "-v", f"{self.workspace.resolve()}:{WORKDIR}",
            "-w", WORKDIR,
        ]
        if self.readonly_data is not None:
            args += ["-v", f"{self.readonly_data.resolve()}:{DATA_DIR}:ro"]
        if self.cfg.platform:
            args += ["--platform", self.cfg.platform]
        for k, v in self.proxy_env().items():
            args += ["-e", f"{k}={v}"]
        args += [self.cfg.image, "sleep", "infinity"]
        await run_cmd(*args)
        self._started.append("agent")
        if self.cfg.network == "allowlist":
            await self._wait_for_proxy()

    async def _wait_for_proxy(self, seconds: float = 20.0) -> None:
        probe = (
            f"for i in $(seq {int(seconds * 5)}); do "
            f"(echo > /dev/tcp/{PROXY_ALIAS}/{PROXY_PORT}) 2>/dev/null && exit 0; sleep 0.2; done; exit 1"
        )
        code, _, _ = await run_cmd("docker", "exec", self.name, "bash", "-c", probe, check=False)
        if code != 0:
            _, logs, _ = await run_cmd("docker", "logs", "--tail", "30", self.proxy, check=False)
            raise RuntimeError(f"egress proxy {self.proxy} did not come up:\n{logs}")

    async def stop(self) -> None:
        for args in (
            ("docker", "rm", "-f", self.name),
            ("docker", "rm", "-f", self.proxy),
            ("docker", "network", "rm", self.network),
        ):
            await run_cmd(*args, check=False)
        self._started.clear()

    # -- running things -----------------------------------------------------

    async def exec(
        self,
        argv: list[str],
        *,
        stdin_path: Path | None,
        stdout_path: Path,
        stderr_path: Path,
        deadline_s: float,
        env: dict[str, str] | None = None,
        secret_env: tuple[str, ...] = (),
        user: str = AGENT_USER,
    ) -> ExecResult:
        """Run `argv` in the container, streaming stdout/stderr to files.

        `deadline_s` is the harness's safety net; the agent command is expected to
        carry its own `timeout` (exit 124) so it can end gracefully first.
        `secret_env` names host variables passed by name only (the agent's own key),
        so their values never appear on a command line.
        """
        args = ["docker", "exec", "-i", "-u", user, "-w", WORKDIR]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        host_env = os.environ.copy()
        for k in secret_env:
            if os.environ.get(k):
                args += ["-e", k]  # value taken from our environment, not argv
        args += [self.name, *argv]
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        stdin = open(stdin_path, "rb") if stdin_path else asyncio.subprocess.DEVNULL
        try:
            with open(stdout_path, "ab") as out, open(stderr_path, "ab") as err:
                proc = await asyncio.create_subprocess_exec(
                    *args, stdin=stdin, stdout=out, stderr=err, env=host_env,
                    start_new_session=True,
                )
                try:
                    code = await asyncio.wait_for(proc.wait(), timeout=deadline_s)
                    return ExecResult(code=code or 0, timed_out=False, seconds=loop.time() - t0)
                except TimeoutError:
                    await self.kill_agent_processes()
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    await proc.wait()
                    return ExecResult(code=-1, timed_out=True, seconds=loop.time() - t0)
        finally:
            if stdin_path:
                stdin.close()

    async def kill_agent_processes(self) -> None:
        """Everything the agent started (background jobs included); PID 1 survives."""
        await run_cmd("docker", "exec", "-u", "0", self.name, "pkill", "-KILL", "-u", AGENT_USER, check=False)

    async def copy_out(self, src: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        code, _, _ = await run_cmd("docker", "cp", f"{self.name}:{src}", str(dest), check=False)
        return code == 0

    async def proxy_log(self, dest: Path) -> bool:
        if self.cfg.network != "allowlist":
            return False
        code, _, _ = await run_cmd(
            "docker", "cp", f"{self.proxy}:/var/log/bmbench/access.log", str(dest), check=False
        )
        return code == 0


async def docker_ok() -> tuple[bool, str]:
    try:
        code, out, err = await run_cmd("docker", "info", "--format", "{{.ServerVersion}}", check=False, timeout=20)
    except (FileNotFoundError, TimeoutError) as e:
        return False, str(e)
    return code == 0, (out.strip() or err.strip())


async def image_info(image: str) -> dict[str, str] | None:
    code, out, _ = await run_cmd(
        "docker", "image", "inspect", "--format", "{{.Id}}|{{.Architecture}}|{{.Created}}", image, check=False
    )
    if code != 0:
        return None
    image_id, arch, created = (out.strip().split("|") + ["", "", ""])[:3]
    return {"image": image, "id": image_id, "architecture": arch, "created": created}


async def remove_all() -> list[str]:
    """Remove every container and network the harness labelled (`bmbench clean`)."""
    removed = []
    _, out, _ = await run_cmd("docker", "ps", "-aq", "--filter", f"label={LABEL}=1", check=False)
    ids = out.split()
    if ids:
        await run_cmd("docker", "rm", "-f", *ids, check=False)
        removed += [f"container {i}" for i in ids]
    _, out, _ = await run_cmd("docker", "network", "ls", "-q", "--filter", f"label={LABEL}=1", check=False)
    nets = out.split()
    if nets:
        await run_cmd("docker", "network", "rm", *nets, check=False)
        removed += [f"network {n}" for n in nets]
    return removed
