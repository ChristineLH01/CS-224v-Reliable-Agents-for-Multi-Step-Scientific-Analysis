"""`candidate_scan` in its offline recall mode, against the pinned SHELDON."""
import asyncio
import io
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("sheldon")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bmbench/agents/sheldon_runtime"))

from sheldon.core.llm import FakeLLM  # noqa: E402

from bmb_sheldon import driver, selftest  # noqa: E402
from bmb_sheldon.adapted import ADAPTATIONS, resolve  # noqa: E402
from bmb_sheldon.adapted import candidate_scan as cs  # noqa: E402

MARK = "BMB_SCAN_MARK"


def test_clean_numbers_dedupes_caps_and_renumbers_tests():
    out = cs.ScanOut(
        inventory=" one table ",
        candidates=[cs.CandidateDraft(answer="TP53", basis="data", sources=["r1", "r9"]),
                    cs.CandidateDraft(answer=" tp53 "),  # a repeat of c1
                    cs.CandidateDraft(answer=""),  # empty
                    cs.CandidateDraft(answer="MYC", basis="prior"),
                    cs.CandidateDraft(answer="EGFR")],  # past the cap
        tests=[cs.OpenTestDraft(question="Is the table normalized?", kind="data_quality", affects=["c2", "c4", "c5"]),
               cs.OpenTestDraft(question="  ")])
    rec = cs.clean(out, max_candidates=2, retrieval_ids=["r1"])
    assert [(c["id"], c["answer"]) for c in rec["candidates"]] == [("c1", "TP53"), ("c2", "MYC")]
    assert rec["candidates"][0]["sources_found"] == ["r1"]
    # c2 was a repeat of c1 and c4 became c2; c5 was cut by the cap
    assert rec["tests"] == [{"id": "t1", "question": "Is the table normalized?", "kind": "data_quality",
                             "affects": ["c1", "c2"]}]
    assert rec["dropped"] == 3 and rec["inventory"] == "one table"
    assert cs.clean(None, 5)["candidates"] == []


def test_retrieval_ids_are_read_however_they_are_cited():
    for ref in ("r3", "R3", "[r3]", "retrieval 3", "Retrieval #3", "3", " r 3 "):
        assert cs.retrieval_id(ref) == "r3", ref
    assert cs.retrieval_id("data_scrubbed.cif") == "data_scrubbed.cif"
    out = cs.ScanOut(candidates=[cs.CandidateDraft(answer="X", sources=["retrieval 1", "2", "r9"])])
    assert cs.clean(out, 5, ["r1", "r2"])["candidates"][0]["sources_found"] == ["retrieval 1", "2"]


def test_short_list_is_extended_once_and_marked(tmp_path, clean_env):
    def tools(prompt, offered):
        if "submit_candidate_scan" in {t.get("name") for t in offered}:
            return {"name": "submit_candidate_scan", "input": cs.ScanOut(
                candidates=[cs.CandidateDraft(answer="Escherichia coli")]).model_dump()}
        return selftest.tool_responder(prompt, offered)

    def responder(prompt, schema):
        if schema is cs.ScanOut:  # the follow-up: repeats c1, adds two
            assert "at least 3 are needed" in prompt
            return cs.ScanOut(candidates=[cs.CandidateDraft(answer="Escherichia coli"),
                                          cs.CandidateDraft(answer="Shigella flexneri", basis="prior"),
                                          cs.CandidateDraft(answer="Salmonella enterica", basis="prior")])
        return selftest.responder(prompt, schema)

    conf = conf_for(tmp_path, {"candidate_scan_only": True, "candidate_scan_probe_calls": 4})
    model = FakeLLM(responder=responder, tool_responder=tools, model="fake")
    rec = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))["candidate_scan"]
    assert [(c["answer"], c["extended"]) for c in rec["candidates"]] == [
        ("Escherichia coli", False), ("Shigella flexneri", True), ("Salmonella enterica", True)]
    assert rec["first_scan_candidates"] == 1 and rec["extended_candidates"] == 2
    assert "up to 4 tool calls" in model.systems[1] and "at least 3" in model.systems[1]


def test_all_leaves_candidate_scan_out():
    assert "candidate_scan" in ADAPTATIONS
    assert resolve(["all"]) == ["early_stop", "shared_analysis_state"]
    assert resolve(["candidate_scan", "all"]) == ["early_stop", "shared_analysis_state", "candidate_scan"]


def scan_responder(prompt, schema):
    if schema is cs.ScanOut:  # recovery path: never reached when the tool is called
        return cs.ScanOut()
    return selftest.responder(prompt, schema)


