"""Grading: answer correctness vs. the rubric, and a cheating audit of the trajectory.

BioMysteryBench (v11) scores all-or-nothing: "Score 1.0 if the model did not cheat
AND got the answer correct." The two halves are judged separately so each judge
sees only what it needs:

  1. correctness -- question, rubric, the extracted answer (+ final message as context)
  2. cheating    -- question, every tool call, regex pre-flags, the proxy log

Both judges are `claude -p` with no tools, run in a throwaway sandbox container:
the same auth as the agent, no API key, nothing from the host's ~/.claude.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from bmbench.agents.base import classify_error
from bmbench.config import SECRET_ENV, GraderConfig, resolve
from bmbench.dataset import Problem
from bmbench.sandbox import LABEL, run_cmd
from bmbench.trajectory import Segment, Step, action_log, tool_inputs_text

JUDGE_SYSTEM = (
    "You are a careful, impartial grader for a scientific benchmark. You have no tools. "
    "Follow the grading instructions exactly and answer only through the required JSON schema."
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "extracted_answer": {"type": "string"},
        "normalized_answer": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["correct", "extracted_answer", "normalized_answer", "rationale"],
}

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "cheated": {"type": "boolean"},
        "evidence": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["cheated", "evidence", "rationale"],
}

#: (pattern, label). Applied to tool *inputs* only -- what the agent did, not what
#: data or tools printed back.
FLAG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE if ci else 0), label)
    for p, label, ci in [
        (r"\bGS[EM]\d{3,}\b", "GEO series/sample accession", False),
        (r"\bGPL\d{2,}\b", "GEO platform accession", False),
        (r"\b[SED]R[RXPS]\d{5,}\b", "SRA/ENA/DDBJ accession", False),
        (r"\bPRJ(?:NA|EB|DB)\d+\b", "BioProject accession", False),
        (r"\bSAM(?:N|EA|D)\d{5,}\b", "BioSample accession", False),
        (r"\bE-[A-Z]{4}-\d+\b", "ArrayExpress accession", False),
        (r"\bdb\s*=\s*['\"]?(?:gds|geo|sra|bioproject|biosample)\b", "Entrez query of GEO/SRA/BioProject", True),
        (r"-db\s+['\"]?(?:gds|sra|bioproject|biosample)\b", "EDirect query of GEO/SRA/BioProject", True),
        (r"ncbi\.nlm\.nih\.gov/(?:geo|sra|bioproject|biosample|gds|Traces)", "GEO/SRA web resource", True),
        (r"ebi\.ac\.uk/(?:ena|biostudies|arrayexpress)", "ENA/ArrayExpress web resource", True),
        (r"\b(?:fasterq-dump|fastq-dump|prefetch|pysradb|ffq|GEOparse|geofetch)\b", "SRA/GEO download tool", False),
        (r"\b(?:pubmed|europepmc|scholar\.google|biorxiv|medrxiv)\b", "literature search", True),
    ]
]


def regex_flags(text: str, context: int = 80, limit: int = 40) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern, label in FLAG_PATTERNS:
        for m in pattern.finditer(text):
            key = (label, m.group(0))
            if key in seen:
                continue
            seen.add(key)
            lo, hi = max(0, m.start() - context), min(len(text), m.end() + context)
            flags.append({"label": label, "match": m.group(0), "context": text[lo:hi].replace("\n", " ")})
            if len(flags) >= limit:
                return flags
    return flags


_LOG_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)/(\d{3})\s+(\S+)\s+(\S+)")


def summarize_network(log_path: Path) -> dict[str, Any]:
    """Allowed/denied hosts from the squid access log (paths only for plain HTTP)."""
    allowed: Counter[str] = Counter()
    denied: Counter[str] = Counter()
    http_urls: list[str] = []
    if not log_path.is_file():
        return {"available": False}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LOG_RE.match(line)
        if not m:
            continue
        _ts, _client, squid_status, _code, method, url = m.groups()
        if method == "-" or squid_status.startswith("NONE"):
            continue  # aborted connection, no request line (e.g. error:transaction-end-before-headers)
        host = url.split(":")[0] if method == "CONNECT" else re.sub(r"^\w+://([^/:]+).*$", r"\1", url)
        if "DENIED" in squid_status:
            denied[host] += 1
        else:
            allowed[host] += 1
        if method != "CONNECT" and len(http_urls) < 50:
            http_urls.append(f"{method} {url}")
    return {
        "available": True,
        "allowed": dict(allowed.most_common()),
        "denied": dict(denied.most_common()),
        "n_requests": sum(allowed.values()) + sum(denied.values()),
        "n_denied": sum(denied.values()),
        "http_urls": http_urls,
    }


def _network_text(summary: dict[str, Any]) -> str:
    if not summary.get("available"):
        return "(no proxy log: the run used open networking)"
    fmt = lambda c: ", ".join(f"{h} ({n})" for h, n in c.items()) or "none"  # noqa: E731
    text = f"allowed hosts: {fmt(summary['allowed'])}\ndenied hosts: {fmt(summary['denied'])}"
    if summary.get("http_urls"):
        text += "\nplain-HTTP requests (full URLs):\n" + "\n".join(summary["http_urls"])
    return text


def render(template: str, **values: Any) -> str:
    """Replace `{name}` for the given names only, so other braces survive."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit // 2] + "\n…\n" + text[-limit // 2 :]


# -- the judge call ------------------------------------------------------------


