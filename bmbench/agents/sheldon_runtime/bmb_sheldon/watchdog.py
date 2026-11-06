"""The time limit, enforced on the commands SHELDON runs.

SHELDON's own `max_seconds` stops new stages from starting and then runs its
finalize stage, but it waits for whatever is still running: a shell command can
take up to its 600 s timeout, and a model call already in flight can start one
more. The harness stops Claude Code's and Codex's running processes at the limit;
this does the same for SHELDON. From the deadline on, every process SHELDON
started (its shell commands and their children) is SIGKILLed, again every
`interval` seconds until SHELDON exits, so a command launched after the limit is
stopped too. SHELDON itself is never touched: it goes on to give its answer.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path


def ppid_map() -> dict[int, int]:
    """{pid: parent pid} for every process visible here (/proc on Linux, `ps` elsewhere)."""
    out: dict[int, int] = {}
    proc = Path("/proc")
    if proc.is_dir() and (proc / "self" / "stat").exists():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # pid (comm) state ppid ...: comm may hold spaces and parentheses
            rest = stat[stat.rfind(")") + 2:].split()
            if len(rest) >= 2:
                out[int(entry.name)] = int(rest[1])
        return out
    text = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True).stdout
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            out[int(parts[0])] = int(parts[1])
    return out


def descendants(root: int) -> list[int]:
    parents = ppid_map()
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = [], list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid == root or pid in found:
            continue
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


class TimeLimit:
    """Kill this process's descendants from `deadline` (a `time.monotonic()` value) on."""

    def __init__(self, deadline: float, interval: float = 2.0, root: int | None = None) -> None:
        self.deadline = deadline
        self.interval = interval
        self.root = root or os.getpid()
        self.hit_at: float | None = None
        self.killed = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bmb-time-limit", daemon=True)

    @property
    def hit(self) -> bool:
        return self.hit_at is not None

    def start(self) -> TimeLimit:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def kill_now(self) -> int:
        n = 0
        for pid in descendants(self.root):
            try:
                os.kill(pid, signal.SIGKILL)
                n += 1
            except (ProcessLookupError, PermissionError):
                pass
        self.killed += n
        return n

    def _run(self) -> None:
        if self._stop.wait(max(0.0, self.deadline - time.monotonic())):
            return
        self.hit_at = time.monotonic()
        while True:
            self.kill_now()
            if self._stop.wait(self.interval):
                return
