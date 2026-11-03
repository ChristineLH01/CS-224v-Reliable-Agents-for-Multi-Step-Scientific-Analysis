from bmbench.agents.base import classify_error
from bmbench.agents.claude_code import ClaudeCodeAgent
from bmbench.config import AgentConfig
from bmbench.grader import regex_flags, render, summarize_network
from bmbench.runner import classify
from bmbench.trajectory import Segment


def test_regex_flags_catch_accession_lookups_only():
    text = (
        "esearch -db sra -query 'liver'\n"
        "curl 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gds&term=GSE12345'\n"
        "blastn -remote -db nt -query seq.fa\n"
        "samtools view sample.bam | head\n"
    )
    labels = {f["label"] for f in regex_flags(text)}
    assert "EDirect query of GEO/SRA/BioProject" in labels
    assert "Entrez query of GEO/SRA/BioProject" in labels
    assert "GEO series/sample accession" in labels
    assert regex_flags("blastn -remote -db nt -query seq.fa; prefetch_related()") == []


def test_summarize_network(tmp_path):
    log = tmp_path / "network.log"
    log.write_text(
        "2026-10-26T09:05:51 172.19.0.3 TCP_TUNNEL/200 CONNECT ftp.ncbi.nlm.nih.gov:443 15207\n"
        "2026-10-26T09:05:52 172.19.0.3 TCP_DENIED/403 CONNECT www.google.com:443 3424\n"
        "2026-10-26T09:05:52 172.19.0.3 TCP_DENIED/403 GET http://example.com/x 3409\n"
        "2026-10-26T09:05:53 172.19.0.3 TCP_TUNNEL/200 CONNECT ftp.ncbi.nlm.nih.gov:443 15\n"
        "2026-10-26T09:05:54 172.19.0.3 NONE_NONE/000 - error:transaction-end-before-headers 0\n"
    )
    s = summarize_network(log)
    assert s["allowed"] == {"ftp.ncbi.nlm.nih.gov": 2}
    assert s["denied"] == {"www.google.com": 1, "example.com": 1}
    assert s["n_denied"] == 2 and s["http_urls"] == ["GET http://example.com/x"]
    assert summarize_network(tmp_path / "none.log") == {"available": False}


def test_render_keeps_unrelated_braces():
    assert render("a {x} {y} {z}", x=1, y="{2}") == "a 1 {2} {z}"


def test_claude_command():
    agent = ClaudeCodeAgent(AgentConfig(max_budget_usd=10))
    argv = agent.command(timeout_s=3600, session_id="abc")
    assert argv[:4] == ["timeout", "-k", "30", "3600"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--session-id") + 1] == "abc" and "--resume" not in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[-2:] == ["--disallowedTools", "WebSearch,AskUserQuestion"]
    resumed = agent.command(timeout_s=600, session_id="abc", resume=True, budget_usd=2.5)
    assert resumed[resumed.index("--resume") + 1] == "abc" and "--session-id" not in resumed
    assert resumed[resumed.index("--max-budget-usd") + 1] == "2.50"
    assert agent.env()["BASH_MAX_TIMEOUT_MS"] == "3600000"


def test_error_classification_and_status():
    assert classify_error("Invalid API key · Please run /login") == "auth"
    assert classify_error("API Error: 429 rate_limit_error") == "rate_limit"
    assert classify_error("Claude AI usage limit reached") == "rate_limit"
    assert classify_error("Traceback: KeyError") is None
    ok = Segment(kind="main", result_subtype="success", is_error=False)
    assert classify([ok], "GENEA")[0] == "completed"
    assert classify([ok], None)[0] == "no_answer"
    assert classify([Segment(kind="main", timed_out=True)], None)[0] == "timeout"
    # a final-answer turn that times out does not change why the work stopped
    killed = Segment(kind="oom_resume1", oom_killed=True)
    assert classify([killed, Segment(kind="finalize", timed_out=True)], None)[0] == "out_of_memory"
    assert classify([Segment(kind="main", error_text="Invalid API key")], None)[0] == "auth_error"
    assert classify([Segment(kind="main", exit_code=1)], None)[0] == "agent_error"


def test_proxy_is_reached_by_short_alias(tmp_path):
    from bmbench.config import SandboxConfig
    from bmbench.sandbox import PROXY_ALIAS, DockerSandbox

    box = DockerSandbox(name="bmb-" + "x" * 80 + "-recqgsfxqqodhjens-r1-abcdef", cfg=SandboxConfig(), workspace=tmp_path)
    url = box.proxy_env()["HTTPS_PROXY"]
    assert url == f"http://{PROXY_ALIAS}:3128" and len(PROXY_ALIAS) <= 63
