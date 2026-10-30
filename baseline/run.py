"""Run one task (recommended first) or all five in fresh Codex sessions."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("task", help="Task ID, or all")
parser.add_argument("--model", help="Explicit Codex model; otherwise uses CLI configuration")
parser.add_argument("--timeout", type=int, default=3600, help="Seconds per task")
parser.add_argument("--codex", help="Codex executable path (or set CODEX_BIN)")
parser.add_argument("--dry-run", action="store_true", help="Validate CLI and tasks without running a model")
args = parser.parse_args()
requested = args.codex or os.environ.get("CODEX_BIN")
codex = shutil.which(requested or "codex")
if requested and not codex:
    parser.error(f"Codex executable not found: {requested}")
if not codex:
    machine = platform.machine().lower()
    target = {("Darwin", "arm64"): "macos-aarch64",
              ("Darwin", "x86_64"): "macos-x86_64"}.get((platform.system(), machine))
    if target:
        candidates = [p for p in (Path.home() / ".vscode/extensions").glob(
            f"openai.chatgpt-*/bin/{target}/codex") if os.access(p, os.X_OK)]
        if candidates:
            codex = str(max(candidates, key=lambda p: p.stat().st_mtime))
if not codex:
    parser.error("Codex CLI not found. Pass --codex /absolute/path/to/codex or set CODEX_BIN.")
print(f"Using Codex: {codex}", flush=True)
tasks = sorted((ROOT / "runs").glob("*/prompt.txt"))
if args.task != "all":
    tasks = [p for p in tasks if p.parent.name == args.task]
if not tasks:
    parser.error("No matching prepared tasks. Run prepare.py first.")
if args.dry_run:
    subprocess.run([codex, "--version"], check=True)
    print("Prepared tasks: " + ", ".join(p.parent.name for p in tasks))
    raise SystemExit(0)
failed = False
for prompt in tasks:
    work = prompt.parent
    if (work / "status.json").exists():
        previous = json.loads((work / "status.json").read_text())
        if args.task == "all" and previous.get("exit_code") == 0:
            print(f"Skipping completed task {work.name}", flush=True)
            continue
        raise SystemExit(f"Existing run in {work}; preserve results before rerunning.")
    command = [codex, "exec", "--skip-git-repo-check", "--sandbox",
               "workspace-write", "--json", "--output-last-message", "answer.md"]
    if args.model:
        command += ["--model", args.model]
    command += ["-"]
    started = time.time()
    print(f"Running {work.name}; logs: {work / 'events.jsonl'}", flush=True)
    with prompt.open() as stdin, (work / "events.jsonl").open("w") as stdout, \
            (work / "stderr.log").open("w") as stderr:
        try:
            result = subprocess.run(command, cwd=work, stdin=stdin, stdout=stdout,
                                    stderr=stderr, timeout=args.timeout)
            status = {"exit_code": result.returncode, "timeout": False}
        except subprocess.TimeoutExpired:
            status = {"exit_code": None, "timeout": True}
        except OSError as exc:
            print(f"Could not start Codex: {exc}", flush=True)
            raise SystemExit(1) from None
    status.update(elapsed_seconds=round(time.time() - started, 2), command=command)
    (work / "status.json").write_text(json.dumps(status, indent=2))
    failed |= status["exit_code"] != 0
    print(f"Finished {work.name}: {status}", flush=True)
raise SystemExit(1 if failed else 0)
