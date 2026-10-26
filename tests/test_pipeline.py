import asyncio
import json
import os
import urllib.error

import pytest

from bmbench import grader, runner
from bmbench.config import GraderConfig, RunConfig
from bmbench.dataset import PREPARED_MARKER, task_data_dir
from bmbench.runner import TaskData, remove_workspace, trial_dir


def test_remove_workspace_lists_then_deletes(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "data").mkdir(parents=True)
    (ws / "data" / "in.fa").write_text(">a\nACGT\n")
    (ws / "ref").mkdir()
    (ws / "ref" / "hg38.fa").write_bytes(b"N" * 1000)
    os.chmod(ws / "ref", 0o500)  # an agent took away its own write permission
    listing = tmp_path / "workspace_files.tsv"
    remove_workspace(ws, listing)
    assert not ws.exists()
    assert listing.read_text().splitlines() == ["path\tbytes", "data/in.fa\t8", "ref/hg38.fa\t1000"]


class _Reply:
    def __init__(self, content, usage=None, cost=None):
        self.content, self.usage, self.cost = content, usage or {}, cost


def _fake_post(replies, seen):
    def post(url, key, body, timeout_s):
        seen.append((url, key, body))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"choices": [{"message": {"content": r.content}}], "usage": r.usage}, r.cost
    return post


def test_judge_sends_strict_schema_and_prices_tokens(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-123456")
    seen = []
    verdict = {"correct": True, "extracted_answer": "CTCF", "normalized_answer": "CTCF", "rationale": "match"}
    monkeypatch.setattr(grader, "_post", _fake_post(
        [_Reply(json.dumps(verdict), {"prompt_tokens": 1_000_000, "completion_tokens": 100_000})], seen))
    res = asyncio.run(grader.judge("p", grader.ANSWER_SCHEMA, GraderConfig()))
    assert res["verdict"] == verdict and res["cost_usd"] == pytest.approx(2.0 + 1.0)
    url, key, body = seen[0]
    assert url == "https://proxy.example/v1/chat/completions" and key == "sk-test-123456"
    assert body["model"] == "gpt-6-sol" and body["reasoning_effort"] == "medium"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_judge_uses_reported_cost_and_does_not_retry_auth(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.example/v1/chat/completions")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-123456")
    seen = []
    verdict = {"cheated": False, "evidence": "", "rationale": "ok"}
    monkeypatch.setattr(grader, "_post", _fake_post([_Reply(json.dumps(verdict), cost="0.0123")], seen))
    res = asyncio.run(grader.judge("p", grader.AUDIT_SCHEMA, GraderConfig(effort=None)))
    assert res["cost_usd"] == pytest.approx(0.0123)
    assert seen[0][0] == "https://proxy.example/v1/chat/completions" and "reasoning_effort" not in seen[0][2]

    seen.clear()
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    err.read = lambda: b'{"error": "bad key"}'
    monkeypatch.setattr(grader, "_post", _fake_post([err, err], seen))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        asyncio.run(grader.judge("p", grader.AUDIT_SCHEMA, GraderConfig()))
    assert len(seen) == 1


def test_judge_needs_endpoint(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
        grader.judge_endpoint()


def test_task_data_downloads_one_question_ahead_and_deletes_when_done(tmp_path, monkeypatch):
    cfg = RunConfig(n_trials=2)
    cfg.dataset.root = str(tmp_path / "data")
    run_dir = tmp_path / "run"
    downloads = []

    def fake_prepare(name, root, ids):
        (task_id,) = ids
        downloads.append(task_id)
        d = task_data_dir(name, task_id, root)
        d.mkdir(parents=True)
        (d / PREPARED_MARKER).touch()

    def finish(task_id, k, status="completed"):
        tdir = trial_dir(run_dir, task_id, k)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "result.json").write_text(json.dumps({"status": status}))

    monkeypatch.setattr(runner, "prepare_tasks", fake_prepare)

    async def scenario():
        data = TaskData(cfg, run_dir, {"A": 2, "B": 2, "C": 0})
        await data.acquire("A")
        assert downloads == ["A"]
        await data.acquire("A")  # A's last trial starts: B downloads in the background
        await asyncio.sleep(0.05)
        assert downloads == ["A", "B"]
        finish("A", 1)
        data.release("A")
        assert task_data_dir("preview", "A", cfg.dataset.root).exists()  # a trial is still running
        finish("A", 2, status="rate_limited")
        data.release("A")
        assert task_data_dir("preview", "A", cfg.dataset.root).exists()  # kept for the resumed run
        await data.acquire("B")
        await data.acquire("B")
        for k in (1, 2):
            finish("B", k)
            data.release("B")
        assert not task_data_dir("preview", "B", cfg.dataset.root).exists()
        assert downloads == ["A", "B"]  # C had nothing left to run

    asyncio.run(scenario())
