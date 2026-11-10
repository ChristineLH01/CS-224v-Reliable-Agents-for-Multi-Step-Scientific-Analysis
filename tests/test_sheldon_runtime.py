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


def test_reply_cut_off_mid_tool_call_ends_the_loop_and_is_not_replayed():
    """The output cap hit while the model was writing a command: SHELDON sees
    max_tokens (its tool loop stops there, as on its Anthropic path), not a tool call
    with no command, and the conversation can continue without the half-written call."""
    half = {**CALL, "arguments": '{"command": "python - <<\'PY\'\\nimport numpy'}
    client = FakeClient(response([REASONING, half], status="incomplete", reason="max_output_tokens", out=16000),
                        response([MESSAGE], text="done"))
    llm = ResponsesLLM(base_url="http://x/v1", api_key="k", usd_per_mtok=PRICES, client=client,
                       model="gpt-6-luna", effort="high", cache=DiskCache(enabled=False))
    tool = {"name": "run_shell", "description": "shell",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}
    ran = []

    async def execute(name, args):
        ran.append(args)
        return "ran"

    convo = asyncio.run(llm.converse("test it", tools=[tool], execute=execute, max_iterations=3))
    assert convo.stopped_by == "max_tokens" and ran == [] and convo.tool_calls == []
    assert not any(b.get("type") == "tool_use" for m in convo.messages for b in m["content"] if isinstance(b, dict))
    # session mode: the next turn continues this transcript
    asyncio.run(llm.converse("next turn", tools=[tool], execute=execute, history=convo.messages))
    replayed = client.calls[1]["input"]
    assert not any(i.get("type") in ("function_call", "reasoning") for i in replayed)


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



# -- adapted SHELDON -----------------------------------------------------------------

import inspect  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
import textwrap  # noqa: E402

from sheldon.core.llm import FakeLLM  # noqa: E402
from sheldon.methods.sheldon.loop import Sheldon  # noqa: E402
from sheldon.methods.sheldon.schemas import SUBMIT_SCHEMAS, ObservationDraft, VerdictDraft  # noqa: E402
from sheldon.methods.sheldon.state import State  # noqa: E402

from bmb_sheldon import selftest as selftest_script  # noqa: E402
from bmb_sheldon.adapted import ADAPTATIONS, METHOD, early_stop, resolve  # noqa: E402
from bmb_sheldon.adapted.early_stop import StopCheckOut, gate  # noqa: E402
from bmb_sheldon.adapted.method import AdaptedSheldon, loop_without_adaptations  # noqa: E402


def test_adapted_loop_is_sheldons_own_loop_plus_marked_blocks():
    ours = loop_without_adaptations(textwrap.dedent(inspect.getsource(AdaptedSheldon._loop)))
    assert ours == textwrap.dedent(inspect.getsource(Sheldon.run))
    assert "# >>> adaptation: early_stop" in inspect.getsource(AdaptedSheldon._loop)


def test_adaptation_names_resolve_to_an_explicit_ordered_list():
    assert resolve(None) == resolve([]) == []
    assert resolve(["all"]) == resolve("all") == list(ADAPTATIONS)
    assert resolve(["early_stop", "early_stop", " "]) == ["early_stop"]
    with pytest.raises(ValueError, match="unknown SHELDON adaptation"):
        resolve(["early-stop"])


def test_config_picks_the_adapted_method_only_when_something_is_on(tmp_path):
    base = {"sheldon": {"model": "gpt-6-luna", "method": "sheldon", "params": {"mode": "session"}}}
    cfg = driver.build_config(base, tmp_path / "task.json", 60)
    assert cfg.method == "sheldon" and "adaptations" not in cfg.params
    cfg = driver.build_config({**base, "adaptations": ["all"]}, tmp_path / "task.json", 60)
    assert cfg.method == METHOD and cfg.params["adaptations"] == ["early_stop"]
    with pytest.raises(ValueError, match="method: sheldon"):
        driver.build_config({"sheldon": {"method": "direct"}, "adaptations": ["early_stop"]}, tmp_path / "t", 60)


def case(lead=(3, 0), rivals=((0, 2), (1, 3)), shelved=False) -> State:
    """h1 the lead, h2.. rivals, each with the given (supporting, refuting) observations;
    h9 a hypothesis about method that is no answer."""
    s = State()
    for i, (sup, ref) in enumerate([lead, *rivals], start=1):
        h = s.add(f"The answer is candidate {i}.", "", "", 1)
        s.tested(h, 1)
        for j in range(sup):
            s.observe(f"o for h{i} #{j}", "", [], 1, "local", [{"hypothesis": h.id, "relation": "supports", "why": ""}])
        for j in range(ref):
            s.observe(f"o against h{i} #{j}", "", [], 1, "local", [{"hypothesis": h.id, "relation": "refutes", "why": ""}])
    if shelved:
        s.shelve(s.hypotheses["h1"], "x", 1)
    s.add("Count the reads with a different tool.", "", "", 1)
    return s


