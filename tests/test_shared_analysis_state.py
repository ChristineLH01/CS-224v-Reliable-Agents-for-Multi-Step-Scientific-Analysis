"""Shared-state behavior against the pinned SHELDON tool and driver interfaces."""
import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sheldon")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bmbench/agents/sheldon_runtime"))

from sheldon.core.probes import ShellProbe
from bmb_sheldon.adapted.shared_analysis_state import AnalysisState, RecordedProbe, SharedLLM
from bmb_sheldon import driver
from bmb_sheldon.selftest import fake_llm


def test_parallel_tests_have_private_outputs_and_next_round_gets_summary(tmp_path):
    work = tmp_path / "workspace"
    (work / "data").mkdir(parents=True)
    (work / "data/input.txt").write_text("input")
    state = AnalysisState(str(work), str(tmp_path / "archive"))
    state.run = SimpleNamespace(round=1)
    shell = RecordedProbe(ShellProbe(cwd=str(work)), state)
    prompts = []

    class Model:
        async def converse(self, prompt, *, execute, tag, **kwargs):
            prompts.append((tag, prompt))
            await asyncio.sleep(0)  # both contexts coexist
            result = await execute("run_shell", {"command": f"cat data/input.txt > result.txt; echo {tag} >> result.txt"})
            assert result.ok
            return SimpleNamespace(tool_calls=[SimpleNamespace(name="submit_local", input={
                "observations": [{"content": f"Finding for {tag}", "sources": ["result.txt"]}], "done": True})],
                stopped_by="stop")

    async def execute(name, args):
        return await shell.run(**args)

    async def scenario():
        llm = SharedLLM(Model(), state)
        await asyncio.gather(*(llm.converse("test", execute=execute, tag=f"candidate_analysis.h{i}.1")
                               for i in (1, 2)))
        state.run.round = 2
        await llm.converse("test next round", execute=execute, tag="candidate_analysis.h1.1")

    asyncio.run(scenario())
    folders = sorted((work / "analysis/tests").glob("*/*"))
    assert len(folders) == 3
    for folder in folders:
        assert (folder / "result.txt").is_file()
        assert "input" in (folder / "result.txt").read_text()
    assert not (work / "result.txt").exists()
    later = prompts[-1][1]
    assert "Finding for candidate_analysis.h1.1" in later
    assert "Finding for candidate_analysis.h2.1" in later
    assert state.scope.get().tag == "setup"
    archived = json.loads((tmp_path / "archive/state.json").read_text())
    assert archived == json.loads((work / "analysis/state.json").read_text())
    results = [e for e in archived["entries"] if e["kind"] == "result"]
    assert [e["round"] for e in results] == [1, 1, 2]
    assert all(e["evidence_sources"] == ["result.txt"] for e in results)


def test_failed_downloads_corrections_and_bounded_handoff(tmp_path):
    state = AnalysisState(str(tmp_path / "work"), summary_chars=2000)
    first = state.add("download", "Reference failed", source="https://example.org/ref.fa", status="failed")
    (state.workspace / "reference.fa").write_text(">ref\nACGT\n")
    state.add("download", "Reference", source="https://example.org/ref.fa", status="completed",
              paths=[str(state.workspace / "reference.fa")], supersedes=[first["id"]])
    assert first["id"] not in {json.loads(line).get("id") for line in state.summary().splitlines()}
    for i in range(100):
        state.add("result", f"Finding {i}: " + "x" * 2000)
    assert len(state.summary()) <= 2000
    assert "Finding 99" in state.summary()
    other = AnalysisState(str(tmp_path / "other"))
    assert other.entries == [] and "Finding" not in other.summary()


