"""bmb_sheldon, the runtime that runs inside the sandbox next to SHELDON.

Needs SHELDON installed: uv run --group dev --group sheldon pytest
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sheldon")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bmbench" / "agents" / "sheldon_runtime"))

from sheldon.core.cache import DiskCache  # noqa: E402
from sheldon.methods.sheldon.schemas import FinalOut, LocalOut  # noqa: E402

from bmb_sheldon import driver, watchdog  # noqa: E402
from bmb_sheldon.benchmark import ANSWER_FORMAT, BioMysteryBench  # noqa: E402
from bmb_sheldon.transport import ITEMS_BLOCK, ResponsesBackend, ResponsesLLM, ResponsesStreamError, strict_schema  # noqa: E402

PRICES = [0.10, 0.01, 0.50]


def response(output, text="", status="completed", inp=100, out=20, cached=10, reason=None):
    return SimpleNamespace(
        output=output, output_text=text, status=status, model="gpt-6-luna",
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out,
                              input_tokens_details=SimpleNamespace(cached_tokens=cached)),
    )


class FakeStream:
    def __init__(self, events):
        self.events = list(events)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.events:
            raise StopAsyncIteration
        return self.events.pop(0)

    async def close(self):
        self.closed = True


class FakeClient:
    """`client.responses.create(..., stream=True)`, replaying scripted responses."""

    def __init__(self, *scripted):
        self.scripted = list(scripted)
        self.calls: list[dict] = []
        self.responses = self

    async def create(self, **params):
        self.calls.append(params)
        nxt = self.scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, list):  # raw events
            return FakeStream(nxt)
        return FakeStream([SimpleNamespace(type="response.created"),
                           SimpleNamespace(type="response.completed", response=nxt)])


def backend(client=None):
    return ResponsesBackend(base_url="http://x/v1", api_key="k", effort="high", usd_per_mtok=PRICES,
                            client=client or FakeClient())


REASONING = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "run_shell",
        "arguments": json.dumps({"command": "ls data"})}
MESSAGE = {"type": "message", "role": "assistant",
           "content": [{"type": "output_text", "text": "done", "annotations": []}]}


def test_request_translation_and_effort():
    b = backend()
    req = {
        "model": "gpt-6-luna", "max_tokens": 16000, "system": "be careful",
        "messages": [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": [{"type": "text", "text": "hm"},
                                              {"type": "tool_use", "id": "c9", "name": "run_shell",
                                               "input": {"command": "pwd"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c9", "content": "/workspace"},
                                         {"type": "text", "text": "next stage"}]},
        ],
        "tools": [{"name": "run_shell", "description": "shell", "strict": True,
                   "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}},
                  {"type": "web_search_20260209", "name": "web_search"}],
    }
    p = b.params(req, None)
    assert p["instructions"] == "be careful"
    assert p["reasoning"] == {"effort": "high"} and p["store"] is False
    assert p["include"] == ["reasoning.encrypted_content"] and p["max_output_tokens"] == 16000
    assert p["input"] == [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "hm"},
        {"type": "function_call", "call_id": "c9", "name": "run_shell", "arguments": '{"command": "pwd"}'},
        {"type": "function_call_output", "call_id": "c9", "output": "/workspace"},
        {"role": "user", "content": "next stage"},
    ]
    assert [t["name"] for t in p["tools"]] == ["run_shell"]  # the server-side web tool is dropped
    assert p["tools"][0]["strict"] is False and "text" not in p


def test_strict_schema_requires_everything_and_keeps_property_names():
    s = strict_schema(FinalOut)
    assert set(s["required"]) == {"answer", "file_path", "confidence"}
    assert s["additionalProperties"] is False and "default" not in json.dumps(s)
    nested = strict_schema(LocalOut)
    obs = nested["$defs"]["ObservationDraft"]
    assert set(obs["required"]) == set(obs["properties"]) and "content" in obs["properties"]


def test_result_maps_items_usage_and_cost():
    b = backend()
    res = b.result(response([REASONING, CALL], text=""), {"model": "gpt-6-luna"}, None)
    assert res.stop_reason == "tool_use"
    assert res.content[0]["type"] == ITEMS_BLOCK
    assert res.content[-1] == {"type": "tool_use", "id": "call_1", "name": "run_shell", "input": {"command": "ls data"}}
    assert (res.usage.input_tokens, res.usage.cache_read_tokens, res.usage.output_tokens) == (90, 10, 20)
    assert res.usage.cost_usd == pytest.approx((90 * 0.10 + 10 * 0.01 + 20 * 0.50) / 1e6)


def test_incomplete_and_structured_results():
    b = backend()
    cut = b.result(response([REASONING], status="incomplete", reason="max_output_tokens"), {"model": "m"}, None)
    assert cut.stop_reason == "max_tokens" and cut.text == ""  # returned, not raised
    good = b.result(response([MESSAGE], text='{"answer": "<answer>CTCF</answer>", "file_path": "", "confidence": 0.8}'),
                    {"model": "m"}, FinalOut)
    assert good.parsed.answer == "<answer>CTCF</answer>"
    bad = b.result(response([MESSAGE], text="not json"), {"model": "m"}, FinalOut)
    assert bad.parsed is None  # SHELDON handles a missing parse itself


def test_reasoning_items_replayed_on_the_next_turn():
    """A tool loop through SHELDON's own LLM.converse: the second request carries the
    first reply's raw items (encrypted reasoning included) and the tool output."""
    client = FakeClient(response([REASONING, CALL]), response([MESSAGE], text="done"))
    llm = ResponsesLLM(base_url="http://x/v1", api_key="k", usd_per_mtok=PRICES, client=client,
                       model="gpt-6-luna", effort="high", cache=DiskCache(enabled=False))
    tool = {"name": "run_shell", "description": "shell",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}

    async def execute(name, args):
        return f"ran {args['command']}"

    convo = asyncio.run(llm.converse("look at the data", tools=[tool], execute=execute, max_iterations=3))
    assert convo.text == "done" and convo.tool_calls[0].input == {"command": "ls data"}
    second = client.calls[1]["input"]
    assert REASONING in second and CALL in second
    assert {"type": "function_call_output", "call_id": "call_1", "output": "ran ls data"} in second
    assert all(c["reasoning"] == {"effort": "high"} for c in client.calls)
    assert all(c.get("stream") is True for c in client.calls)
    # SHELDON's transcript holds a reference, not the encrypted blob
    assert "ENC" not in json.dumps(convo.messages)


