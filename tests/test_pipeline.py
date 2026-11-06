import asyncio
import json
import os
import urllib.error

import pytest

from bmbench import grader, runner
from bmbench.config import GraderConfig, RunConfig
from bmbench.dataset import PREPARED_MARKER, problem_data_dir
from bmbench.runner import ProblemData, remove_workspace, attempt_dir
from bmbench.sandbox import ExecResult


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


def test_only_session_logs_and_nonempty_stderr_are_kept(tmp_path):
    from bmbench.agents.claude_code import ClaudeCodeAgent
    from bmbench.agents.codex import CodexAgent
    from bmbench.config import AgentConfig

    assert ClaudeCodeAgent(AgentConfig()).session_dirs == ("/home/agent/.claude/projects",)
    assert CodexAgent(AgentConfig(type="codex")).session_dirs == ("/home/agent/.codex/sessions",)
    (tmp_path / "stderr.main.log").write_text("")
    (tmp_path / "stderr.finalize.log").write_text("warning: something\n")
    (tmp_path / "stream.main.jsonl").write_text("")  # raw output is kept even when empty
    runner.remove_empty_logs(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stderr.finalize.log", "stream.main.jsonl"]


def test_flatten_moves_session_files_up(tmp_path):
    root = tmp_path / "agent_session"
    (root / "sessions" / "2026" / "11" / "05").mkdir(parents=True)
    (root / "sessions" / "2026" / "11" / "05" / "rollout-a.jsonl").write_text("a")
    (root / "projects" / "-workspace" / "s1" / "subagents").mkdir(parents=True)
    (root / "projects" / "-workspace" / "s1.jsonl").write_text("main")
    (root / "projects" / "-workspace" / "s1" / "subagents" / "s1.jsonl").write_text("clash")
    runner.flatten(root)
    assert sorted(p.name for p in root.iterdir()) == ["rollout-a.jsonl", "s1.jsonl", "subagents_s1.jsonl"]
    runner.flatten(tmp_path / "missing")  # no session copied: nothing to do


class _ScriptedBox:
    """A sandbox whose agent invocations follow a script: (exit code, seconds, memory
    kills meanwhile, stream-json lines the agent prints)."""

    script: list = []

    def __init__(self, **_kw):
        self.kills, self.calls, self.prompts, self.pkills = 0, [], [], 0

    async def start(self): pass
    async def stop(self): pass
    async def copy_out(self, *_a): return False
    async def proxy_log(self, *_a): return False
    async def oom_kills(self): return self.kills

    async def kill_agent_processes(self):
        self.pkills += 1

    async def exec(self, argv, *, stdin_path, stdout_path, **_kw):
        code, seconds, kills, lines = type(self).script[len(self.calls)]
        self.calls.append(argv)
        self.prompts.append(stdin_path.name)
        self.kills += kills
        stdout_path.write_text("".join(json.dumps(e) + "\n" for e in lines))
        return ExecResult(code=code, timed_out=False, seconds=seconds)


def _attempt(tmp_path, monkeypatch, script):
    from bmbench.agents.base import make_agent
    from bmbench.dataset import Problem

    cfg = RunConfig()
    cfg.dataset.root = str(tmp_path / "data")
    src = problem_data_dir(cfg.dataset.name, "hb001", cfg.dataset.root)
    src.mkdir(parents=True)
    (src / "reads.fq").write_text("@r\nACGT\n+\nIIII\n")
    (src / PREPARED_MARKER).write_text("")
    boxes = []
    _ScriptedBox.script = script
    monkeypatch.setattr(runner, "DockerSandbox", lambda **kw: boxes.append(_ScriptedBox(**kw)) or boxes[-1])
    problem = Problem("hb001", "Which gene?", "GENEA", ("ncbi.nlm.nih.gov",), "yes")
    result = asyncio.run(runner.run_attempt(cfg, make_agent(cfg.agent), problem, 1, tmp_path / "run"))
    return result, boxes[0]


_WORKING = {"type": "assistant", "message": {"id": "m1", "content": [
    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "mmseqs easy-cluster all.fa otu tmp"}}]}}
_ANSWER = {"type": "result", "subtype": "success", "is_error": False, "result": "<answer>GENEA</answer>",
           "num_turns": 3, "total_cost_usd": 0.1}


_TAGGED = {"type": "assistant", "message": {"id": "m0", "content": [{"type": "text", "text": "<answer>GENEB</answer>"}]}}


def test_agent_killed_for_memory_ends_the_attempt(tmp_path, monkeypatch):
    # the kernel killed mmseqs and then the agent CLI (exit 137) 200 s into a 1 h limit
    result, box = _attempt(tmp_path, monkeypatch, [(137, 200.0, 2, [_WORKING])])
    assert [s["kind"] for s in result["segments"]] == ["main"] and len(box.calls) == 1  # no resume, no closing turn
    assert result["status"] == "out_of_memory" and result["answer"] is None
    assert result["out_of_memory"] and not result["timed_out"]
    assert result["oom_kills"] == 2 and result["agent_oom_kills"] == 1


def test_an_answer_written_before_a_memory_kill_does_not_count(tmp_path, monkeypatch):
    result, _ = _attempt(tmp_path, monkeypatch, [(137, 200.0, 1, [_TAGGED, _WORKING])])
    assert result["status"] == "out_of_memory" and result["answer"] is None


def test_commands_killed_for_memory_do_not_end_the_attempt(tmp_path, monkeypatch):
    # mmseqs was killed for memory, the agent saw the error and carried on to an answer
    result, box = _attempt(tmp_path, monkeypatch, [(0, 900.0, 3, [_WORKING, _ANSWER])])
    assert result["status"] == "completed" and result["answer"] == "GENEA"
    assert result["oom_kills"] == 3 and result["agent_oom_kills"] == 0 and not result["out_of_memory"]


def test_kill_at_the_time_limit_is_still_a_timeout(tmp_path, monkeypatch):
    # `timeout` sends SIGKILL (137) 30 s after the limit when the agent ignores SIGTERM
    result, box = _attempt(tmp_path, monkeypatch, [(137, 3630.0, 0, [_WORKING]), (0, 10.0, 0, [_ANSWER])])
    assert [s["kind"] for s in result["segments"]] == ["main", "finalize"]
    assert result["timed_out"] and result["finalized_after_timeout"] and result["agent_oom_kills"] == 0
    assert box.prompts[1] == "finalize.md" and box.calls[1][3] == "300"  # the shared 5 min closing window


def test_scrub_secrets_in_small_and_large_files(tmp_path):
    (tmp_path / "small.json").write_text('{"key": "sk-secret-123456"}')
    (tmp_path / "big.jsonl").write_text("x" * 200 + "\n" + "token sk-secret-123456 here\n" * 50)
    (tmp_path / "clean.txt").write_text("nothing to see")
    runner.scrub_secrets(tmp_path, ["sk-secret-123456"], max_bytes=100)  # big.jsonl goes line by line
    assert "sk-secret" not in (tmp_path / "small.json").read_text()
    big = (tmp_path / "big.jsonl").read_text()
    assert "sk-secret" not in big and big.count("[REDACTED]") == 50
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.jsonl", "clean.txt", "small.json"]
