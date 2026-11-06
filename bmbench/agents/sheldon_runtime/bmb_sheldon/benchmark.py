"""BioMysteryBench as a SHELDON benchmark: one problem, posed exactly as bmbench poses it.

SHELDON's extension point for a new benchmark is a `Benchmark` subclass registered
by name; this is that, and nothing in SHELDON is edited. One instance serves one
attempt at one problem:

  task      `question` is the prompt bmbench renders from prompts/problem.md, the same
            text Claude Code and Codex get. There is no answer: the answer key never
            enters the sandbox, and grading happens on the host, as for every agent.
  tools     none of the benchmark's own. The base class would add a `run_python`
            sandbox that runs in SHELDON's interpreter (no bio packages) for 10 s; the
            run's one tool is SHELDON's generic `run_shell`, opening in /workspace.
  sandbox   none: the Docker container around SHELDON is the isolation boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sheldon.benchmarks.base import Benchmark
from sheldon.core.registry import benchmarks
from sheldon.core.types import Prediction, RunContext, Score, Task

NAME = "biomysterybench"
#: repeats the answer instruction of prompts/problem.md, for SHELDON's finalize stage
ANSWER_FORMAT = (
    "Give one specific answer, in the form the question asks for, inside answer tags, "
    "like this: `<answer>your answer</answer>`."
)


@benchmarks.register(NAME)
class BioMysteryBench(Benchmark):
    name = NAME
    splits = ("attempt",)
    answer_kind = "text"
    answer_format = ANSWER_FORMAT
    blurb = "BioMysteryBench, one problem per run, as posed by bmbench (graded on the host)"

    def _task_file(self) -> Path:
        path = self.opts.get("task_file")
        if not path:
            raise ValueError(f"{NAME}: benchmark_opts.task_file is required")
        return Path(str(path))

    def load(self, split: str = "attempt", limit: int | None = None, seed: int = 0) -> list[Task]:
        data = json.loads(self._task_file().read_text(encoding="utf-8"))
        return [Task(id=str(data["id"]), benchmark=self.name, question=str(data["question"]), answer=None)]

    def probes(self, task: Task) -> list[Any]:
        return []

    def sandbox(self, task: Task) -> None:
        return None

    def parse_answer(self, text: str) -> Any:
        return (text or "").strip()

    async def score(self, task: Task, pred: Prediction, ctx: RunContext) -> Score:
        # SHELDON's runner needs a score row; the judge runs on the host.
        return Score(task_id=task.id, score=0.0, detail={"graded_by": "bmbench, on the host"})

    def check(self) -> list[str]:
        try:
            path = self._task_file()
        except ValueError as e:
            return [str(e)]
        return [] if path.is_file() else [f"{NAME}: task file not found: {path}"]
