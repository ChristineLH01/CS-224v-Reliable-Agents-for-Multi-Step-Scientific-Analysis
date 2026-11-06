"""SHELDON (github.com/stanford-oval/sheldon), unmodified, as the agent under test.

SHELDON runs inside the sandbox like `claude -p` and `codex exec`: the image holds
it at a pinned commit, and `bmb_sheldon.driver` (bmbench/agents/sheldon_runtime)
runs one attempt through SHELDON's own `run_experiment`, with the model reached over
the Responses API (see bmb_sheldon/transport.py) through the LiteLLM proxy.

Differences from the CLI agents that the runner has to know about:

  resumable = False   SHELDON keeps no state a killed run could resume from, so it gets
                      no finalize or nudge resume turns. At the time limit it stops its
                      commands and runs its own finalize stage; the closing window
                      (`finalize_timeout_s`) is part of its own command.
  trace               what it did is in its trace and retrieval files, copied out of the
                      sandbox; this module turns them into steps (for the cheating audit),
                      token totals and a transcript organised by round and stage.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bmbench.agents.base import Agent
from bmbench.config import LITELLM_KEY_ENV, litellm_base_url
from bmbench.sandbox import WORKDIR
from bmbench.trajectory import Segment, Step, extract_answer

#: the SHELDON commit the image installs (docker/sandbox/Dockerfile, bmb_sheldon.SHELDON_COMMIT)
SHELDON_COMMIT = "7fbf027d1fb414443612d4ad2661b396c8352cd3"
PYTHON = "/opt/sheldon/.venv/bin/python"
HOME = "/home/agent/.sheldon"
#: exit status of coreutils `timeout` when its limit fired (137 if it had to SIGKILL)
TIMEOUT_EXIT_CODES = (124, 137)
#: SHELDON's stage events worth a line in the transcript and the trajectory
STAGE_EVENTS = ("plan", "merge.done", "explore.done", "select.done", "candidate_analysis.revision",
                "candidate_analysis.done", "comparative_analysis.done", "exploit.done", "round.done",
                "finalize", "budget.stop", "error")


class SheldonAgent(Agent):
    name = "sheldon"
    session_dirs = (HOME,)
    secret_env = (LITELLM_KEY_ENV,)
    resumable = False
    flatten_session = False

    @property
    def closing_s(self) -> int:
        return int(self.cfg.finalize_timeout_s)

    @property
    def infra_domains(self) -> tuple[str, ...]:
        try:
            host = urlparse(litellm_base_url()).hostname
        except RuntimeError:
            return ()
        return (host,) if host else ()

    def sheldon_config(self) -> dict[str, Any]:
        """SHELDON's own Config fields: the run's model and effort, then `agent.sheldon`."""
        return {"model": self.cfg.model, "effort": self.cfg.effort or "high", **dict(self.cfg.sheldon or {})}

    def command(self, *, timeout_s: int, session_id: str, resume: bool = False,
                budget_usd: float | None = None) -> list[str]:
        limit = max(1, int(timeout_s))
        return ["timeout", "-k", "30", str(limit + self.closing_s), PYTHON, "-m", "bmb_sheldon.driver",
                "--time-limit", str(limit)]

    def auth_check_command(self, timeout_s: int) -> list[str]:
        return ["timeout", "-k", "10", str(timeout_s), PYTHON, "-m", "bmb_sheldon.driver", "--ping"]

    def selftest_command(self) -> list[str]:
        return ["timeout", "-k", "10", "300", PYTHON, "-m", "bmb_sheldon.driver", "--selftest"]

    def version_command(self) -> list[str]:
        return [PYTHON, "-m", "bmb_sheldon.driver", "--version"]

    def env(self) -> dict[str, str]:
        conf = {"sheldon": self.sheldon_config(), "usd_per_mtok": self.cfg.usd_per_mtok, "workdir": WORKDIR,
                "home": HOME}
        env = {"BMB_SHELDON_CONFIG": json.dumps(conf)}
        try:
            env["LITELLM_BASE_URL"] = litellm_base_url()
        except RuntimeError:
            pass
        env.update(self.cfg.env)
        return env

    # -- what the driver printed ------------------------------------------------------

    def parse(self, stdout_path: Path, stderr_path: Path, segment: Segment) -> Segment:
        result: dict[str, Any] | None = None
        try:
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result = event
            elif event.get("type") == "start":
                segment.info["start"] = {k: v for k, v in event.items() if k != "type"}
        if result is not None:
            error = str(result.get("error") or "")
            raw = str(result.get("final_text") or result.get("answer") or "")
            segment.result_subtype = "error" if error else "success"
            segment.is_error = bool(error)
            segment.error_text = error
            segment.final_text = raw
            # SHELDON's finalize returns a structured answer field: the text in answer
            # tags if it has them, else the whole field.
            segment.answer = (extract_answer(raw) or raw.strip() or None) if raw.strip() else None
            usage = result.get("usage") or {}
            segment.usage = _tokens(usage)
            segment.num_turns = int(usage.get("calls") or 0)
            segment.cost_usd = self.cost_from_tokens(segment.usage) or 0.0
            hit = bool(result.get("time_limit_hit"))
            segment.closed_out = hit and segment.answer is not None
            segment.timed_out = hit
            segment.info["result"] = {k: v for k, v in result.items() if k not in ("type", "usage")}
        if segment.exit_code in TIMEOUT_EXIT_CODES:
            segment.timed_out = True
        if result is None or segment.is_error or segment.exit_code not in (0, *TIMEOUT_EXIT_CODES):
            try:
                tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except FileNotFoundError:
                tail = ""
            if tail.strip():
                segment.error_text = (segment.error_text + "\n" + tail).strip()
        return segment

    # -- what SHELDON recorded ----------------------------------------------------------

    def session_usage(self, session_root: Path) -> dict[str, int] | None:
        totals: Counter[str] = Counter()
        for trace in traces(session_root):
            for event in read_events(trace):
                if event.get("kind") == "llm":
                    for k, v in (event.get("usage") or {}).items():
                        if isinstance(v, (int, float)) and k != "cost_usd":
                            totals[k] += int(v)
        if not totals:
            return None
        out = _tokens(totals)
        out["api_messages"] = totals.get("calls", 0)
        return out

    def cost_from_tokens(self, tokens: dict[str, int] | None) -> float | None:
        if not tokens or not self.cfg.usd_per_mtok:
            return None
        usd_in, usd_cached, usd_out = self.cfg.usd_per_mtok
        return ((tokens.get("input_tokens", 0) + tokens.get("cache_creation_input_tokens", 0)) * usd_in
                + tokens.get("cache_read_input_tokens", 0) * usd_cached
                + tokens.get("output_tokens", 0) * usd_out) / 1e6

    def subagent_segments(self, session_root: Path) -> list[Segment]:
        """SHELDON's work, from its trace and retrieval files: what the audit and
        `tool_counts` see. The driver's own output carries only the result."""
        segments = []
        for trace in traces(session_root):
            run = Recorded(trace)
            segments.append(Segment(kind="sheldon", steps=run.steps(), session_id=trace.parent.parent.name))
        return segments

    def transcript(self, problem_id: str, attempt: int, segments: list[Segment], session_root: Path) -> str | None:
        found = traces(session_root)
        if not found:
            return None
        return "\n\n".join(Recorded(t).markdown(problem_id, attempt, segments) for t in found)

    def result_extra(self, session_root: Path, segments: list[Segment]) -> dict[str, Any]:
        info = next((s.info.get("result") for s in reversed(segments) if s.info.get("result")), None) or {}
        extra = {k: info.get(k) for k in ("rounds", "stop_reason", "hypotheses", "n_hypotheses", "n_observations",
                                          "time_limit_hit", "seconds_after_limit", "commands_killed")}
        start = next((s.info.get("start") for s in segments if s.info.get("start")), None) or {}
        extra.update(mode=start.get("mode"), sheldon_commit=start.get("sheldon_commit"))
        found = traces(session_root)
        if found:
            extra.update(Recorded(found[0]).command_stats())
        return extra

    def compact_session(self, session_root: Path) -> None:
        """Compress SHELDON's traces (every prompt in full: tens of MB) once the attempt
        is done and scrubbed; the readers here take `.jsonl.gz` as well."""
        for trace in session_root.rglob("traces/*.jsonl"):
            with open(trace, "rb") as src, gzip.open(str(trace) + ".gz", "wb") as dst:
                dst.writelines(src)
            trace.unlink()


