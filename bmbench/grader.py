"""Grading: answer correctness vs. the rubric, and a cheating audit of the trajectory.

BioMysteryBench (v11) scores all-or-nothing: "Score 1.0 if the model did not cheat
AND got the answer correct." The two halves are judged separately so each judge
sees only what it needs:

  1. correctness -- question, rubric, the extracted answer (+ final message as context)
  2. cheating    -- question, every tool call, regex pre-flags, the proxy log

Both judges are one chat-completions call to one fixed model for every agent
(default GPT-6 Sol), through the OpenAI-compatible LiteLLM proxy named in
LITELLM_BASE_URL. The call runs on the host, outside any sandbox.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import truststore

from bmbench.config import LITELLM_KEY_ENV, LITELLM_URL_ENV, GraderConfig, resolve
from bmbench.dataset import Problem
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
    "additionalProperties": False,
}

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "cheated": {"type": "boolean"},
        "evidence": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["cheated", "evidence", "rationale"],
    "additionalProperties": False,
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


def judge_endpoint() -> tuple[str, str]:
    """(chat-completions URL, API key) from the environment / .env."""
    base = os.environ.get(LITELLM_URL_ENV, "").strip().rstrip("/")
    key = os.environ.get(LITELLM_KEY_ENV, "").strip()
    if not base or not key:
        raise RuntimeError(f"judge not configured: set {LITELLM_URL_ENV} and {LITELLM_KEY_ENV} in .env")
    return (base if base.endswith("/chat/completions") else base + "/chat/completions"), key


@cache
def _tls() -> ssl.SSLContext:
    """The OS trust store (macOS keychain, Linux system CAs). python.org's macOS build
    ships without CA certificates, so the default context cannot verify anything."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _post(url: str, key: str, body: dict[str, Any], timeout_s: float) -> tuple[dict[str, Any], str | None]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s, context=_tls()) as resp:
        return json.loads(resp.read()), resp.headers.get("x-litellm-response-cost")


def _cost(usage: dict[str, Any], reported: str | None, cfg: GraderConfig) -> float:
    """The proxy's own figure when it sends one, else tokens × the configured prices."""
    try:
        if reported and float(reported) > 0:
            return float(reported)
    except ValueError:
        pass
    usd_in, usd_out = cfg.usd_per_mtok
    return (int(usage.get("prompt_tokens") or 0) * usd_in + int(usage.get("completion_tokens") or 0) * usd_out) / 1e6


async def judge(prompt: str, schema: dict, cfg: GraderConfig, retries: int = 3) -> dict[str, Any]:
    """One chat-completions call with a strict JSON-schema reply; returns the parsed verdict."""
    url, key = judge_endpoint()
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "verdict", "strict": True, "schema": schema}},
    }
    if cfg.effort:
        body["reasoning_effort"] = cfg.effort
    last_error = ""
    for attempt in range(retries + 1):
        if attempt:
            await asyncio.sleep(10 * attempt)
        try:
            data, reported_cost = await asyncio.to_thread(_post, url, key, body, cfg.timeout_s)
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.read().decode(errors='replace')[:1000]}"
            if e.code in (400, 401, 403, 404):
                break  # a bad request, key or model name does not improve with retries
            continue
        except (OSError, ValueError) as e:  # connection errors, timeouts, a non-JSON reply
            last_error = f"{type(e).__name__}: {e}"
            continue
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        try:
            verdict = json.loads(message.get("content") or "")
        except json.JSONDecodeError:
            verdict = None
        if isinstance(verdict, dict) and all(k in verdict for k in schema["required"]):
            usage = data.get("usage") or {}
            return {"verdict": verdict, "cost_usd": _cost(usage, reported_cost, cfg), "usage": usage}
        last_error = f"no usable verdict: {message.get('refusal') or (message.get('content') or '')[:500]!r}"
    raise RuntimeError(f"judge failed ({cfg.model}): {last_error.strip()[:1500]}")


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


async def grade_trial(trial_dir: Path, problem: Problem, cfg: GraderConfig,
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
        "judge": {"model": cfg.model, "effort": cfg.effort, "cost_usd": 0.0, "prompt_tokens": 0,
                  "completion_tokens": 0},
    }

    def spent(res: dict[str, Any]) -> None:
        grade["judge"]["cost_usd"] += res["cost_usd"]
        for k in ("prompt_tokens", "completion_tokens"):
            grade["judge"][k] += int(res["usage"].get(k) or 0)

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
        res = await judge(prompt, ANSWER_SCHEMA, cfg)
        v = res["verdict"]
        spent(res)
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
        res = await judge(prompt, AUDIT_SCHEMA, cfg)
        v = res["verdict"]
        spent(res)
        grade.update(cheated=bool(v.get("cheated")), cheat_evidence=str(v.get("evidence", "")),
                     cheat_rationale=str(v.get("rationale", "")))

    grade["score"] = 1.0 if (grade["correct"] and not grade["cheated"]) else 0.0
    grade["graded_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out_path.write_text(json.dumps(grade, indent=2, ensure_ascii=False), encoding="utf-8")
    return grade
