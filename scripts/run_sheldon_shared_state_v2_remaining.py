"""Run and grade the remaining 24 shared-state v2 attempts, resumably.

Four preview and four full questions, three attempts each. All other solver
settings match the v1 batch. The v1 Docker image and results are preserved.
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
BATCH = ROOT / "runs" / "shared_state_v2_remaining_batch_20261113"
CONFIGS = ["sheldon_shared_state_v2_remaining_preview.yaml", "sheldon_shared_state_v2_remaining_full.yaml"]


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
    state = {"pid": os.getpid(), "grading": "after_each_solver_group", "groups": {}}

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
    for image in ("bmbench-sandbox:shared-state-v2", "bmbench-proxy:latest"):
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
        from bmbench.config import load_config

        cfg = load_config(cfgdir / name)
        found = list((cfg.run_dir / "problems").glob("*/attempt-*/result.json"))
        if len(found) != 12:
            save("stopped_on_error", dataset=dataset, error="Expected twelve solver result files")
            return 1
        save("grading", dataset=dataset)
        grade_command = [str(ROOT / ".venv/bin/bmbench"), "grade", "-r", str(cfg.run_dir), "--concurrency", "2"]
        with log.open("a", buffering=1) as out:
            grading = subprocess.run(grade_command, stdout=out, stderr=subprocess.STDOUT, env=env)
        grades = list((cfg.run_dir / "problems").glob("*/attempt-*/grade.json"))
        state["groups"][dataset].update(grade_exit_code=grading.returncode, graded_attempts=len(grades))
        if grading.returncode or len(grades) != 12:
            save("stopped_on_error", dataset=dataset, error="Grading incomplete; rerun to resume")
            return 1
    compare()
    save("completed", dataset=None)
    return 0


def compare() -> None:
    import csv
    from bmbench.config import load_config
    from bmbench.report import collect, CSV_FIELDS
    from bmbench.metrics import summarize_by_split, per_problem

    newer, older = [], []
    reused = []
    for dataset, problem in [("preview", "hb035"), ("full", "hb010")]:
        remaining_cfg = load_config(BATCH / "configs" / f"sheldon_shared_state_v2_remaining_{dataset}.yaml")
        completed, _ = collect(remaining_cfg.run_dir)
        assert len(completed) == 12
        prior_dir = ROOT / "runs" / f"adapted_shared_state_v2_{problem}_3attempts_20261113"
        prior, _ = collect(prior_dir)
        assert len(prior) == 3
        newer.extend(completed + prior)
        reused.append(str(prior_dir))
        baseline, _ = collect(ROOT / "runs" / f"adapted_10q_{dataset}_3attempts_shell8000_20261112")
        assert len(baseline) == 15
        older.extend(baseline)
    assert len({(r["problem_id"], r["attempt"]) for r in newer}) == 30
    metrics = {"v1": summarize_by_split(older), "v2": summarize_by_split(newer), "reused_runs": reused}
    (BATCH / "comparison.json").write_text(json.dumps(metrics, indent=2) + "\n")
    with (BATCH / "combined-attempts.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(newer)
    old_problems = {p["problem_id"]: p for p in per_problem(older)}
    lines = ["# Shared-state v2: ten-question comparison", "",
             "Includes 24 new attempts and the six completed v2 attempts; none is counted twice.", "",
             "| Question | v1 | v2 |", "|---|---:|---:|"]
    for p in per_problem(newer):
        old = old_problems[p["problem_id"]]
        lines.append(f"| {p['problem_id']} | {old['correct']}/3 | {p['correct']}/3 |")
    for label in ("v1", "v2"):
        m=metrics[label]["all"]
        lines += ["", f"{label}: accuracy {m['accuracy']:.1%}, pass@3 {m['pass_at_k']:.1%}, "
                  f"pass^3 {m['pass_hat_k']:.1%}, mean time {m['minutes_mean']:.1f} minutes."]
    lines += ["", "Both versions include early stop. This historical comparison does not isolate causality.", ""]
    (BATCH / "comparison.md").write_text("\n".join(lines))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
