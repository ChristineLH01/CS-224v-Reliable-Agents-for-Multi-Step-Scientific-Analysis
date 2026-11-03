import json

import pytest

from bmbench.agents.base import make_agent
from bmbench.agents.claude_code import ClaudeCodeAgent
from bmbench.agents.codex import CodexAgent
from bmbench.config import AgentConfig, load_config
from bmbench.trajectory import Segment, final_answer

# synthetic events in `codex exec --json` format (0.166)
EVENTS = [
    {"type": "thread.started", "thread_id": "01a1aafe-0000-7000-8000-000000000001"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "I'll inspect the data."}},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/bash -lc 'ls data'",
                                      "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/bash -lc 'ls data'",
                                        "aggregated_output": "reads.fa\n", "exit_code": 0, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "command_execution",
                                        "command": "/bin/bash -lc \"curl -sS https://www.example.com/\"",
                                        "aggregated_output": "curl: (7) CONNECT tunnel failed, response 403\n",
                                        "exit_code": 7, "status": "failed"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "file_change", "status": "completed",
                                        "changes": [{"path": "/workspace/analysis.py", "kind": "add"}]}},
    {"type": "item.completed", "item": {"id": "item_4", "type": "reasoning", "text": "Compare against the reference."}},
    {"type": "item.completed", "item": {"id": "item_5", "type": "agent_message",
                                        "text": "The data points to GENEA.\n<answer>GENEA</answer>"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600,
                                         "cache_write_input_tokens": 100, "output_tokens": 50,
                                         "reasoning_output_tokens": 10}},
]


def _parse(tmp_path, events, exit_code=0):
    out = tmp_path / "stream.jsonl"
    out.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    agent = CodexAgent(AgentConfig(type="codex", model="gpt-6.1-sol", usd_per_mtok=[2.0, 0.1, 10.0]))
    return agent.parse(out, tmp_path / "stderr.log", Segment(kind="main", exit_code=exit_code))


