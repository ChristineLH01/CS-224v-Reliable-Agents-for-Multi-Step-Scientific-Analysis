from pathlib import Path

from bmbench.agents.claude_code import ClaudeCodeAgent
from bmbench.config import AgentConfig
from bmbench.trajectory import (
    Segment,
    Step,
    action_log,
    extract_answer,
    final_answer,
    render_transcript,
    tool_counts,
    tool_inputs_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude_stream.jsonl"


def parse_fixture(tmp_path: Path, exit_code: int = 0) -> Segment:
    agent = ClaudeCodeAgent(AgentConfig())
    seg = Segment(kind="main", exit_code=exit_code)
    return agent.parse(FIXTURE, tmp_path / "missing-stderr.log", seg)


def test_extract_answer_takes_last_tag():
    assert extract_answer("x <answer> A </answer> y <answer>B</answer>") == "B"
    assert extract_answer("no tags") is None
    assert extract_answer("<answer>  </answer>") is None
    assert extract_answer("<ANSWER>multi\nline</ANSWER>") == "multi\nline"


def test_parse_claude_stream(tmp_path):
    seg = parse_fixture(tmp_path)
    assert seg.session_id == "sess-1"
    assert seg.result_subtype == "success" and seg.is_error is False
    assert seg.cost_usd == 1.25 and seg.num_turns == 4
    assert seg.usage["output_tokens"] == 500
    kinds = [s.kind for s in seg.steps]
    assert kinds.count("tool_use") == 3 and kinds.count("tool_result") == 3
    assert "thinking" in kinds and "system" in kinds  # compact boundary recorded
    sub = [s for s in seg.steps if s.parent == "tu2"]
    assert sub and all(s.parent == "tu2" for s in sub)
    assert tool_counts([seg]) == {"Bash": 2, "Task": 1}
    answer, final = final_answer([seg])
    assert answer == "hsa-miR-0000-5p" and final.startswith("The most common")


def test_timeout_exit_code_marks_segment(tmp_path):
    assert parse_fixture(tmp_path, exit_code=124).timed_out
    assert not parse_fixture(tmp_path, exit_code=0).timed_out


def test_missing_result_keeps_stderr(tmp_path):
    out = tmp_path / "s.jsonl"
    out.write_text('{"type":"system","subtype":"init","session_id":"s"}\nnot json\n')
    err = tmp_path / "e.log"
    err.write_text("Invalid API key · Please run /login")
    seg = ClaudeCodeAgent(AgentConfig()).parse(out, err, Segment(kind="main", exit_code=1))
    assert not seg.got_result
    assert "Invalid API key" in seg.error_text


def test_final_answer_falls_back_to_earlier_tag():
    seg1 = Segment(kind="main", steps=[Step(kind="text", text="<answer>GENEA</answer> but let me double check")])
    seg2 = Segment(kind="nudge1", final_text="I ran out of ideas.")
    answer, final = final_answer([seg1, seg2])
    assert answer == "GENEA" and final == "I ran out of ideas."


def test_action_log_and_transcript(tmp_path):
    seg = parse_fixture(tmp_path)
    log = action_log([seg], max_chars=10_000)
    assert "[1] Bash: ls -la /workspace/data" in log
    assert "(subagent)" in log and "db=gds" in log
    assert "db=gds&term=GSE12345" in tool_inputs_text([seg])
    md = render_transcript("hb035", 1, [seg])
    assert "<answer>hsa-miR-0000-5p</answer>" in md and "**→ Bash**" in md


def test_action_log_truncates_middle():
    steps = [Step(kind="tool_use", tool="Bash", tool_input={"command": f"echo {i} " + "x" * 200}, tool_use_id=str(i))
             for i in range(200)]
    log = action_log([Segment(kind="main", steps=steps)], max_chars=3000)
    assert "omitted" in log and len(log) < 3500
    assert log.startswith("[1] Bash") and "[200] Bash" in log


def test_session_usage_dedupes_messages_and_includes_subagents(tmp_path):
    import json

    def line(mid, out):
        return json.dumps({"type": "assistant", "message": {"id": mid, "usage": {
            "input_tokens": 1, "output_tokens": out, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}})

    main = tmp_path / "claude/projects/-workspace/s.jsonl"
    sub = tmp_path / "claude/projects/-workspace/s/subagents/a.jsonl"
    sub.parent.mkdir(parents=True)
    main.write_text("\n".join([line("m1", 100), line("m1", 100), line("m2", 50), '{"type":"user"}']))
    sub.write_text(line("m3", 7))
    usage = ClaudeCodeAgent(AgentConfig()).session_usage(tmp_path)
    assert usage == {"input_tokens": 3, "output_tokens": 157, "cache_read_input_tokens": 30,
                     "cache_creation_input_tokens": 15, "api_messages": 3}
    assert ClaudeCodeAgent(AgentConfig()).session_usage(tmp_path / "nothing") is None


def test_multiple_result_events_sum_turns_keep_cumulative_cost(tmp_path):
    out = tmp_path / "s.jsonl"
    out.write_text(
        '{"type":"result","subtype":"success","result":"first","total_cost_usd":0.6,"num_turns":23}\n'
        '{"type":"assistant","message":{"id":"m9","content":[{"type":"text","text":"<answer>X</answer>"}]},"parent_tool_use_id":null}\n'
        '{"type":"result","subtype":"success","result":"<answer>X</answer>","total_cost_usd":0.63,"num_turns":1}\n'
    )
    seg = ClaudeCodeAgent(AgentConfig()).parse(out, tmp_path / "e.log", Segment(kind="main"))
    assert seg.num_turns == 24 and seg.cost_usd == 0.63 and seg.final_text == "<answer>X</answer>"
