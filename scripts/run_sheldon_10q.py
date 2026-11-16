"""Run the documented ten-question adapted SHELDON experiment, resumably.

Five preview + five full questions, in two consecutive 15-attempt runs. Wait
for other bmbench containers first so two 3.5 GB attempts fit this laptop.
No secrets or reference answers are copied into the batch provenance files.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "runs" / "adapted_10q_batch_20261112"
CONFIGS = ["sheldon_adapted_10q_preview.yaml", "sheldon_adapted_10q_full.yaml"]


def main() -> int:
    BATCH.mkdir(parents=True, exist_ok=True)
    with (BATCH / "supervisor.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("This batch already has a running supervisor.", flush=True)
            return 1
        return supervise()


def supervise() -> int:
    os.chdir(ROOT)
    env = dict(os.environ)
    env["PATH"] = "/Applications/Docker.app/Contents/Resources/bin:" + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    docker = shutil.which("docker", path=env["PATH"])
    if not docker:
        raise RuntimeError("Docker CLI missing")
    state = {"pid": os.getpid(), "grading": "deferred", "groups": {}}

    def save(status: str, **fields: object) -> None:
        state.update(status=status, updated_at=datetime.now(timezone.utc).isoformat(), **fields)
        temp = BATCH / "status.tmp"
        temp.write_text(json.dumps(state, indent=2) + "\n")
        temp.replace(BATCH / "status.json")
        print(json.dumps({"status": status, **fields}), flush=True)

    cfgdir = BATCH / "configs"
    cfgdir.mkdir(exist_ok=True)
    # Freeze the exact configuration and implementation used for this launch.
    for name in CONFIGS:
        destination = cfgdir / name
        source = ROOT / "configs" / name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Batch configuration changed: {name}; use a new batch for new settings")
        shutil.copy2(source, destination)
    sources = [*sorted((ROOT / "bmbench").rglob("*.py")),
               *sorted((ROOT / "bmbench").rglob("*.prompt"))]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    provenance = BATCH / "provenance.json"
    if provenance.exists() and json.loads(provenance.read_text())["source_sha256"] != hashes:
        raise RuntimeError("Implementation changed; use a new batch instead of mixing implementations")
    images = {}
    for image in ("bmbench-sandbox:latest", "bmbench-proxy:latest"):
        result = subprocess.run([docker, "image", "inspect", image, "--format", "{{.Id}}"],
                                capture_output=True, text=True, check=True, env=env)
        images[image] = result.stdout.strip()
    if provenance.exists() and json.loads(provenance.read_text())["images"] != images:
        raise RuntimeError("Docker image changed; use a new batch instead of mixing images")
    provenance.write_text(json.dumps({"source_sha256": hashes, "images": images}, indent=2) + "\n")

    # These are existing runs started by the user; never stop or clean them.
    while True:
        result = subprocess.run([docker, "ps", "--filter", "label=bmbench=1", "--format", "{{.Names}}"],
                                capture_output=True, text=True, check=True, env=env)
        active = [name for name in result.stdout.splitlines() if name and not name.endswith("-proxy")]
        if not active:
            break
        save("waiting_for_existing_attempts", active_containers=active)
        time.sleep(30)

    for name in CONFIGS:
        dataset = "preview" if "preview" in name else "full"
        log = BATCH / f"{dataset}.log"
        save("running", dataset=dataset, log=str(log), active_containers=[])
        command = [str(ROOT / ".venv/bin/bmbench"), "run", "-c", str(cfgdir / name), "--no-grade"]
        with log.open("a", buffering=1) as out:
            out.write(f"\nBatch launch {datetime.now(timezone.utc).isoformat()}\n")
            result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, env=env)
        state["groups"][dataset] = {"exit_code": result.returncode, "log": str(log)}
        if result.returncode:
            save("stopped_on_error", dataset=dataset, exit_code=result.returncode)
            return result.returncode
    save("completed", dataset=None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
