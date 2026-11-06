"""The SHELDON adapter on the host: invocation, parsing, SHELDON's records, the runner flow."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import pytest

from bmbench import runner
from bmbench.agents.base import make_agent
from bmbench.agents.sheldon import SHELDON_COMMIT, Recorded, SheldonAgent, traces
from bmbench.config import load_config
from bmbench.dataset import PREPARED_MARKER, Problem, problem_data_dir
from bmbench.sandbox import ExecResult
from bmbench.trajectory import Segment, action_log, tool_counts

ROOT = Path(__file__).resolve().parents[1]


def agent() -> SheldonAgent:
    return make_agent(load_config("configs/sheldon_preview.yaml").agent)


def test_commit_matches_runtime_and_image():
    runtime = (ROOT / "bmbench/agents/sheldon_runtime/bmb_sheldon/__init__.py").read_text()
    dockerfile = (ROOT / "docker/sandbox/Dockerfile").read_text()
    assert f'SHELDON_COMMIT = "{SHELDON_COMMIT}"' in runtime
    assert f"ARG SHELDON_COMMIT={SHELDON_COMMIT}" in dockerfile


def test_command_env_and_run_name(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://models.example.edu/v1/chat/completions")
    a = agent()
    assert a.command(timeout_s=3600, session_id="x") == [
        "timeout", "-k", "30", "3900", "/opt/sheldon/.venv/bin/python", "-m", "bmb_sheldon.driver",
        "--time-limit", "3600"]
    assert not a.resumable and a.closing_s == 300 and a.secret_env == ("LITELLM_API_KEY",)
    assert a.infra_domains == ("models.example.edu",)
    env = a.env()
    assert env["LITELLM_BASE_URL"] == "https://models.example.edu/v1"
    conf = json.loads(env["BMB_SHELDON_CONFIG"])
    sh = conf["sheldon"]
    assert (sh["model"], sh["effort"], sh["max_rounds"], sh["max_calls"], sh["max_usd"]) == ("gpt-6-luna", "high", 3, None, None)
    assert sh["params"]["mode"] == "session" and sh["params"]["select_k"] == 3
    assert sh["params"]["candidate_analysis_concurrent"] is True and sh["params"]["probes"] == ["run_shell"]
    assert conf["usd_per_mtok"] == [0.10, 0.01, 0.50] and conf["workdir"] == "/workspace"
    assert "LITELLM_API_KEY" not in json.dumps(env)
    cfg = load_config("configs/sheldon_preview.yaml")
    assert cfg.run_name == "preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts"


def _driver_output(tmp_path, *events, stderr=""):
    out, err = tmp_path / "stream.main.jsonl", tmp_path / "stderr.main.log"
    out.write_text("".join(json.dumps(e) + "\n" for e in events))
    err.write_text(stderr)
    return out, err


START = {"type": "start", "sheldon_commit": SHELDON_COMMIT, "model": "gpt-6-luna", "effort": "high", "mode": "session"}


def test_parse_answer_from_tags_or_the_whole_field(tmp_path):
    usage = {"calls": 40, "input_tokens": 900_000, "output_tokens": 100_000, "cache_read_tokens": 100_000}
    out, err = _driver_output(tmp_path, START, {"type": "result", "answer": "The species is <answer>Homo sapiens</answer>",
                                               "error": "", "rounds": 3, "usage": usage, "time_limit_hit": False})
    seg = agent().parse(out, err, Segment(kind="main"))
    assert seg.answer == "Homo sapiens" and seg.got_result and not seg.is_error and not seg.timed_out
    assert seg.num_turns == 40 and seg.usage["cache_read_input_tokens"] == 100_000
    assert seg.cost_usd == pytest.approx((900_000 * 0.10 + 100_000 * 0.01 + 100_000 * 0.50) / 1e6)
    out, err = _driver_output(tmp_path, {"type": "result", "answer": "CTCF", "error": "", "usage": {}})
    assert agent().parse(out, err, Segment(kind="main")).answer == "CTCF"  # the structured field, untagged


def test_parse_time_limit_and_errors(tmp_path):
    out, err = _driver_output(tmp_path, {"type": "result", "answer": "<answer>X</answer>", "error": "",
                                         "time_limit_hit": True, "seconds_after_limit": 42.0, "usage": {}})
    seg = agent().parse(out, err, Segment(kind="main"))
    assert seg.timed_out and seg.closed_out and seg.answer == "X"
    out, err = _driver_output(tmp_path, {"type": "result", "answer": "", "error": "AuthenticationError: 401 invalid api key",
                                         "usage": {}})
    seg = agent().parse(out, err, Segment(kind="main"))
    assert seg.is_error and "401" in seg.error_text and seg.answer is None
    out, err = _driver_output(tmp_path, START, stderr="Traceback ...")  # killed at the hard limit
    seg = agent().parse(out, err, Segment(kind="main", exit_code=124))
    assert seg.timed_out and not seg.got_result and not seg.closed_out


def _usage(n_in=100, n_out=10):
    return {"calls": 1, "input_tokens": n_in, "output_tokens": n_out, "cache_read_tokens": 5, "cache_write_tokens": 0,
            "cost_usd": 0.0}


def write_run(root: Path) -> Path:
    """A SHELDON run as the sandbox leaves it: trace + retrieval files."""
    run = root / "sheldon" / "runs" / "biomysterybench__sheldon__attempt__bmbench__abc"
    events = [
        {"kind": "tools", "available": ["run_shell"]},
        {"kind": "llm", "tag": "plan", "text": "{}", "parsed": {"situation": "Q", "deliverable": "tags"}, "usage": _usage()},
        {"kind": "plan", "accepted": True},
        {"kind": "llm", "tag": "session.explore", "text": "", "usage": _usage(),
         "tool_use": [{"name": "run_shell", "input": {"command": "ls data"}}]},
        {"kind": "llm", "tag": "session.explore", "text": "", "usage": _usage(),
         "tool_use": [{"name": "submit_exploration", "input": {"hypotheses": [{"statement": "bacterial"}]}}]},
        {"kind": "explore.done", "round": 1, "added": ["h1", "h2"], "revived": []},
        {"kind": "llm", "tag": "candidate_analysis.h1.1", "text": "", "usage": _usage(),
         "tool_use": [{"name": "run_shell", "input": {"command": "head -2 data/a.fa"}}]},
        {"kind": "llm", "tag": "candidate_analysis.h2.1", "text": "", "usage": _usage(),
         "tool_use": [{"name": "run_shell", "input": {"command": "ls data"}}]},  # redone by another test
        {"kind": "llm", "tag": "candidate_analysis.h1.1", "text": "", "usage": _usage(),
         "tool_use": [{"name": "submit_local", "input": {"notes": "looked"}}]},
        {"kind": "candidate_analysis.done", "round": 1, "hypothesis": "h1", "stop": "done", "iterations": 1, "final": "h1"},
        {"kind": "round.done", "round": 1, "tested": ["h1", "h2"], "shelved": [], "observations": 1},
        {"kind": "llm", "tag": "finalize", "text": "", "usage": _usage(),
         "tool_use": [{"name": "submit_final", "input": {"answer": "<answer>E. coli</answer>"}}]},
        {"kind": "finalize", "answer": "<answer>E. coli</answer>", "source": "answer"},
        {"kind": "sheldon.final", "hypotheses": [{"id": "h1", "statement": "bacterial", "status": "tested", "region": "taxa"}],
         "observations": [{"id": "o1", "content": "16S hit", "round": 1, "origin": "local", "region": "seq",
                           "verdicts": {"h1": {"relation": "supports", "why": "hit"}}}]},
    ]
    (run / "traces").mkdir(parents=True)
    (run / "traces" / "problem.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rdir = run / "retrievals" / "problem"
    rdir.mkdir(parents=True)
    rets = [("r1", "ls data", "a.fa"), ("r2", "head -2 data/a.fa", ">s1\nACGT"), ("r3", "ls data", "a.fa"),
            ("r4", "curl https://example.org", "not traced")]
    for rid, cmd, out in rets:
        (rdir / f"{rid}.json").write_text(json.dumps({"id": rid, "tool": "run_shell", "arguments": {"command": cmd},
                                                      "summary": out, "ok": True, "evidence": [{"exit": 0}]}))
    return run


def test_records_become_steps_for_the_audit(tmp_path):
    write_run(tmp_path)
    a = agent()
    (seg,) = a.subagent_segments(tmp_path)
    assert tool_counts([seg]) == {"Bash": 4}  # three traced calls and one only in the retrievals
    uses = [s for s in seg.steps if s.kind == "tool_use"]
    assert [u.tool_input["command"] for u in uses] == ["ls data", "head -2 data/a.fa", "ls data", "curl https://example.org"]
    assert [u.tool_use_id for u in uses] == ["r1", "r2", "r3", "r4"]
    assert uses[1].parent == "candidate_analysis.h1.1" and uses[0].parent is None  # sub-agents vs deciding stages
    log = action_log([seg], 10_000)
    assert "head -2 data/a.fa" in log and "⇒ >s1" in log
    assert any(s.kind == "text" and "submit_final" in s.text for s in seg.steps)
    usage = a.session_usage(tmp_path)
    assert usage["input_tokens"] == 700 and usage["output_tokens"] == 70 and usage["api_messages"] == 7


def test_transcript_and_command_stats(tmp_path):
    write_run(tmp_path)
    a = agent()
    segs = [Segment(kind="main", info={"start": START, "result": {"rounds": 1, "stop_reason": "max_rounds"}})]
    md = a.transcript("hb002", 1, segs, tmp_path)
    for heading in ("## Plan", "## Round 1", "### explore", "### candidate analysis · h1 · iteration 1",
                    "### candidate analysis · h2 · iteration 1", "## Finalize", "## Final state"):
        assert heading in md
    assert md.index("## Plan") < md.index("## Round 1") < md.index("## Finalize")
    assert "round 1 done: tested h1, h2" in md and "| h1 | tested | taxa | bacterial |" in md
    assert "o1] (round 1, local, seq) 16S hit — h1 supports" in md
    extra = a.result_extra(tmp_path, segs)
    assert extra["commands"] == 3 and extra["commands_repeated"] == 1 and extra["rounds"] == 1


def test_compacted_traces_still_read(tmp_path):
    run = write_run(tmp_path)
    a = agent()
    a.compact_session(tmp_path)
    assert not list(run.rglob("*.jsonl")) and (run / "traces" / "problem.jsonl.gz").is_file()
    with gzip.open(run / "traces" / "problem.jsonl.gz", "rt") as f:
        assert '"kind": "plan"' in f.read()
    assert traces(tmp_path) and Recorded(traces(tmp_path)[0]).command_stats()["commands"] == 3
    assert a.session_usage(tmp_path)["api_messages"] == 7


# -- the runner with a non-resumable agent ---------------------------------------------------


class _Box:
    script: list = []

    def __init__(self, **_kw):
        self.calls, self.deadlines, self.pkills = [], [], 0

    async def start(self): pass
    async def stop(self): pass
    async def copy_out(self, *_a): return False
    async def proxy_log(self, *_a): return False
    async def oom_kills(self): return 0

    async def kill_agent_processes(self):
        self.pkills += 1

    async def exec(self, argv, *, stdin_path, stdout_path, deadline_s, **_kw):
        code, seconds, lines = type(self).script[len(self.calls)]
        self.calls.append(argv)
        self.deadlines.append(deadline_s)
        stdout_path.write_text("".join(json.dumps(e) + "\n" for e in lines))
        return ExecResult(code=code, timed_out=False, seconds=seconds)


def _attempt(tmp_path, monkeypatch, script):
    cfg = load_config("configs/sheldon_preview.yaml")
    cfg.dataset.root = str(tmp_path / "data")
    src = problem_data_dir(cfg.dataset.name, "hb001", cfg.dataset.root)
    src.mkdir(parents=True)
    (src / "reads.fq").write_text("@r\nACGT\n+\nIIII\n")
    (src / PREPARED_MARKER).write_text("")
    boxes = []
    _Box.script = script
    monkeypatch.setattr(runner, "DockerSandbox", lambda **kw: boxes.append(_Box(**kw)) or boxes[-1])
    problem = Problem("hb001", "Which gene?", "GENEA", ("ncbi.nlm.nih.gov",), "yes")
    result = asyncio.run(runner.run_attempt(cfg, make_agent(cfg.agent), problem, 1, tmp_path / "run"))
    return result, boxes[0]


def test_time_limit_closed_out_by_sheldon_itself(tmp_path, monkeypatch):
    final = {"type": "result", "answer": "<answer>GENEA</answer>", "error": "", "rounds": 2,
             "stop_reason": "budget:max_seconds=3600", "time_limit_hit": True, "seconds_after_limit": 95.0,
             "usage": {"calls": 80, "input_tokens": 1000, "output_tokens": 100}}
    result, box = _attempt(tmp_path, monkeypatch, [(0, 3695.0, [START, final])])
    assert len(box.calls) == 1 and box.pkills == 0  # no resume turn of any kind
    assert box.deadlines == [3600 + 300 + 120]
    assert result["status"] == "completed" and result["answer"] == "GENEA"
    assert result["timed_out"] and result["finalized_after_timeout"]
    assert result["sheldon"]["rounds"] == 2 and result["sheldon"]["seconds_after_limit"] == 95.0
    assert result["cost_usd"] == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6)


def test_killed_at_the_hard_limit_is_a_timeout_without_answer(tmp_path, monkeypatch):
    result, box = _attempt(tmp_path, monkeypatch, [(124, 3900.0, [START])])
    assert len(box.calls) == 1
    assert result["status"] == "timeout" and result["answer"] is None and not result["finalized_after_timeout"]


def test_no_nudge_for_an_empty_answer(tmp_path, monkeypatch):
    empty = {"type": "result", "answer": "", "error": "", "rounds": 3, "usage": {}}
    result, box = _attempt(tmp_path, monkeypatch, [(0, 1200.0, [START, empty])])
    assert len(box.calls) == 1 and result["status"] == "no_answer" and result["nudges"] == 0
