"""Bounded diagnostic run with compact progress. No changes to SHELDON core."""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sheldon.core.config import Config
from sheldon.runner import run_dir


def prediction_status(output, task):
    """The upstream CLI exits 0 even when a task has a recorded error."""
    path = output / "predictions.jsonl"
    if not path.exists():
        return False, "No predictions.jsonl was produced"
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    matching = [record for record in records if record.get("task_id") == task]
    if len(matching) != 1:
        return False, f"Expected one prediction for {task}; found {len(matching)}"
    record = matching[0]
    if record.get("error"):
        return False, f"Task failed: {record['error']}"
    if not str(record.get("answer") or "").strip():
        return False, "Task produced an empty answer"
    return True, "Nonempty answer produced; correctness still requires separate grading"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--task", default="hb020")
    parser.add_argument("--hard-timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tag = "debug-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    config = ROOT / "configs/experiments/biomysterybench_debug.yaml"
    cfg = Config.load(config, model=args.model, tasks=args.task, tag=tag)
    output = run_dir(cfg)
    command = [sys.executable, "-m", "sheldon.cli", "run", "-c", str(config),
               "--model", args.model, "--task", args.task, "--tag", tag]
    print(f"Diagnostic only; output: {output}", flush=True)
    print(f"Hard deadline: {args.hard_timeout}s; Responses stream deadline: 90s; SDK retries: 0", flush=True)
    if args.dry_run:
        print("Command:", command)
        return
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SHELDON_REQUEST_TIMEOUT": "90", "SHELDON_RESPONSES_TIMEOUT": "90", "SHELDON_RESPONSES_STREAM": "1", "SHELDON_RETRY_ATTEMPTS": "1"}
    started = time.monotonic()
    last_notice = started
    seen = 0
    proc = None
    try:
        with (output / "console.log").open("w") as log:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed > args.hard_timeout:
                    raise TimeoutError("Diagnostic hard timeout reached")
                traces = list((output / "traces").glob("*.jsonl"))
                if traces:
                    lines = traces[0].read_text().splitlines()
                    for line in lines[seen:]:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        print(f"[{elapsed:.0f}s] {event.get('kind')} {event.get('tag', '')}", flush=True)
                    seen = len(lines)
                if time.monotonic() - last_notice > 30:
                    print(f"[{elapsed:.0f}s] Process alive; {seen} trace events. Waiting for next event.", flush=True)
                    last_notice = time.monotonic()
                time.sleep(1)
    except (KeyboardInterrupt, TimeoutError) as exc:
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        print(f"Stopped: {type(exc).__name__}. Logs preserved in {output}")
        raise SystemExit(124 if isinstance(exc, TimeoutError) else 130)
    success, message = prediction_status(output, args.task)
    print(f"CLI exit={proc.returncode}; {message}")
    print(f"Inspect predictions.jsonl and console.log in {output}")
    raise SystemExit(proc.returncode or (0 if success else 1))


if __name__ == "__main__":
    main()