# -- reading SHELDON's records ----------------------------------------------------------


def traces(session_root: Path) -> list[Path]:
    return sorted([*session_root.rglob("traces/*.jsonl"), *session_root.rglob("traces/*.jsonl.gz")])


def read_events(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    events = []
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return events


def _tokens(usage: dict[str, Any]) -> dict[str, int]:
    """SHELDON's usage (input = uncached) in the token keys the rest of the harness uses."""
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_write_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _key(tool: str, arguments: Any) -> str:
    return f"{tool}:{json.dumps(arguments or {}, sort_keys=True, default=str)}"


def _clip(text: str, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit} more chars]"


def stage_name(tag: str) -> str:
    tag = tag.removeprefix("session.")
    if tag.startswith("candidate_analysis."):
        parts = tag.split(".")
        hyp = parts[1] if len(parts) > 1 else "?"
        it = f" · iteration {parts[2]}" if len(parts) > 2 else ""
        return f"candidate analysis · {hyp}{it}"
    return {"merge": "merge regions", "comparative_analysis": "comparative analysis"}.get(tag, tag)


class Recorded:
    """One SHELDON run's trace and retrievals, matched up: each shell call a model made
    (in the trace) with the command's output (in its retrieval file)."""

    def __init__(self, trace: Path) -> None:
        self.trace = trace
        self.events = read_events(trace)
        self.retrievals = self._retrievals(trace.parent.parent / "retrievals")
        self._by_key: dict[str, list[dict[str, Any]]] = {}
        for r in self.retrievals:
            self._by_key.setdefault(_key(r.get("tool", ""), r.get("arguments")), []).append(r)
        self._used: set[str] = set()

    @staticmethod
    def _retrievals(root: Path) -> list[dict[str, Any]]:
        out = []
        for path in root.glob("*/r*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            stem = path.stem[1:]
            entry["_n"] = int(stem) if stem.isdigit() else 1 << 30
            out.append(entry)
        return sorted(out, key=lambda e: e["_n"])

    def match(self, tool: str, arguments: Any) -> dict[str, Any] | None:
        for r in self._by_key.get(_key(tool, arguments), []):
            if r["id"] not in self._used:
                self._used.add(r["id"])
                return r
        return None

    def calls(self) -> list[dict[str, Any]]:
        """Every model call and stage event, in order, each tool call with its retrieval,
        tagged with the round it belongs to (0 = before round 1, None = after the last)."""
        self._used = set()
        rounds_done, finalizing = 0, False
        out = []
        for e in self.events:
            kind = e.get("kind")
            tag = str(e.get("tag") or "")
            if kind == "finalize" or (kind == "llm" and tag == "finalize"):
                finalizing = True
            rnd: int | None = None if finalizing else rounds_done + 1
            if kind == "llm":
                if tag == "plan":
                    rnd = 0
                uses = []
                for tu in e.get("tool_use") or []:
                    name = tu.get("name") or ""
                    ret = None if name.startswith("submit_") else self.match(name, tu.get("input"))
                    uses.append({"name": name, "input": tu.get("input"), "retrieval": ret})
                out.append({"kind": "llm", "tag": tag, "round": rnd, "text": e.get("text") or "",
                            "parsed": e.get("parsed"), "uses": uses, "stop": e.get("stop_reason"),
                            "usage": e.get("usage") or {}})
            elif kind in STAGE_EVENTS or str(kind).endswith(".recovered"):
                if kind == "plan":
                    rnd = 0
                out.append({"kind": kind, "round": e.get("round", rnd) if kind == "round.done" else rnd, "event": e})
                if kind == "round.done":
                    rounds_done = int(e.get("round") or rounds_done + 1)
        return out

    # -- steps, for trajectory.json and the cheating audit ---------------------------------

    def steps(self) -> list[Step]:
        steps: list[Step] = []
        for c in self.calls():
            if c["kind"] != "llm":
                steps.append(Step(kind="system", text=_event_line(c["kind"], c["event"])))
                continue
            tag = c["tag"]
            sub = tag if tag.startswith("candidate_analysis") else None  # SHELDON's sub-agents
            if c["text"].strip() and c["parsed"] is None:
                steps.append(Step(kind="text", text=f"[{stage_name(tag)}] {c['text']}", parent=sub))
            if c["parsed"] is not None:
                steps.append(Step(kind="text", text=f"[{stage_name(tag)}] submitted: "
                                                    f"{json.dumps(c['parsed'], ensure_ascii=False)}", parent=sub))
            for u in c["uses"]:
                if u["name"].startswith("submit_"):
                    steps.append(Step(kind="text", text=f"[{stage_name(tag)}] {u['name']}: "
                                                        f"{json.dumps(u['input'], ensure_ascii=False)}", parent=sub))
                    continue
                steps += _tool_steps(u["name"], u["input"], u["retrieval"], sub)
        for r in self.retrievals:  # anything run that no traced call accounts for
            if r["id"] not in self._used:
                steps += _tool_steps(r.get("tool", ""), r.get("arguments"), r, None)
        return steps

    def command_stats(self) -> dict[str, int]:
        """How much shell work was redone: commands whose exact text an earlier stage
        call (a different stage, test or round) had already run."""
        seen: dict[str, str] = {}
        total = repeated = 0
        for c in self.calls():
            if c["kind"] != "llm":
                continue
            for u in c["uses"]:
                if u["name"] != "run_shell":
                    continue
                total += 1
                cmd = str((u["input"] or {}).get("command", ""))
                owner = f"{c['round']}:{c['tag']}"
                if cmd in seen and seen[cmd] != owner:
                    repeated += 1
                seen.setdefault(cmd, owner)
        return {"commands": total, "commands_repeated": repeated}

    # -- the transcript ------------------------------------------------------------------------

    def markdown(self, problem_id: str, attempt: int, segments: list[Segment]) -> str:
        calls = self.calls()
        info = next((s.info.get("result") for s in reversed(segments) if s.info.get("result")), {}) or {}
        start = next((s.info.get("start") for s in segments if s.info.get("start")), {}) or {}
        lines = [f"# Transcript: {problem_id} / attempt {attempt} (SHELDON)", ""]
        lines += [f"_SHELDON {str(start.get('sheldon_commit', ''))[:7]} · `{start.get('model', '?')}` effort "
                  f"`{start.get('effort', '?')}` · {start.get('mode', '?')} mode · rounds {info.get('rounds', '?')} "
                  f"(stop: {info.get('stop_reason') or '?'}) · {sum(1 for c in calls if c['kind'] == 'llm')} model calls · "
                  f"{len(self.retrievals)} commands" + (" · **time limit hit**" if info.get("time_limit_hit") else "")
                  + "_", ""]
        sections: dict[Any, list[dict[str, Any]]] = {}
        for c in calls:
            sections.setdefault(c["round"], []).append(c)
        order = sorted((k for k in sections if k is not None), key=int) + ([None] if None in sections else [])
        for rnd in order:
            title = "Plan" if rnd == 0 else "Finalize" if rnd is None else f"Round {rnd}"
            lines += [f"## {title}", ""]
            lines += self._section(sections[rnd])
        final = next((e for e in reversed(self.events) if e.get("kind") == "sheldon.final"), None)
        if final:
            lines += _final_state(final)
        for s in segments:
            if s.error_text:
                lines += ["**error:**", "", "```", _clip(s.error_text, 3000), "```", ""]
        return "\n".join(lines)

    def _section(self, calls: list[dict[str, Any]]) -> list[str]:
        groups: dict[str, list[dict[str, Any]]] = {}
        blocks: list[Any] = []
        for c in calls:  # one block per stage call, at its first appearance; events in place
            if c["kind"] == "llm":
                if c["tag"] not in groups:
                    groups[c["tag"]] = []
                    blocks.append(c["tag"])
                groups[c["tag"]].append(c)
            else:
                blocks.append(c)
        lines: list[str] = []
        for b in blocks:
            if isinstance(b, dict):
                lines += [f"_{_event_line(b['kind'], b['event'])}_", ""]
                continue
            lines += [f"### {stage_name(b)}", ""]
            for c in groups[b]:
                if c["text"].strip() and c["parsed"] is None:
                    lines += ["**model:**", "", _clip(c["text"].strip(), 4000), ""]
                if c["parsed"] is not None:
                    lines += ["**submitted:**", "", "```json", _clip(json.dumps(c["parsed"], indent=1,
                                                                               ensure_ascii=False), 5000), "```", ""]
                for u in c["uses"]:
                    if u["name"].startswith("submit_"):
                        lines += [f"**submitted** (`{u['name']}`):", "", "```json",
                                  _clip(json.dumps(u["input"], indent=1, ensure_ascii=False), 5000), "```", ""]
                        continue
                    cmd = (u["input"] or {}).get("command") if isinstance(u["input"], dict) else None
                    lines += [f"**→ {'shell' if u['name'] == 'run_shell' else u['name']}**", "", "```",
                              _clip(cmd if cmd is not None else json.dumps(u["input"], ensure_ascii=False), 4000),
                              "```", ""]
                    r = u["retrieval"]
                    if r is not None:
                        ev = (r.get("evidence") or [{}])[0] if r.get("evidence") else {}
                        lines += [f"<details><summary>← output [{r.get('id')}]"
                                  + (f" (exit {ev.get('exit')})" if isinstance(ev, dict) and "exit" in ev else "")
                                  + "</summary>", "", "```", _clip(str(r.get("summary", "")), 2500), "```", "",
                                  "</details>", ""]
                if c["stop"] in ("max_tokens", "refusal"):
                    lines += [f"_model call ended: {c['stop']}_", ""]
        return lines


def _tool_steps(name: str, arguments: Any, retrieval: dict[str, Any] | None, parent: str | None) -> list[Step]:
    tool = "Bash" if name == "run_shell" else name
    rid = (retrieval or {}).get("id") or f"call-{id(arguments)}"
    steps = [Step(kind="tool_use", tool=tool, tool_input=arguments if isinstance(arguments, dict) else
                  {"input": arguments}, tool_use_id=rid, parent=parent)]
    if retrieval is not None:
        ev = (retrieval.get("evidence") or [{}])[0] if retrieval.get("evidence") else {}
        code = ev.get("exit") if isinstance(ev, dict) else None
        steps.append(Step(kind="tool_result", text=str(retrieval.get("summary", "")), tool_use_id=rid,
                          is_error=not retrieval.get("ok", True) or code not in (0, None), parent=parent))
    return steps


def _event_line(kind: str, e: dict[str, Any]) -> str:
    def ids(key: str) -> str:
        return ", ".join(e.get(key) or []) or "none"

    if kind == "plan":
        return f"plan: split {'accepted' if e.get('accepted') else 'rejected (whole statement used)'}"
    if kind == "explore.done":
        return f"explore: added {ids('added')}; revived {ids('revived')}"
    if kind == "select.done":
        return f"select: chose {ids('chosen')} ({e.get('by', '')})"
    if kind == "candidate_analysis.revision":
        return (f"candidate analysis {e.get('hypothesis')}: revised claim "
                f"{'accepted as ' + str(e.get('child')) if e.get('accepted') else 'not accepted'}")
    if kind == "candidate_analysis.done":
        return (f"candidate analysis {e.get('hypothesis')} done: {e.get('iterations')} iteration(s), "
                f"stop {e.get('stop')}, final {e.get('final')}")
    if kind == "comparative_analysis.done":
        return f"comparative analysis: observations {ids('observations')}"
    if kind == "exploit.done":
        return "exploit: " + "; ".join(f"{op} {ids(op)}" for op in ("keep", "refine", "repair", "shelve"))
    if kind == "round.done":
        return (f"round {e.get('round')} done: tested {ids('tested')}; shelved {ids('shelved')}; "
                f"{e.get('observations', 0)} observations")
    if kind == "finalize":
        return f"finalize: answer {str(e.get('answer'))[:200]!r} (from {e.get('source')})"
    if kind == "merge.done":
        return f"merge regions: {ids('merged')}"
    if kind == "budget.stop":
        return f"budget stop at {e.get('where')}: {e.get('reason', '')}"
    if kind == "error":
        return f"error: {str(e.get('error'))[:300]}"
    return f"{kind}: {e.get('stopped_by', '')}"


def _final_state(final: dict[str, Any]) -> list[str]:
    lines = ["## Final state", "", "### Hypotheses", "", "| id | status | region | statement | notes |",
             "|---|---|---|---|---|"]
    for h in final.get("hypotheses") or []:
        cell = lambda s: " ".join(str(s or "").split()).replace("|", "/")  # noqa: E731
        note = h.get("reason") if h.get("status") == "shelved" else h.get("notes")
        lines.append(f"| {h.get('id')} | {h.get('status')} | {cell(h.get('region'))} | {cell(h.get('statement'))[:300]} "
                     f"| {cell(note)[:200]} |")
    lines += ["", "### Observations", ""]
    for o in final.get("observations") or []:
        verdicts = "; ".join(f"{hid} {v.get('relation')}" for hid, v in (o.get("verdicts") or {}).items())
        lines.append(f"- [{o.get('id')}] (round {o.get('round')}, {o.get('origin')}, {o.get('region')}) "
                     f"{' '.join(str(o.get('content', '')).split())[:500]}" + (f" — {verdicts}" if verdicts else ""))
    return lines + [""]
