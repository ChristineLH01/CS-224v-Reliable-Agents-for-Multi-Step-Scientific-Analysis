"""BioMysteryBench adapter for solving tasks and saving answers.

Local mode does not enforce filesystem or network isolation. Optional Linux
bubblewrap isolation hides gold files but still does not enforce domain rules.
"""
from __future__ import annotations

import csv
import platform
import re
import shutil
import sys
from pathlib import Path

from sheldon.benchmarks.base import Benchmark
from sheldon.core import isolation
from sheldon.core.isolation import Sandbox
from sheldon.core.registry import benchmarks
from sheldon.core.types import Prediction, RunContext, Score, Task


@benchmarks.register("biomysterybench", "bmb")
class BioMysteryBench(Benchmark):
    name = "biomysterybench"
    splits = ("preview",)
    blurb = "BioMysteryBench preview; analysis of anonymized biological files"
    probes_essential = True
    answer_format = "Give the requested biological answer explicitly, followed by supporting evidence and limitations."

    def load(self, split="preview", limit=None, seed=0):
        if split not in self.splits:
            raise ValueError(f"Unsupported split: {split}")
        with (self.root / "problems.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        tasks = []
        for row in rows:
            tid = row["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", tid):
                raise ValueError(f"Invalid task ID: {tid!r}")
            if not (self.root / "files" / tid).is_dir():
                raise FileNotFoundError(f"Missing files for {tid}; run scripts/prepare_biomysterybench.py")
            files = sorted(
                str(p.relative_to(self.root / "files" / tid))
                for p in (self.root / "files" / tid).rglob("*") if p.is_file()
            )
            # Public input metadata only; no extracted evidence or gold hints.
            inventory = "\n".join(f"- data/{name}" for name in files[:100])
            environment = (
                f"Runtime OS: {platform.system()}. Python executable: {sys.executable}. "
                "Use this Python executable for analysis. "
                "On macOS use BSD-compatible commands: find does not support -printf; "
                "prefer Python pathlib for file enumeration. Quote paths containing spaces. "
                "For shell pipelines use set -o pipefail so earlier command failures remain visible. "
                "An empty listing or a command failure is not evidence that data is absent.\n"
            )
            question = (
                f"{row['question']}\n\n"
                "Analyze this task's files in ./data (use this path instead of any input path above). "
                "Preserve analysis scripts and evidence in the working directory. "
                f"Allowed network domains: {row['allowed_domains']}\n"
                "Do not search for benchmark solutions or answer keys. Do not look up "
                "GEO/SRA/ENA/BioProject accession IDs to identify the source dataset, "
                "publication, or study metadata. Standard bioinformatics database use "
                "(gene lookup, sequence annotation, reference downloads, BLAST) is allowed. "
                "Only inspect this task's workspace and installed tools. "
                "Never inspect other tasks, previous runs, or grading files."
                f"\n\n{environment}\nInput file inventory ({len(files)} files; first 100 shown):\n{inventory}"
            )
            tasks.append(Task(
                id=tid, benchmark=self.name, question=question,
                payload={"allowed_domains": row["allowed_domains"]},
                answer=None,
                meta={"human_solvable": row["human_solvable"], "split": split},
            ))
        return self.subsample(tasks, limit, seed)

    def workspace(self, task: Task, path: Path):
        source = (self.root / "files" / task.id).resolve()
        if not source.is_relative_to((self.root / "files").resolve()):
            raise ValueError("Task data path escapes files directory")
        if any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("Symlinks are not allowed in task data")
        shutil.copytree(source, path / "data", dirs_exist_ok=True)

    def probes(self, task):
        # The configured run_shell uses the runner's seeded workspace. Avoid
        # the default PythonProbe, whose cwd is not the task workspace.
        return []

    def sandbox(self, task):
        if not self.opts.get("isolate", False):
            return None
        if not isolation.available():
            raise RuntimeError("isolate=true requires Linux bubblewrap; use false only for local debugging")
        # The runner mounts the seeded workspace separately, not the data root.
        return Sandbox.of()

    async def score(self, task: Task, pred: Prediction, ctx: RunContext):
        # Required by the upstream Benchmark interface; no grading is performed.
        # The runner still writes scores.jsonl, so explicitly label its zero as
        # a framework placeholder rather than an accuracy measurement.
        return Score(task_id=task.id, score=0.0,
                     detail={"status": "not_evaluated",
                             "reason": "Automatic grading is not implemented; use predictions.jsonl for answers."})

    def check(self):
        if not (self.root / "problems.csv").is_file():
            return ["Run python scripts/prepare_biomysterybench.py first"]
        try:
            self.load()
        except (OSError, ValueError, KeyError) as exc:
            return [str(exc)]
        return []