async def judge(prompt: str, schema: dict, cfg: GraderConfig, image: str, retries: int = 2) -> dict[str, Any]:
    """One tool-less `claude -p` call in a throwaway container; returns its parsed result."""
    last_error = ""
    for attempt in range(retries + 1):
        name = f"bmb-judge-{uuid.uuid4().hex[:10]}"
        argv = ["docker", "run", "--rm", "-i", "--name", name, "--label", f"{LABEL}=1", "-u", "agent"]
        for k in SECRET_ENV:
            if os.environ.get(k):
                argv += ["-e", k]
        argv += [image, "timeout", "-k", "10", str(cfg.timeout_s), "claude", "-p", "--model", cfg.model]
        if cfg.effort:
            argv += ["--effort", cfg.effort]
        argv += ["--tools", "", "--no-session-persistence", "--output-format", "json",
                 "--json-schema", json.dumps(schema), "--system-prompt", JUDGE_SYSTEM]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=cfg.timeout_s + 60)
        except TimeoutError:
            await run_cmd("docker", "rm", "-f", name, check=False)
            proc.kill()
            await proc.wait()
            last_error = "judge timed out"
            continue
        text = out.decode(errors="replace").strip()
        try:
            data = json.loads(text.splitlines()[-1]) if text else {}
        except json.JSONDecodeError:
            data = {}
        verdict = data.get("structured_output")
        if verdict is None and isinstance(data.get("result"), str):
            try:
                verdict = json.loads(data["result"])
            except json.JSONDecodeError:
                verdict = None
        if isinstance(verdict, dict) and not data.get("is_error"):
            return {"verdict": verdict, "cost_usd": float(data.get("total_cost_usd") or 0.0)}
        last_error = (data.get("result") if isinstance(data.get("result"), str) else "") or err.decode(errors="replace")[-1500:]
        if classify_error(last_error) == "auth":
            break
        await asyncio.sleep(10 * (attempt + 1))
    raise RuntimeError(f"judge failed: {last_error.strip()[:1500]}")


# -- grading one trial ---------------------------------------------------------


def load_segments(trial_dir: Path) -> list[Segment]:
    path = trial_dir / "trajectory.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for seg in raw.get("segments", []):
        steps = [Step(**s) for s in seg.pop("steps", [])]
        known = {k: v for k, v in seg.items() if k in Segment.__dataclass_fields__}
        segments.append(Segment(**known, steps=steps))
    return segments


async def grade_trial(trial_dir: Path, problem: Problem, cfg: GraderConfig, image: str,
                      force: bool = False) -> dict[str, Any] | None:
    out_path = trial_dir / "grade.json"
    if out_path.is_file() and not force:
        return json.loads(out_path.read_text(encoding="utf-8"))
    result_path = trial_dir / "result.json"
    if not result_path.is_file():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
    segments = load_segments(trial_dir)
    answer = result.get("answer") or ""
    final_message = result.get("final_text") or ""

    grade: dict[str, Any] = {
        "task_id": problem.id,
        "trial": result.get("trial"),
        "judge": {"model": cfg.model, "effort": cfg.effort, "cost_usd": 0.0},
    }

    # 1) correctness
    if not answer.strip() and not final_message.strip():
        grade.update(correct=False, extracted_answer="", normalized_answer="",
                     answer_rationale="no answer produced")
    else:
        template = resolve("prompts/grade_answer.md").read_text(encoding="utf-8")
        prompt = render(
            template,
            question=problem.question,
            rubric=problem.answer_rubric,
            answer=answer or "(empty: the agent gave no <answer> tag)",
            final_message=_clip(final_message, cfg.max_final_message_chars),
        )
        res = await judge(prompt, ANSWER_SCHEMA, cfg, image)
        v = res["verdict"]
        grade["judge"]["cost_usd"] += res["cost_usd"]
        grade.update(correct=bool(v.get("correct")), extracted_answer=str(v.get("extracted_answer", "")),
                     normalized_answer=str(v.get("normalized_answer", "")),
                     answer_rationale=str(v.get("rationale", "")))

    # 2) cheating audit
    flags = regex_flags(tool_inputs_text(segments))
    network = summarize_network(trial_dir / "network.log")
    grade["flags"] = flags
    grade["network"] = {k: v for k, v in network.items() if k != "http_urls"}
    n_tools = sum(1 for seg in segments for s in seg.steps if s.kind == "tool_use")
    if n_tools == 0:
        grade.update(cheated=False, cheat_evidence="", cheat_rationale="no tool calls")
    else:
        template = resolve("prompts/audit_cheating.md").read_text(encoding="utf-8")
        flag_text = "\n".join(f"- {f['label']}: `{f['match']}` … {f['context']}" for f in flags) or "(none)"
        prompt = render(
            template,
            question=problem.question,
            flags=flag_text,
            network=_network_text(network),
            actions=action_log(segments, cfg.max_action_log_chars),
        )
        res = await judge(prompt, AUDIT_SCHEMA, cfg, image)
        v = res["verdict"]
        grade["judge"]["cost_usd"] += res["cost_usd"]
        grade.update(cheated=bool(v.get("cheated")), cheat_evidence=str(v.get("evidence", "")),
                     cheat_rationale=str(v.get("rationale", "")))

    grade["score"] = 1.0 if (grade["correct"] and not grade["cheated"]) else 0.0
    grade["graded_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out_path.write_text(json.dumps(grade, indent=2, ensure_ascii=False), encoding="utf-8")
    return grade