def scan_tools(prompt, tools):
    names = {t.get("name") for t in tools}
    if "submit_candidate_scan" not in names:
        return selftest.tool_responder(prompt, tools)
    if MARK not in prompt:
        return {"name": "run_shell", "input": {"command": f"echo {MARK}; ls data"}}
    return {"name": "submit_candidate_scan", "input": cs.ScanOut(
        inventory="one FASTA file",
        candidates=[cs.CandidateDraft(answer="Escherichia coli", evidence="sample.fa", basis="data",
                                      sources=["r1"]),
                    cs.CandidateDraft(answer="Homo sapiens", basis="prior")],
        tests=[cs.OpenTestDraft(question="Is the file truncated?", kind="data_quality", affects=["c1"])],
    ).model_dump()}


def conf_for(tmp_path, params, adaptations=("candidate_scan",)):
    work = tmp_path / "workspace"
    (work / "data").mkdir(parents=True, exist_ok=True)
    (work / "data/sample.fa").write_text(">seq\nACGT\n")
    return {"sheldon": {"model": "fake", "max_rounds": 3, "max_calls": None, "max_usd": None,
                        "params": {"mode": "session", "rounds": 3, "n_initial": 3, "select_k": 3,
                                   "probes": ["run_shell"], **params}},
            "adaptations": list(adaptations), "workdir": str(work), "home": str(tmp_path / "home")}


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("SHELDON_RUNS", "SHELDON_DATA", "SHELDON_CACHE"):
        monkeypatch.setenv(var, "")


@pytest.mark.parametrize("adaptations", [["candidate_scan"], ["shared_analysis_state", "candidate_scan"]])
def test_scan_only_stops_after_the_scan(tmp_path, clean_env, adaptations):
    conf = conf_for(tmp_path, {"candidate_scan_only": True, "candidate_scan_probe_calls": 4}, adaptations)
    model = FakeLLM(responder=scan_responder, tool_responder=scan_tools, model="fake")
    out = io.StringIO()
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, out, llm=model))
    assert not result["error"]
    assert result["rounds"] == 0 and result["stop_reason"] == cs.STOP_REASON
    rec = result["candidate_scan"]
    assert [c["answer"] for c in rec["candidates"]] == ["Escherichia coli", "Homo sapiens"]
    assert rec["tests"][0]["affects"] == ["c1"]
    assert rec["parsed"] and rec["searched"] and rec["retrievals"] == 1
    assert rec["candidates"][0]["sources_found"] == ["r1"]
    assert "explains" not in rec["candidates"][0] and "mode" not in rec  # the single-mode record is unchanged
    assert "c1. Escherichia coli  [data]" in result["answer"]
    # plan and the scan only: no explore, no candidate analysis, no finalize
    run_dir = Path(result["run_dir"])
    events = [json.loads(line) for t in run_dir.glob("traces/*.jsonl") for line in t.read_text().splitlines()]
    tags = {str(e.get("tag")) for e in events if e.get("kind") == "llm"}
    # two candidates, below the minimum of 3: one follow-up call, which added nothing here
    assert tags == {"plan", "candidate_scan", "candidate_scan.extend"}
    assert rec["first_scan_candidates"] == 2 and rec["extended_candidates"] == 0
    assert any(e.get("kind") == cs.EVENT for e in events)
    assert any(MARK in s for s in model.systems + model.prompts)


def test_candidate_scan_refuses_to_run_without_scan_only(tmp_path, clean_env):
    conf = conf_for(tmp_path, {})
    model = FakeLLM(responder=scan_responder, tool_responder=scan_tools, model="fake")
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))
    assert "candidate_scan_only" in result["error"]
    assert model.prompts == []


# -- structured mode -------------------------------------------------------------------

STRUCT_MARK = "BMB_STRUCT_MARK"
IDENT_MARK = "BMB_IDENT_MARK"


def test_feature_ids_and_structure_are_cleaned():
    for ref in ("f2", "F2", "[f2]", "feature 2", "Feature #2", "2"):
        assert cs.feature_id(ref) == "f2", ref
    out = cs.StructureOut(inventory=" one mzML file ", tables=[" /workspace/scan/ions.tsv: every precursor ", " "],
                          features=[cs.FeatureDraft(kind="family", description="precursors share 167.0858",
                                                    values="256.1696, 272.1646", sources=[" r2 "]),
                                    cs.FeatureDraft(description="  "),  # empty: dropped
                                    cs.FeatureDraft(description="an intense 286.1439 ion"),
                                    cs.FeatureDraft(description="past the cap")])
    rec = cs.clean_structure(out, max_features=2)
    assert [(f["id"], f["kind"]) for f in rec["features"]] == [("f1", "family"), ("f2", "signal")]
    assert rec["features"][0]["sources"] == ["r2"] and rec["inventory"] == "one mzML file"
    assert rec["tables"] == ["/workspace/scan/ions.tsv: every precursor"]
    text = cs.render_features(rec["features"])
    assert "[f1] (family) precursors share 167.0858" in text and "lookup values: 256.1696, 272.1646" in text
    assert cs.render_features([]) == "(none found)"