def test_stream_errors_carry_a_status_for_sheldons_retries():
    b = backend(FakeClient([SimpleNamespace(type="error", code="rate_limit_exceeded", message="slow down")]))
    with pytest.raises(ResponsesStreamError) as e:
        asyncio.run(b.call({"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}, None))
    assert e.value.status_code == 429
    b = backend(FakeClient([SimpleNamespace(type="response.created")]))
    with pytest.raises(ResponsesStreamError) as e:
        asyncio.run(b.call({"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}, None))
    assert e.value.status_code == 502


def test_strict_schema_rejection_retries_without_strict():
    class Rejected(Exception):
        status_code = 400

    client = FakeClient(Rejected("Invalid schema for response_format"), response([MESSAGE], text='{"answer": "x"}'))
    res = asyncio.run(backend(client).call(
        {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}, FinalOut))
    assert client.calls[0]["text"]["format"]["strict"] is True
    assert client.calls[1]["text"]["format"]["strict"] is False and res.parsed.answer == "x"


def test_benchmark_never_carries_an_answer(tmp_path):
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps({"id": "hb020", "question": "Which species?"}))
    bench = BioMysteryBench(task_file=str(task_file))
    assert bench.check() == []
    (task,) = bench.load()
    assert task.question == "Which species?" and task.answer is None
    assert bench.probes(task) == [] and bench.sandbox(task) is None
    assert bench.answer_format == ANSWER_FORMAT and "<answer>" in ANSWER_FORMAT
    assert BioMysteryBench(task_file=str(tmp_path / "missing.json")).check()


def test_config_fixes_bmbench_fields_and_opens_the_shell_in_the_workspace(tmp_path):
    conf = {"workdir": "/workspace", "sheldon": {
        "model": "gpt-6-luna", "max_rounds": 3, "max_calls": None, "cache": True, "concurrency": 8,
        "params": {"mode": "session", "probes": ["run_shell"]}}}
    cfg = driver.build_config(conf, tmp_path / "task.json", 3599)
    assert (cfg.benchmark, cfg.cache, cfg.concurrency, cfg.resume, cfg.max_seconds) == ("biomysterybench", False, 1, False, 3599)
    assert cfg.max_rounds == 3 and cfg.max_calls is None and cfg.params["mode"] == "session"
    (shell,) = cfg.params["probes"]
    assert shell["run_shell"]["cwd"] == "/workspace"
    assert shell["run_shell"]["env"]["SHELDON_WORKSPACE"] == "/workspace"
    assert cfg.params["benchmark_opts"]["task_file"] == str(tmp_path / "task.json")


def test_time_limit_kills_commands_and_spares_sheldon():
    child = subprocess.Popen(["bash", "-c", "sleep 60 & wait"])  # a command and its own child
    time.sleep(0.3)
    grandchildren = [p for p in watchdog.descendants(os.getpid()) if p != child.pid]
    assert child.pid in watchdog.descendants(os.getpid()) and grandchildren
    limit = watchdog.TimeLimit(time.monotonic() + 0.2, interval=0.2).start()
    child.wait(timeout=10)
    time.sleep(0.5)
    limit.stop()
    assert limit.hit and limit.killed >= 2
    assert child.returncode == -9
    assert not set(grandchildren) & set(watchdog.ppid_map())  # the grandchild is gone too
    os.kill(os.getpid(), 0)  # this process is alive


def test_selftest_runs_the_whole_loop_offline(monkeypatch):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):  # the driver sets these; restore them after
        monkeypatch.setenv(var, os.environ.get(var, ""))
    conf = {"sheldon": {"model": "gpt-6-luna", "effort": "high", "max_rounds": 3, "max_calls": None,
                        "max_usd": None, "params": {"mode": "session", "rounds": 3, "n_initial": 3, "select_k": 3,
                                                    "probes": ["run_shell"]}}}
    out = io.StringIO()
    assert driver.selftest(conf, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [x["type"] for x in lines] == ["start", "result", "selftest"]
    assert lines[1]["answer"] == "<answer>selftest-answer</answer>" and lines[1]["rounds"] >= 1


def test_missing_credentials_are_an_auth_error(monkeypatch):
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    cfg = driver.build_config({"sheldon": {"model": "gpt-6-luna"}}, Path("/dev/null"), 60)
    with pytest.raises(RuntimeError, match="invalid api key"):
        driver.make_llm(cfg, {})

