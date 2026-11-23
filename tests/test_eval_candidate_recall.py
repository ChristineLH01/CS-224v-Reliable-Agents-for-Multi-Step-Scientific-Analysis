"""scripts/eval_candidate_recall.py on made-up run folders, with a scripted judge."""
import gzip
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_candidate_recall", ROOT / "scripts/eval_candidate_recall.py")
ev = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ev  # dataclasses look their module up
spec.loader.exec_module(ev)

from bmbench.dataset import Problem  # noqa: E402

PROBLEM = Problem(id="p1", question="Which organism?", answer_rubric="Escherichia coli", allowed_domains=(),
                  human_solvable="yes")


def scan_run(root: Path) -> Path:
    a = root / "scan_run/problems/p1/attempt-1"
    a.mkdir(parents=True)
    rdir = a / "agent_session/sheldon/runs/x/retrievals/problem"
    rdir.mkdir(parents=True)
    for n in (1, 2, 3):
        (rdir / f"r{n}.json").write_text(json.dumps({"id": f"r{n}"}))
    # cited every way models cite: none of these is written `r3`
    record = {"candidates": [{"id": "c1", "answer": "Homo sapiens", "basis": "prior", "sources": ["retrieval 3"]},
                             {"id": "c2", "answer": "E. coli", "basis": "data", "sources": ["7", "notes"]}],
              "tests": [{"id": "t1"}], "usage": {"cost_usd": 0.02}, "parsed": True, "retrievals": 3}
    (a / "result.json").write_text(json.dumps({"status": "completed", "sheldon": {"candidate_scan": record}}))
    empty = root / "scan_run/problems/p1/attempt-3"
    empty.mkdir(parents=True)
    (empty / "result.json").write_text(json.dumps({"status": "completed", "sheldon": {"candidate_scan": {
        "candidates": [], "tests": [{"id": "t1"}]}}}))
    miss = root / "scan_run/problems/p1/attempt-2"
    miss.mkdir(parents=True)
    (miss / "result.json").write_text(json.dumps({"status": "timeout", "error": "killed"}))
    return root / "scan_run"


def baseline_run(root: Path) -> Path:
    a = root / "base_run/problems/p1/attempt-1"
    runs = a / "agent_session/sheldon/runs/x"
    (runs / "traces").mkdir(parents=True)
    hyps = [{"id": "h1", "statement": "It is a mammal.", "born": 1},
            {"id": "h2", "statement": "Find the organism by BLAST.", "born": 1},
            {"id": "h3", "statement": "A refinement in round 1.", "born": 1, "parent": "h1"}]
    (runs / "predictions.jsonl").write_text(json.dumps({"belief": {"hypotheses": hyps}}) + "\n")
    with gzip.open(runs / "traces/problem.jsonl.gz", "wt") as f:
        f.write(json.dumps({"kind": "explore.done", "round": 1, "added": ["h2", "h1"]}) + "\n")
    (a / "grade.json").write_text(json.dumps({"correct": False}))
    return root / "base_run"


def fake_judge(calls):
    async def judge(prompt, schema, cfg, image=None, retries=2):
        calls.append(prompt)
        if "concrete" in json.dumps(schema) or "names_answer" in schema["properties"]:
            names = "BLAST" not in prompt
            return {"verdict": {"names_answer": names, "answer": "mammal" if names else "",
                                "kind": "answer" if names else "method"}, "cost_usd": 0.01, "usage": {}}
        if "covered" in schema["properties"]:
            return {"verdict": {"covered": False, "parts": "none", "rationale": "-"}, "cost_usd": 0.01, "usage": {}}
        submitted = prompt.split("<submitted_answer>")[1].split("</submitted_answer>")[0]
        return {"verdict": {"correct": "coli" in submitted, "extracted_answer": submitted.strip(),
                            "normalized_answer": submitted.strip(), "rationale": "-"}, "cost_usd": 0.01, "usage": {}}
    return judge


def test_extract_grade_and_summarize(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "judge", fake_judge(calls))
    monkeypatch.setattr(ev, "load_all_problems", lambda: {"p1": PROBLEM})
    monkeypatch.setattr(ev, "load_dotenv", lambda: None)
    out = tmp_path / "out"
    args = ["--scan", str(scan_run(tmp_path)), "--baseline", str(baseline_run(tmp_path)), "--out", str(out)]
    assert ev.main(args) == 0
    rows = {(r["source"], r["attempt"]): r for r in json.loads((out / "summary.json").read_text())["attempts"]}
    scan = rows[("scan", "1")]
    assert scan["hit"] is True and scan["hit_rank"] == 2 and scan["hit_top1"] is False and scan["hit_top3"] is True
    assert scan["hit_basis"] == "data" and scan["scan_unverified_candidates"] == 1  # c2 cites r7, never made
    assert "no candidate_scan record (timeout: killed)" in rows[("scan", "2")]["note"]
    assert rows[("scan", "2")]["hit"] == ""  # interrupted: left out
    assert rows[("scan", "3")]["hit"] is False and rows[("scan", "3")]["n_items"] == 0  # empty list: a miss
    base = rows[("baseline", "1")]
    # round 1 is explore's own list, in id order; the refinement h3 is not in it
    assert base["n_items"] == 2 and base["hit"] is False and base["n_naming_answer"] == 1
    assert base["covered_by_union"] is False and base["final_correct"] is False
    md = (out / "summary.md").read_text()
    assert "| p1 | 1/2 | 0/2 | 1/2 | 1/2 |" in md and "1/2" in md
    # a rerun is served from the cache
    n = len(calls)
    assert ev.main(args) == 0 and len(calls) == n


def test_no_judge_only_extracts(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "judge", None)
    monkeypatch.setattr(ev, "load_all_problems", lambda: {"p1": PROBLEM})
    out = tmp_path / "out"
    assert ev.main(["--scan", str(scan_run(tmp_path)), "--out", str(out), "--no-judge"]) == 0
    assert not (out / "judgments.jsonl").exists()
    assert json.loads((out / "summary.json").read_text())["attempts"][0]["hit"] == ""
