"""Offline adapter checks; synthetic labels only, no credentials or API calls."""
import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sheldon.benchmarks.biomysterybench import BioMysteryBench
from sheldon.core.probes import ShellProbe
from sheldon.core.types import Prediction


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "benchmark"
        data = self.root / "files" / "synthetic"
        data.mkdir(parents=True)
        (data / "reads.fasta").write_text(">anonymous\nACGT\n")
        with (self.root / "problems.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "question", "answer_rubric", "allowed_domains", "human_solvable"])
            writer.writeheader()
            writer.writerow(dict(id="synthetic", question="Identify the organism.",
                                 answer_rubric="SECRET_GOLD_SENTINEL", allowed_domains="ncbi.nlm.nih.gov", human_solvable="yes"))
        self.bench = BioMysteryBench(root=self.root)
        self.task = self.bench.load()[0]

    def test_blinding_and_workspace_shell(self):
        blind = self.task.blind()
        self.assertIsNone(blind.answer)
        self.assertIn("data/reads.fasta", blind.question)
        self.assertIn("find does not support -printf", blind.question)
        self.assertNotIn("SECRET_GOLD_SENTINEL", json.dumps(blind.to_dict()))
        self.assertIsNone(self.bench.verifier(blind))
        work = Path(self.tmp.name) / "workspace"
        work.mkdir()
        self.bench.workspace(blind, work)
        self.assertEqual([str(p.relative_to(work)) for p in work.rglob("*") if p.is_file()], ["data/reads.fasta"])
        result = asyncio.run(ShellProbe(cwd=work).run(command="cat data/reads.fasta"))
        self.assertTrue(result.ok)
        self.assertIn("ACGT", result.summary)

    def test_no_automatic_grading_or_answer_key(self):
        self.assertIsNone(self.task.answer)
        # Legacy options must not cause an API call; ctx has no llm or trace.
        bench = BioMysteryBench(root=self.root, judge_model="unused-legacy-option")
        score = asyncio.run(bench.score(self.task, Prediction(task_id=self.task.id, answer="guess"), SimpleNamespace()))
        self.assertEqual(score.detail["status"], "not_evaluated")
        self.assertEqual(score.metrics, {})

    def test_isolation_fails_closed(self):
        bench = BioMysteryBench(root=self.root, isolate=True)
        with patch("sheldon.benchmarks.biomysterybench.isolation.available", return_value=False):
            with self.assertRaises(RuntimeError):
                bench.sandbox(self.task.blind())

    def test_rejects_path_traversal(self):
        with (self.root / "problems.csv").open("w") as f:
            f.write("id,question,answer_rubric,allowed_domains,human_solvable\n../escape,q,gold,,yes\n")
        with self.assertRaises(ValueError):
            self.bench.load()


if __name__ == "__main__":
    unittest.main()