def structured_tools(prompt, tools):
    names = {t.get("name") for t in tools}
    if "submit_structure" in names:
        if STRUCT_MARK not in prompt:
            return {"name": "run_shell", "input": {"command": f"echo {STRUCT_MARK}; ls data"}}
        return {"name": "submit_structure", "input": cs.StructureOut(
            inventory="one FASTA file", tables=["/workspace/scan/seqs.tsv: every sequence"],
            features=[cs.FeatureDraft(kind="family", description="sequences share one best hit", values="seq ids",
                                      sources=["r1"]),
                      cs.FeatureDraft(kind="grouping", description="files split into two groups")]).model_dump()}
    if "submit_candidate_scan" in names:
        if IDENT_MARK not in prompt:  # the opening turn: the structure step's output is in the prompt
            assert "**Features, most salient first**" in prompt
            assert "[f1] (family) sequences share one best hit" in prompt and "/workspace/scan/seqs.tsv" in prompt
            return {"name": "run_shell", "input": {"command": f"echo {IDENT_MARK}"}}
        return {"name": "submit_candidate_scan", "input": cs.IdentifyOut(
            identifications=[cs.IdentificationDraft(feature="F1", entity="Escherichia coli", how="BLAST URL API",
                                                    sources=["r2"]),
                             cs.IdentificationDraft(feature="2", entity="", how="nothing in nt")],
            candidates=[cs.ExplainedCandidate(answer="Escherichia coli", basis="lookup", sources=["r1", "r2"],
                                              explains=["f1", "feature 2", "F1"]),
                        cs.ExplainedCandidate(answer="Shigella flexneri", basis="prior"),
                        cs.ExplainedCandidate(answer="Salmonella enterica", basis="prior", explains=["f2"])],
        ).model_dump()}
    return selftest.tool_responder(prompt, tools)


def structured_responder(prompt, schema):
    if schema in (cs.StructureOut, cs.IdentifyOut, cs.ScanOut):  # recovery paths: never reached here
        return schema()
    return selftest.responder(prompt, schema)


def test_structured_scan_maps_then_identifies(tmp_path, clean_env):
    conf = conf_for(tmp_path, {"candidate_scan_only": True, "candidate_scan_mode": "structured",
                               "candidate_scan_structure_probe_calls": 4, "candidate_scan_identify_probe_calls": 3})
    model = FakeLLM(responder=structured_responder, tool_responder=structured_tools, model="fake")
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))
    assert not result["error"] and result["stop_reason"] == cs.STOP_REASON
    rec = result["candidate_scan"]
    assert rec["mode"] == "structured" and rec["parsed"] and rec["structure_parsed"]
    assert [f["id"] for f in rec["features"]] == ["f1", "f2"] and rec["inventory"] == "one FASTA file"
    assert [(c["answer"], c["explains"]) for c in rec["candidates"]] == [
        ("Escherichia coli", ["f1", "f2"]), ("Shigella flexneri", []), ("Salmonella enterica", ["f2"])]
    # one retrieval per call; the candidate cites both, and both count as the scan's own
    assert rec["structure_retrievals"] == 1 and rec["retrievals"] == 2
    assert rec["candidates"][0]["sources_found"] == ["r1", "r2"]
    assert rec["identifications"][0] == {"feature": "f1", "entity": "Escherichia coli", "how": "BLAST URL API",
                                         "basis": "lookup", "sources": ["r2"]}
    assert rec["identifications"][1]["feature"] == "f2" and rec["identifications"][1]["entity"] == ""
    assert rec["first_scan_candidates"] == 3 and rec["extended_candidates"] == 0
    run_dir = Path(result["run_dir"])
    events = [json.loads(line) for t in run_dir.glob("traces/*.jsonl") for line in t.read_text().splitlines()]
    tags = {str(e.get("tag")) for e in events if e.get("kind") == "llm"}
    assert tags == {"plan", "candidate_scan.structure", "candidate_scan.identify"}
    assert any(e.get("kind") == "candidate_scan.structure_done" for e in events)
    systems = "\n".join(model.systems)
    assert "up to 4 tool calls" in systems and "up to 3 tool calls" in systems
    assert "Do not propose answers" in systems and "Blast.cgi" in systems


def test_unknown_scan_mode_fails_before_any_call(tmp_path, clean_env):
    conf = conf_for(tmp_path, {"candidate_scan_only": True, "candidate_scan_mode": "two-step"})
    model = FakeLLM(responder=structured_responder, tool_responder=structured_tools, model="fake")
    result = asyncio.run(driver.run_sheldon("Which organism?", conf, 300, io.StringIO(), llm=model))
    assert "candidate_scan_mode" in result["error"] and model.prompts == []