def test_shell_nonzero_and_cancellation_are_not_success(tmp_path):
    state = AnalysisState(str(tmp_path))
    shell = RecordedProbe(ShellProbe(cwd=str(tmp_path)), state)
    asyncio.run(shell.run(command="exit 7"))
    assert state.entries[-1]["status"] == "failed"

    class CancelledModel:
        async def converse(self, *args, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(SharedLLM(CancelledModel(), state).converse("test", tag="candidate_analysis.h1.1"))
    assert state.entries[-1]["status"] == "failed"
    assert state.scope.get().tag == "setup"


@pytest.mark.parametrize("mode", ["session", "staged"])
@pytest.mark.parametrize("adaptations", [["shared_analysis_state"], ["early_stop", "shared_analysis_state"]])
def test_driver_runs_real_stages_with_shared_state(tmp_path, monkeypatch, mode, adaptations):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, "")
    work = tmp_path / "workspace"
    (work / "data").mkdir(parents=True)
    (work / "data/sample.fa").write_text(">seq\nACGT\n")
    conf = {"sheldon": {"model": "fake", "max_rounds": 3, "max_calls": None, "max_usd": None,
                        "params": {"mode": mode, "rounds": 3, "n_initial": 3,
                                   "select_k": 3, "probes": ["run_shell"]}},
            "adaptations": adaptations, "workdir": str(work), "home": str(tmp_path / "home")}
    model = fake_llm()
    original = model.converse
    offered = []

    async def inspect_tools(*args, **kwargs):
        offered.extend(tool.get("name") for tool in kwargs.get("tools", []))
        return await original(*args, **kwargs)

    model.converse = inspect_tools
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))
    assert "record_analysis" not in offered
    assert "run_shell" in offered and "download_analysis" in offered
    assert not result["error"]
    assert result["answer"] == "<answer>selftest-answer</answer>"
    assert result["rounds"] == 2
    ledger = json.loads((tmp_path / "home/analysis/state.json").read_text())
    assert ledger["entries"]
    assert result["shared_analysis_state"]["entries"] == len(ledger["entries"])
    tests = [e for e in ledger["entries"] if e["kind"] == "test" and e["status"] == "completed"]
    assert len(tests) >= 2 and len({e["output_dir"] for e in tests}) == len(tests)
    assert any(e["kind"] == "result" and e["source"] == "submit_local" for e in ledger["entries"])


def test_shared_download_deduplicates_concurrent_urls_and_retries_failure(tmp_path):
    import shlex
    from sheldon.core.probes import ProbeResult
    from bmb_sheldon.adapted.shared_analysis_state import DownloadAnalysis

    state = AnalysisState(str(tmp_path))

    class Downloader:
        calls = 0
        fail = True

        async def run(self, command):
            self.calls += 1
            await asyncio.sleep(0)
            target = Path(shlex.split(command)[6])
            target.write_text("partial" if self.fail else "reference")
            return ProbeResult(summary="curl", evidence=[{"exit": 22 if self.fail else 0}])

    shell = Downloader()
    tool = DownloadAnalysis(state, shell)

    async def scenario():
        assert not (await tool.run("https://example.org/ref.fa")).ok
        assert not list((state.root / "downloads").glob("*.part"))
        shell.fail = False
        results = await asyncio.gather(*(tool.run("https://example.org/ref.fa") for _ in range(3)))
        assert all(r.ok for r in results)
        assert shell.calls == 2  # one failed attempt, one shared successful retry
        assert "Reusing" in results[-1].summary

    asyncio.run(scenario())
    assert [e["status"] for e in state.entries] == ["failed", "completed"]
    assert Path(state.entries[-1]["paths"][0]).read_text() == "reference"
    assert not asyncio.run(tool.run("file:///etc/passwd")).ok


def test_long_notes_do_not_hide_later_observations_or_cut_structured_records(tmp_path):
    state = AnalysisState(str(tmp_path))
    llm = SharedLLM(None, state)
    notes = "Long context. " * 2000
    observations = [{"content": f"Independent measurement {i}", "sources": [f"r{i}"]} for i in range(12)]
    observations.append({"content": "The independent table ranks let-7/98 first for 116 input genes.",
                         "sources": ["r156"],
                         "verdicts": [{"hypothesis": "h7", "relation": "refutes", "why": "Different leader"}]})
    llm.record_submission({"notes": notes, "observations": observations, "done": True}, "submit_local")
    persisted = json.loads((state.root / "state.json").read_text())
    submission = next(e for e in persisted["entries"] if e["kind"] == "submission")
    assert submission["submission"]["notes"] == notes
    assert len(submission["submission"]["observations"]) == 13
    results = [e for e in persisted["entries"] if e["kind"] == "result"]
    assert len(results) == 13
    assert results[-1]["summary"] == observations[-1]["content"]
    rows = [json.loads(line) for line in state.summary().splitlines()]
    assert any("let-7/98" in row.get("summary", "") for row in rows)
    assert all(row.get("claim_status") != "verified" for row in rows)
    assert len(state.summary()) <= 10000
    assert any(row.get("excerpt") for row in rows)