def supports(s: State, hid: str = "h1") -> list[str]:
    return [o.id for o in s.observations.values() if o.verdicts.get(hid, {}).get("relation") == "supports"]


def closed(s: State, **over) -> StopCheckOut:
    fields = dict(lead="h1", candidate_answer="candidate 1", covers_question=True, support=supports(s)[:2],
                  rivals=[h for h in s.hypotheses if h not in ("h1", "h4")])
    return StopCheckOut(**{**fields, **over})


def test_gate_passes_a_closed_case():
    s = case()
    g = gate(s, closed(s))
    assert g.passed and not g.failed and g.evidence == {"h1": [3, 0], "h2": [0, 2], "h3": [1, 3]}
    assert gate(s, closed(s, lead="[H1]", support=[f" {o} " for o in supports(s)])).passed
    one = case(lead=(1, 0))
    assert gate(one, closed(one)).passed  # one verified supporting observation is enough


@pytest.mark.parametrize(("make", "over", "why"), [
    (lambda: case(), {"candidate_answer": ""}, "answer: no candidate"),
    (lambda: case(), {"covers_question": False}, "does not cover"),
    (lambda: case(), {"lead": "h7"}, "not a hypothesis"),
    (lambda: case(shelved=True), {}, "is shelved"),
    (lambda: case(lead=(3, 1)), {}, "1 refuting"),
    (lambda: case(lead=(0, 0)), {}, "0 verified supporting observation(s) of h1 cited, 1 needed"),
    (lambda: case(), {"support": ["o4", "o99"]}, "0 verified supporting observation(s)"),
    (lambda: case(), {"rivals": []}, "no rival hypothesis"),
    (lambda: case(rivals=((3, 3),)), {"rivals": ["h2"]}, "h2 not ruled out (+3 -3)"),
    (lambda: case(rivals=((0, 0),)), {"rivals": ["h2"]}, "h2 not ruled out (+0 -0)"),
    (lambda: case(), {"rivals": ["h1", "h2"]}, "h1 is the lead"),
    (lambda: case(), {"untested_rivals": ["candidate 7"]}, "untested: candidate 7"),
    (lambda: case(), {"open_issues": ["the BLAST job is still running"]}, "open: the BLAST job"),
])
def test_gate_holds_any_case_that_is_not_closed(make, over, why):
    s = make()
    g = gate(s, closed(s, **over))
    assert not g.passed and any(why in f for f in g.failed), g.failed


def test_gate_counts_only_verified_citations():
    s = case()  # o1-o3 support h1; o4, o5 refute h2
    g = gate(s, closed(s, support=["o1", "o4", "o2", "o99"]))
    assert g.passed and g.ignored == ["o4", "o99"]


def test_gate_refuses_no_reply():
    assert not gate(case(), None).passed


def test_check_prompt_shows_every_hypothesis_and_the_rounds_summary():
    s = case(shelved=True)
    trace = SimpleNamespace(events=[{"kind": "comparative_analysis.done", "round": 1, "summary": "ANI decides it."},
                                    {"kind": "comparative_analysis.done", "round": 2, "summary": "later"}])
    prompt = early_stop.render_prompt("Which organism?", s, early_stop.comparative_summary(trace, 1))
    assert "Which organism?" in prompt.user and "ANI decides it." in prompt.user and "later" not in prompt.user
    assert all(f"[{h}]" in prompt.user for h in s.hypotheses) and "(shelved" in prompt.user
    assert "untested_rivals" in prompt.system


