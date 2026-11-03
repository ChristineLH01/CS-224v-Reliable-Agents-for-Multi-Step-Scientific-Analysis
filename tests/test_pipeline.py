import asyncio
import json
import os
import urllib.error

import pytest

from bmbench import grader, runner
from bmbench.config import GraderConfig, RunConfig
from bmbench.dataset import PREPARED_MARKER, problem_data_dir
from bmbench.runner import ProblemData, remove_workspace, attempt_dir


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
    res = asyncio.run(grader.judge("p", grader.ANSWER_SCHEMA, GraderConfig(model="gpt-6.1-sol")))
    assert res["verdict"] == verdict and res["cost_usd"] == pytest.approx(2.0 + 1.0)
    url, key, body = seen[0]
    assert url == "https://proxy.example/v1/chat/completions" and key == "sk-test-123456"
    assert body["model"] == "gpt-6.1-sol" and body["reasoning_effort"] == "medium"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_judge_uses_reported_cost_and_does_not_retry_auth(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.example/v1/chat/completions")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-123456")
    seen = []
    verdict = {"cheated": False, "evidence": "", "rationale": "ok"}
    monkeypatch.setattr(grader, "_post", _fake_post([_Reply(json.dumps(verdict), cost="0.0123")], seen))
    res = asyncio.run(grader.judge("p", grader.AUDIT_SCHEMA, GraderConfig(model="gpt-6.1-sol", effort=None)))
    assert res["cost_usd"] == pytest.approx(0.0123)
    assert seen[0][0] == "https://proxy.example/v1/chat/completions" and "reasoning_effort" not in seen[0][2]

    seen.clear()
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    err.read = lambda: b'{"error": "bad key"}'
    monkeypatch.setattr(grader, "_post", _fake_post([err, err], seen))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        asyncio.run(grader.judge("p", grader.AUDIT_SCHEMA, GraderConfig(model="gpt-6.1-sol")))
    assert len(seen) == 1


def test_judge_needs_endpoint(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
        grader.judge_endpoint()


def test_problem_data_downloads_one_problem_ahead_and_deletes_when_done(tmp_path, monkeypatch):
    cfg = RunConfig(n_attempts=2)
    cfg.dataset.root = str(tmp_path / "data")
    run_dir = tmp_path / "run"
    downloads = []

    def fake_prepare(name, root, ids):
        (problem_id,) = ids
        downloads.append(problem_id)
        d = problem_data_dir(name, problem_id, root)
        d.mkdir(parents=True)
        (d / PREPARED_MARKER).touch()

    def finish(problem_id, k, status="completed"):
        adir = attempt_dir(run_dir, problem_id, k)
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "result.json").write_text(json.dumps({"status": status}))

    monkeypatch.setattr(runner, "prepare_problems", fake_prepare)

    async def scenario():
        data = ProblemData(cfg, run_dir, {"A": 2, "B": 2, "C": 0})
        await data.acquire("A")
        assert downloads == ["A"]
        await data.acquire("A")  # A's last attempt starts: B downloads in the background
        await asyncio.sleep(0.05)
        assert downloads == ["A", "B"]
        finish("A", 1)
        data.release("A")
        assert problem_data_dir("preview", "A", cfg.dataset.root).exists()  # an attempt is still running
        finish("A", 2, status="rate_limited")
        data.release("A")
        assert problem_data_dir("preview", "A", cfg.dataset.root).exists()  # kept for the resumed run
        await data.acquire("B")
        await data.acquire("B")
        for k in (1, 2):
            finish("B", k)
            data.release("B")
        assert not problem_data_dir("preview", "B", cfg.dataset.root).exists()
        assert downloads == ["A", "B"]  # C had nothing left to run

    asyncio.run(scenario())


def test_claude_judge_runs_claude_p_with_auth_by_name(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test-0123456789")
    seen = []
    verdict = {"cheated": False, "evidence": "", "rationale": "ok"}

    class Proc:
        async def communicate(self, data):
            seen.append(data)
            return json.dumps({"is_error": False, "structured_output": verdict, "total_cost_usd": 0.05,
                               "usage": {"input_tokens": 10, "cache_read_input_tokens": 90,
                                         "output_tokens": 30}}).encode(), b""

    async def fake_exec(*argv, **_kw):
        seen.append(argv)
        return Proc()

    monkeypatch.setattr(grader.asyncio, "create_subprocess_exec", fake_exec)
    res = asyncio.run(grader.judge("audit this", grader.AUDIT_SCHEMA, GraderConfig(), "img:latest"))
    argv = seen[0]
    assert grader.uses_claude(GraderConfig()) and not grader.uses_claude(GraderConfig(model="gpt-6.1-sol"))
    assert "img:latest" in argv and argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert "--json-schema" in argv and argv[argv.index("--tools") + 1] == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" in argv and not any("sk-ant" in a for a in argv)  # by name only
    assert seen[1] == b"audit this"
    assert res["verdict"] == verdict and res["cost_usd"] == 0.05
    assert res["usage"] == {"prompt_tokens": 100, "completion_tokens": 30}


def test_one_prompt_per_question_and_changes_are_refused(tmp_path):
    task = tmp_path / "problems" / "hb035"
    path = runner.problem_prompt(task, "the prompt")
    assert path == task / "prompt.md" and runner.problem_prompt(task, "the prompt") == path  # later attempts reuse it
    with pytest.raises(runner.PromptChanged, match="new --run-name"):
        runner.problem_prompt(task, "a prompt with another time limit")
    assert path.read_text() == "the prompt"


def test_run_names_follow_the_settings():
    from bmbench.config import load_config

    assert load_config("configs/claude_code_preview.yaml").run_name == \
        "preview_claude-code_opus-5-5_high_timeout-1h_3-attempts"
    assert load_config("configs/codex_full.yaml", ["sandbox.timeout_s=5400", "n_attempts=1"]).run_name == \
        "full_codex_gpt-6-1-sol_high_timeout-90min_1-attempt"
    assert load_config("configs/codex_full.yaml", ["run_name=mine"]).run_name == "mine"
    assert attempt_dir(tmp := runner.Path("/r"), "hb035", 2) == tmp / "problems" / "hb035" / "attempt-2"
