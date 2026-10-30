"""Prepare 25 clean inputs; execution requires the shared CC container harness."""
import csv
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "comparison"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise SystemExit(f"Refusing to overwrite existing experiment: {OUT}")
    with (ROOT / "source/problems.csv").open(encoding="utf-8-sig", newline="") as f:
        problems = list(csv.DictReader(f))
    if len(problems) != 5:
        raise SystemExit("Expected 5 preview questions")
    revision = (ROOT / "source/.cache/huggingface/download/problems.csv.metadata").read_text().splitlines()[0]
    config = {
        "status": "prepared_not_executed",
        "dataset": "Anthropic/BioMysteryBench-preview",
        "dataset_revision": revision,
        "dataset_csv_sha256": digest(ROOT / "source/problems.csv"),
        "data_zip_sha256": digest(ROOT / "source/data.zip"),
        "solver": "codex_cli",
        "solver_model": None,
        "reasoning_effort": "high",
        "trials_per_question": 5,
        "timeout_seconds": 1800,
        "fresh_container_per_trial": True,
        "web_search": "disabled",
        "network_policy": "enforce per-question allowed_domains externally; model transport separate",
        "judge_provider": "anthropic",
        "judge_model": None,
        "shared_cc_image_digest": None,
        "shared_cc_prompt": None,
        "shared_cc_judge_prompt": None,
        "parallelism": None,
        "cpu_limit": None,
        "memory_limit": None,
        "notes": "Target settings only. None values must be resolved before execution. Prepared prompts are provisional until matched against CC prompts.",
        "trials": [],
    }
    for row in problems:
        task_id = row["id"]
        if Path(task_id).name != task_id or task_id in (".", ".."):
            raise ValueError("Invalid task ID")
        candidates = [p for p in (ROOT / "extracted").rglob(task_id) if p.is_dir()]
        if len(candidates) != 1:
            raise ValueError(f"Missing or ambiguous data directory: {task_id}")
        for trial in range(1, 6):
            work = OUT / "inputs" / task_id / f"trial_{trial:02d}"
            work.mkdir(parents=True)
            shutil.copytree(candidates[0], work / "data")
            # Never reuse prior solver prompts, trajectories, answers, or analysis.
            prompt = f"""Analyze the files in /workspace/data to answer this question:
{row['question']}

Use /workspace/data if the question mentions another input path.
Save analysis scripts and evidence in /workspace.
Web search is disabled. Network access is restricted to:
{row['allowed_domains']}
Do not look up GEO/SRA/ENA/BioProject accession IDs to identify the original
dataset, source publication, or study metadata. Standard bioinformatics
database use (gene ID lookup, sequence annotation, reference genome download,
BLAST) is allowed. Do not seek benchmark answers or solutions.
Provide a final answer, evidence, and limitations within the time budget.
"""
            (work / "prompt.txt").write_text(prompt)
            files = {str(p.relative_to(work)): digest(p) for p in sorted(work.rglob("*")) if p.is_file()}
            config["trials"].append({
                "question_id": task_id, "trial": trial,
                "input_dir": str(work.relative_to(OUT)),
                "allowed_domains": row["allowed_domains"],
                "input_sha256": files,
            })
    (OUT / "experiment.json").write_text(json.dumps(config, indent=2))
    print(f"Prepared {len(config['trials'])} clean trial inputs in {OUT}")
    print("No model calls made. Docker harness and unresolved experiment settings still required.")


if __name__ == "__main__":
    main()