def scripted_llm(settle: bool) -> FakeLLM:
    """The selftest's scripted model, with the bacterial hypothesis (h1) supported twice
    and the human one (h2) refuted, and a stop check that reports a closed case or none."""

    def responder(prompt, schema):
        if schema is LocalOut:
            bacterial = "bacterial" in prompt.split("**Hypothesis under test**", 1)[-1][:300]
            verdict = [VerdictDraft(relation="supports" if bacterial else "refutes", why="scripted")]
            texts = ["16S matches a bacterium.", "Genome size fits a bacterium."] if bacterial else ["No human reads."]
            return LocalOut(notes="checked", done=True,
                            observations=[ObservationDraft(content=t, verdicts=verdict) for t in texts])
        if schema is StopCheckOut:
            if not settle:
                return StopCheckOut()
            sup = re.findall(r"- \[(o\d+)\] [^\n]*\n  - supports h1:", prompt)
            return StopCheckOut(lead="h1", candidate_answer="bacteria", covers_question=True, support=sup,
                                rivals=["h2"])
        return selftest_script.responder(prompt, schema)

    def tool_responder(prompt, tools):
        names = {t.get("name") for t in tools}
        for name, schema in SUBMIT_SCHEMAS.items():
            if name in names:
                return {"name": name, "input": responder(prompt, schema).model_dump()}
        return None

    return FakeLLM(responder=responder, tool_responder=tool_responder, model="gpt-6-luna")


def run_scripted(monkeypatch, adaptations, settle):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "workspace"
        (work / "data").mkdir(parents=True)
        conf = {"sheldon": {"model": "gpt-6-luna", "effort": "high", "max_rounds": 3, "max_calls": None,
                            "max_usd": None, "params": {"mode": "session", "rounds": 3, "n_initial": 3,
                                                        "select_k": 3, "probes": ["run_shell"]}},
                "adaptations": adaptations, "workdir": str(work), "home": str(Path(tmp) / "home")}
        out = io.StringIO()
        result = asyncio.run(driver.run_sheldon("## Question\n\nWhich organism?\n", conf, 300, out,
                                                llm=scripted_llm(settle)))
        trace = next(Path(result["run_dir"]).glob("traces/*.jsonl"))
        events = [json.loads(x) for x in trace.read_text().splitlines()]
    start = json.loads(out.getvalue().splitlines()[0])
    return result, events, start


def test_early_stop_ends_a_settled_run_after_round_1(monkeypatch):
    result, events, start = run_scripted(monkeypatch, ["early_stop"], settle=True)
    assert not result["error"] and result["answer"] == "<answer>selftest-answer</answer>"
    assert (result["rounds"], result["stop_reason"]) == (1, "settled")
    assert result["early_stop"] == [{"round": 1, "passed": True, "failed": []}]
    assert start["adaptations"] == ["early_stop"]
    check = next(e for e in events if e["kind"] == early_stop.EVENT)
    assert check["evidence"] == {"h1": [2, 0], "h2": [0, 1]} and check["check"]["candidate_answer"] == "bacteria"
    kinds = [e["kind"] for e in events]
    assert kinds.index("round.done") < kinds.index(early_stop.EVENT) < kinds.index("finalize")
    assert any(e["kind"] == "llm" and e.get("tag") == early_stop.TAG for e in events)


def test_a_check_that_does_not_pass_changes_nothing(monkeypatch):
    result, events, _ = run_scripted(monkeypatch, ["early_stop"], settle=False)
    assert (result["rounds"], result["stop_reason"]) == (2, "nothing_to_test")
    assert [c["round"] for c in result["early_stop"]] == [1] and not result["early_stop"][0]["passed"]
    base, base_events, start = run_scripted(monkeypatch, [], settle=True)
    assert (base["rounds"], base["stop_reason"]) == (2, "nothing_to_test") and "early_stop" not in base
    assert start["adaptations"] == [] and not any(e["kind"] == early_stop.EVENT for e in base_events)


def test_a_failing_check_call_is_a_check_that_did_not_pass():
    async def broken(*_a, **_k):
        raise RuntimeError("proxy down")

    logged = []
    run = SimpleNamespace(round=1, task=SimpleNamespace(question="q"), state=case(),
                          ctx=SimpleNamespace(llm=SimpleNamespace(complete=broken), budget=None,
                                              trace=SimpleNamespace(events=[], log=lambda k, **f: logged.append(f))))
    rec = asyncio.run(early_stop.check(run))
    assert rec["passed"] is False and "proxy down" in rec["failed"][0] and logged[0]["passed"] is False


def test_selftest_checks_early_stop_when_it_is_on(monkeypatch):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
    conf = {"sheldon": {"model": "gpt-6-luna", "effort": "high", "max_rounds": 3, "max_calls": None,
                        "max_usd": None, "params": {"mode": "session", "rounds": 3, "n_initial": 3, "select_k": 3,
                                                    "probes": ["run_shell"]}}, "adaptations": ["all"]}
    out = io.StringIO()
    assert driver.selftest(conf, out)
    assert json.loads(out.getvalue().splitlines()[-1])["checks"]["early stop checked"] is True