def test_codex_command(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://models.example.edu/v1/chat/completions")
    agent = CodexAgent(AgentConfig(type="codex", model="gpt-6.1-sol", effort="high"))
    argv = agent.command(timeout_s=3600, session_id="ignored")
    assert argv[:6] == ["timeout", "-k", "30", "3600", "codex", "exec"] and argv[-1] == "-"
    assert "--json" in argv and "--dangerously-bypass-approvals-and-sandbox" in argv
    assert argv[argv.index("-m") + 1] == "gpt-6.1-sol"
    configs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert "model_reasoning_effort=high" in configs and 'web_search="disabled"' in configs
    assert 'model_providers.bmbench_litellm.base_url="https://models.example.edu/v1"' in configs
    assert 'model_providers.bmbench_litellm.env_key="LITELLM_API_KEY"' in configs
    resumed = agent.command(timeout_s=180, session_id="ignored", resume=True)
    assert resumed[5:7] == ["exec", "resume"] and resumed[-2:] == ["--last", "-"]
    assert agent.secret_env == ("LITELLM_API_KEY",) and agent.infra_domains == ("models.example.edu",)
    assert ClaudeCodeAgent(AgentConfig()).secret_env != agent.secret_env  # never each other's credentials


def test_codex_command_needs_the_proxy_url(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    agent = CodexAgent(AgentConfig(type="codex"))
    assert agent.infra_domains == ()
    with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
        agent.command(timeout_s=60, session_id="x")


def test_parse_codex_events(tmp_path):
    seg = _parse(tmp_path, EVENTS)
    assert seg.session_id == "01a1aafe-0000-7000-8000-000000000001"
    assert seg.got_result and not seg.is_error and not seg.timed_out
    kinds = [s.kind for s in seg.steps]
    assert kinds == ["text", "tool_use", "tool_result", "tool_use", "tool_result", "tool_use", "tool_result",
                     "thinking", "text"]
    assert seg.steps[1].tool == "Bash" and seg.steps[1].tool_input == {"command": "ls data"}
    assert seg.steps[3].tool_input == {"command": "curl -sS https://www.example.com/"}
    assert seg.steps[4].is_error and "[exit code 7]" in seg.steps[4].text
    assert seg.steps[5].tool == "FileChange"
    assert final_answer([seg])[0] == "GENEA"
    assert seg.num_turns == 4  # three tool calls + the final message
    assert seg.usage == {"input_tokens": 300, "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100,
                         "output_tokens": 50, "reasoning_output_tokens": 10}
    assert seg.cost_usd == pytest.approx((400 * 2.0 + 600 * 0.1 + 50 * 10.0) / 1e6)


def test_parse_codex_failure_and_timeout(tmp_path):
    failed = _parse(tmp_path, [EVENTS[0], {"type": "error", "message": "exceeded retry limit, last status: 429"},
                               {"type": "turn.failed", "error": {"message": "exceeded retry limit, last status: 429"}}],
                    exit_code=1)
    assert failed.is_error and "429" in failed.error_text
    killed = _parse(tmp_path, EVENTS[:5], exit_code=124)
    assert killed.timed_out and not killed.got_result and killed.final_text == "I'll inspect the data."
    # a retried stream error inside a turn that then completes is not an error
    recovered = _parse(tmp_path, EVENTS[:3] + [{"type": "error", "message": "Reconnecting... 1/5"}] + EVENTS[3:])
    assert not recovered.is_error and recovered.error_text == ""


def test_codex_session_usage_takes_each_sessions_total(tmp_path):
    def rollout(name, session, totals):
        lines = [{"type": "session_meta", "payload": {"id": session}}]
        lines += [{"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": t}}}
                  for t in totals]
        (tmp_path / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    t1 = {"input_tokens": 100, "cached_input_tokens": 50, "cache_write_input_tokens": 0, "output_tokens": 10,
          "total_tokens": 110}
    t2 = {"input_tokens": 300, "cached_input_tokens": 200, "cache_write_input_tokens": 50, "output_tokens": 30,
          "total_tokens": 330}
    rollout("main.jsonl", "s-main", [t1, t2])  # cumulative: the last/largest counts
    rollout("sub.jsonl", "s-sub", [t1])        # a subagent's own session adds to it
    agent = CodexAgent(AgentConfig(type="codex", usd_per_mtok=[2.0, 0.1, 10.0]))
    usage = agent.session_usage(tmp_path)
    assert usage == {"input_tokens": 50 + 50, "cache_read_input_tokens": 250, "cache_creation_input_tokens": 50,
                     "output_tokens": 40, "reasoning_output_tokens": 0, "api_messages": 3}
    assert agent.cost_from_tokens(usage) == pytest.approx((150 * 2.0 + 250 * 0.1 + 40 * 10.0) / 1e6)
    assert agent.session_usage(tmp_path / "none") is None
    assert CodexAgent(AgentConfig(type="codex")).cost_from_tokens(usage) is None  # no prices configured


def test_codex_config_loads():
    cfg = load_config("configs/codex_preview.yaml")
    agent = make_agent(cfg.agent)
    assert isinstance(agent, CodexAgent) and cfg.agent.model == "gpt-6.1-sol" and cfg.agent.effort == "high"
    assert cfg.sandbox.timeout_s == 3600 and cfg.n_attempts == 3 and cfg.grader.model == "claude-opus-5-5"


def test_subagent_commands_come_from_rollouts(tmp_path):
    def rollout(name, metas, items):
        lines = [{"type": "session_meta", "payload": m} for m in metas] + [{"type": "response_item", "payload": i}
                                                                           for i in items]
        (tmp_path / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    spawn = {"type": "function_call", "name": "spawn_agent", "arguments": '{"task_name": "check"}', "call_id": "c1"}
    main = {"id": "t-main", "source": "exec", "thread_source": "user"}
    sub = {"id": "t-sub", "thread_source": "subagent", "forked_from_id": "t-main",
           "source": {"subagent": {"thread_spawn": {"parent_thread_id": "t-main", "agent_nickname": "Dirac"}}}}
    rollout("rollout-2026-11-05T09-00-00-t-main.jsonl", [main], [spawn])
    rollout("rollout-2026-11-05T09-00-05-t-sub.jsonl", [sub, main], [
        spawn,  # inherited from the parent: not the subagent's own call
        {"type": "custom_tool_call", "name": "exec", "input": 'tools.exec_command({cmd: "esearch -db sra"})',
         "call_id": "c2"},
        {"type": "custom_tool_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "done"}]},
    ])
    (seg,) = CodexAgent(AgentConfig(type="codex")).subagent_segments(tmp_path)
    assert seg.kind == "subagent:Dirac" and [s.kind for s in seg.steps] == ["tool_use", "tool_result"]
    assert seg.steps[0].parent == "Dirac" and "esearch -db sra" in seg.steps[0].tool_input["code"]
    assert seg.steps[1].text == "done"
    assert final_answer([seg]) == (None, "")  # never a source of the answer
