"""Download the official preview and prepare solver inputs without rubrics."""
import csv
import json
import shutil
import zipfile
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent


def main():
    source = Path(snapshot_download(
        "Anthropic/BioMysteryBench-preview", repo_type="dataset",
        local_dir=ROOT / "source",
        allow_patterns=["problems.csv", "data.zip", "README.md", "CHANGELOG.md", "LICENSE"],
    ))
    with (source / "problems.csv").open(newline="", encoding="utf-8-sig") as f:
        problems = list(csv.DictReader(f))
    if len(problems) != 5:
        raise ValueError(f"Expected 5 preview problems, got {len(problems)}")
    extracted = ROOT / "extracted"
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(source / "data.zip") as archive:
        for member in archive.infolist():
            target = (extracted / member.filename).resolve()
            if not target.is_relative_to(extracted.resolve()):
                raise ValueError(f"Unsafe archive path: {member.filename}")
        archive.extractall(extracted)
    for row in problems:
        task_id = row["id"]
        if Path(task_id).name != task_id or task_id in (".", ".."):
            raise ValueError("Invalid task ID")
        candidates = [p for p in extracted.rglob(task_id) if p.is_dir()]
        if len(candidates) != 1:
            raise ValueError(f"Expected one data directory for {task_id}: {candidates}")
        work = ROOT / "runs" / task_id
        work.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidates[0], work / "data", dirs_exist_ok=True)
        prompt = f"""Solve this bioinformatics task using actual analysis of ./data.
Task: {row['question']}
If the question references another data directory, use ./data instead.
Allowed network domains: {row['allowed_domains']}
Use the sheldon-runtime Conda environment; Python executable: __PYTHON__.
Preserve analysis scripts and evidence in the current working directory.
Only inspect this task's working directory and standard installed tools.
Do not read sibling directories, benchmark source files, answer keys, or rubrics.
Do not search for benchmark solutions or look up GEO/SRA/ENA/BioProject
accessions to identify the original study, publication, or sample metadata.
Standard bioinformatics database queries are allowed within the allowed domains.
Report unavailable dependencies or network access rather than inventing results.
Finish with a clearly labeled final answer, evidence, and limitations.
"""
        import sys
        (work / "prompt.txt").write_text(prompt.replace("__PYTHON__", sys.executable))
        print(f"Prepared {task_id}")
    (ROOT / "dataset_version.json").write_text(json.dumps({
        "dataset": "Anthropic/BioMysteryBench-preview",
        "problem_ids": [row["id"] for row in problems],
        "note": "HF revision is recorded in source/.cache/huggingface/download metadata",
    }, indent=2))


if __name__ == "__main__":
    main()
