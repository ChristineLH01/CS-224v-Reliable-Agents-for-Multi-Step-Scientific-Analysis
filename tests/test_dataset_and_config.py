import zipfile

import pytest

from bmbench import dataset
from bmbench.config import load_config
from bmbench.dataset import Problem, parse_domains, safe_extract, select_problems
from bmbench.runner import effective_domains, render_task_prompt
from bmbench.sandbox import minimal_domains

RUBRIC = "The answer is SECRET-ANSWER. Score 1.0 if the model did not cheat AND got the answer correct."


def problem(**kw):
    base = dict(id="hb999", question="Which gene was knocked out?", answer_rubric=RUBRIC,
                allowed_domains=("ncbi.nlm.nih.gov", "pypi.org"), human_solvable="yes")
    base.update(kw)
    return Problem(**base)


def test_parse_domains():
    raw = "conda.anaconda.org, https://ncbi.nlm.nih.gov/, *.ensembl.org,ncbi.nlm.nih.gov"
    assert parse_domains(raw) == ("conda.anaconda.org", "ncbi.nlm.nih.gov", "ensembl.org")


def test_minimal_domains_drops_covered_subdomains():
    assert minimal_domains(["ncbi.nlm.nih.gov", "ftp.ncbi.nlm.nih.gov", "Pypi.org", "pypi.org"]) == [
        "ncbi.nlm.nih.gov", "pypi.org"]


def test_safe_extract_skips_metadata_and_rejects_slip(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("hb1/a.txt", "a")
        z.writestr("__MACOSX/hb1/._a.txt", "junk")
        z.writestr("hb2/b.txt", "b")
    out = tmp_path / "out"
    assert safe_extract(good, out, only_top={"hb1"}) == ["hb1"]
    assert (out / "hb1/a.txt").read_text() == "a"
    assert not (out / "__MACOSX").exists() and not (out / "hb2").exists()

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.txt", "x")
    with pytest.raises(ValueError):
        safe_extract(bad, tmp_path / "out2")


def test_load_problems_handles_multiline_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "resolve", lambda p: tmp_path / p)
    meta = tmp_path / "data/preview/meta"
    meta.mkdir(parents=True)
    (meta / "problems.csv").write_text(
        'id,question,answer_rubric,allowed_domains,human_solvable\n'
        'hb1,"Line one\nline two","rubric, with comma","a.org, b.org",yes\n'
        'hb2,Q2,R2,,no\n'
    )
    ps = dataset.load_problems("preview", "data")
    assert [p.id for p in ps] == ["hb1", "hb2"]
    assert ps[0].question == "Line one\nline two" and ps[0].allowed_domains == ("a.org", "b.org")
    assert select_problems(ps, human_solvable="no")[0].id == "hb2"
    with pytest.raises(KeyError):
        select_problems(ps, ids=["nope"])


def test_prompt_never_contains_the_rubric():
    cfg = load_config("configs/claude_code_preview.yaml")
    p = problem()
    text = render_task_prompt(p, cfg, n_files=3)
    assert "SECRET-ANSWER" not in text and "rubric" not in text.lower()
    assert p.question in text and "/workspace/data" in text and "3 files" in text
    assert "<answer>your answer</answer>" in text  # neutral format example, never a real answer
    assert "ncbi.nlm.nih.gov, pypi.org" in text and "60 minutes" in text and "3.5 GB" in text
    assert "{" not in text  # every placeholder filled
    assert "answer_rubric" not in p.public()


def test_effective_domains_adds_infra_only_to_proxy(monkeypatch):
    from bmbench.agents.base import make_agent

    cfg = load_config("configs/claude_code_preview.yaml")
    told, admitted = effective_domains(problem(), cfg, make_agent(cfg.agent).infra_domains)
    assert "api.anthropic.com" not in told and "api.anthropic.com" in admitted
    assert "files.pythonhosted.org" in admitted
    monkeypatch.setenv("LITELLM_BASE_URL", "https://models.example.edu/v1/chat/completions")
    codex = load_config("configs/codex_preview.yaml")
    _, admitted = effective_domains(problem(), codex, make_agent(codex.agent).infra_domains)
    assert "models.example.edu" in admitted and "api.anthropic.com" not in admitted
    cfg = load_config("configs/claude_code_preview.yaml", ["sandbox.override_allowed_domains=[]"])
    told, admitted = effective_domains(problem(), cfg)
    assert told == [] and "ncbi.nlm.nih.gov" not in admitted


def test_config_overrides_and_unknown_keys():
    cfg = load_config("configs/claude_code_preview.yaml",
                      ["agent.effort=high", "n_trials=2", "dataset.task_ids=[hb035,hb020]"])
    assert cfg.agent.effort == "high" and cfg.n_trials == 2
    assert cfg.dataset.task_ids == ["hb035", "hb020"]
    with pytest.raises(ValueError, match="timeout"):
        load_config(None, ["sandbox.timeout=5"])