def test_opposing_findings_are_preserved_as_unresolved_not_overwritten(tmp_path):
    state = AnalysisState(str(tmp_path), summary_chars=2000)
    llm = SharedLLM(None, state)
    llm.record_submission({"observations": [
        {"content": "Measurement A supports h1.", "verdicts": [{"hypothesis": "h1", "relation": "supports"}]},
        {"content": "Measurement B contradicts h1.", "verdicts": [{"hypothesis": "h1", "relation": "refutes"}]},
    ]}, "submit_local")
    rows = [json.loads(line) for line in state.summary().splitlines()]
    assert rows[0]["conflicting_hypotheses"] == ["h1"]
    assert {v["relation"] for row in rows for v in row.get("verdicts", [])} == {"supports", "refutes"}
    assert all(row["claim_status"] == "reported" for row in rows[1:])


def test_local_iterations_reuse_test_directory_and_preserve_previous_files(tmp_path):
    work = tmp_path / "workspace"
    (work / "data").mkdir(parents=True)
    state = AnalysisState(str(work))
    state.run = SimpleNamespace(round=1)
    shell = RecordedProbe(ShellProbe(cwd=str(work)), state)
    first = state.start("candidate_analysis.h1.1")
    token = state.scope.set(first)
    asyncio.run(shell.run(command="echo previous > checkpoint.txt"))
    state.scope.reset(token)
    second = state.start("candidate_analysis.h1.2")
    assert second.directory == first.directory
    token = state.scope.set(second)
    result = asyncio.run(shell.run(command="cat checkpoint.txt"))
    state.scope.reset(token)
    assert result.ok and "previous" in result.summary
    peer = state.start("candidate_analysis.h2.1")
    assert peer.directory != second.directory
    assert not (Path(peer.directory) / "checkpoint.txt").exists()
    state.run.round = 2
    assert state.start("candidate_analysis.h1.1").directory != first.directory


def test_lookups_are_recorded_automatically_without_a_bookkeeping_call(tmp_path):
    from sheldon.core.probes import ProbeResult

    class Lookup:
        name = "run_shell"
        description = "shell"
        decisive = server_side = transient = False
        max_strength = 0.9
        input_schema = {}
        cwd = str(tmp_path)

        async def run(self, **kwargs):
            return ProbeResult(summary="Measured lookup result", evidence=[{"exit": 0}])

    state = AnalysisState(str(tmp_path))
    shell = RecordedProbe(Lookup(), state)
    asyncio.run(shell.run(command="curl https://example.org/lookup?q=gene"))
    assert len(state.entries) == 1
    assert state.entries[0]["kind"] == "lookup"
    assert state.entries[0]["claim_status"] == "unverified"
    assert "Measured lookup result" in state.summary()


def test_summary_reports_omissions_and_stays_within_budget_for_long_records(tmp_path):
    state = AnalysisState(str(tmp_path), summary_chars=2000)
    text = "First evidence. " + "Measurement details. " * 200 + "Contrary evidence at the end."
    for i in range(30):
        state.add("result", text, verdicts=[{"hypothesis": "h1", "relation": "neutral"}])
    rows = [json.loads(line) for line in state.summary().splitlines()]
    assert len(state.summary()) <= 2000
    assert rows[0]["omitted_findings"] > 0
    assert len(rows) > 1
    assert "Contrary evidence at the end" in rows[1]["summary"]
    assert rows[1]["excerpt"] is True
    assert rows[1]["ledger_ref"]
    assert state.entries[0]["summary"] == text


def test_revised_claim_continues_same_test_but_independent_child_does_not(tmp_path):
    state = AnalysisState(str(tmp_path))
    state.run = SimpleNamespace(round=1, state=SimpleNamespace(hypotheses={
        "h1": SimpleNamespace(parent=""), "h3": SimpleNamespace(parent="h1"),
        "h4": SimpleNamespace(parent="h1")}))
    parent = state.start("candidate_analysis.h1.1")
    revision = state.start("candidate_analysis.h3.2")
    independent = state.start("candidate_analysis.h4.1")
    assert parent.directory == revision.directory
    assert independent.directory != parent.directory


def test_local_verdict_with_no_hypothesis_is_attributed_to_its_test(tmp_path):
    from bmb_sheldon.adapted.shared_analysis_state import Scope

    state = AnalysisState(str(tmp_path))
    token = state.scope.set(Scope(tag="candidate_analysis.h7.1", round=1))
    SharedLLM(None, state).record_submission({"observations": [
        {"content": "Contrary measurement", "verdicts": [{"relation": "refutes"}]}]}, "submit_local")
    state.scope.reset(token)
    row = next(e for e in state.entries if e["kind"] == "result")
    assert row["verdicts"][0]["hypothesis"] == "h7"
