"""`python -m bmb_sheldon.driver`: what the bmbench sandbox runs for one SHELDON attempt.

    stdin    the problem prompt (prompts/problem.md, rendered by bmbench)
    env      BMB_SHELDON_CONFIG  JSON: {"sheldon": <SHELDON Config fields>, "usd_per_mtok": [...],
                                       "workdir": "/workspace", "home": "/home/agent/.sheldon"}
             LITELLM_BASE_URL, LITELLM_API_KEY   the model endpoint (the key passed by name)
    argv     --time-limit S   the attempt's limit: SHELDON's `max_seconds`, and the moment
                              its running commands are stopped (see watchdog.py)
             --selftest       the whole loop offline, against SHELDON's scripted FakeLLM
             --ping           four tiny real calls: plain, tool round trip, multi-turn, structured
    stdout   JSON lines: {"type": "start", ...} then {"type": "result", ...}

SHELDON runs through its own `run_experiment`, unmodified; this module only builds
its config, hands it the Responses transport as `llm=`, watches the clock, and
reports what SHELDON's `predictions.jsonl` says.
"""

from __future__ import annotations

import time

T0 = time.monotonic()  # the attempt's clock starts before the heavy imports

import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from bmb_sheldon import SHELDON_COMMIT, __version__  # noqa: E402

#: SHELDON Config fields bmbench sets itself; the run config may not move them
FIXED = {"benchmark", "split", "limit", "tasks", "seed", "concurrency", "call_concurrency", "cache",
         "resume", "tag", "max_seconds"}


def emit(out: Any, kind: str, **fields: Any) -> None:
    out.write(json.dumps({"type": kind, **fields}, default=str) + "\n")
    out.flush()


def shell_probes(spec: Any, workdir: str) -> list[Any]:
    """SHELDON's `run_shell`, opened in the attempt's working directory (where the data is)."""
    out = []
    for item in spec or []:
        name, kwargs = (item, {}) if isinstance(item, str) else next(iter(item.items()))
        if name == "run_shell":
            kwargs = dict(kwargs or {})
            kwargs.setdefault("cwd", workdir)
            kwargs["env"] = {"SHELDON_WORKSPACE": workdir, **(kwargs.get("env") or {})}
            item = {"run_shell": kwargs}
        out.append(item)
    return out


def build_config(conf: dict[str, Any], task_file: Path, max_seconds: float) -> Any:
    from sheldon.core.config import Config  # noqa: PLC0415

    from bmb_sheldon.benchmark import NAME  # noqa: PLC0415

    fields = {k: v for k, v in dict(conf.get("sheldon") or {}).items() if k not in FIXED}
    params = dict(fields.pop("params", None) or {})
    params["probes"] = shell_probes(params.get("probes", ["run_shell"]), conf.get("workdir") or "/workspace")
    params["benchmark_opts"] = {**(params.get("benchmark_opts") or {}), "task_file": str(task_file)}
    return Config(**fields, params=params, benchmark=NAME, split="attempt", limit=None, tasks="", seed=0,
                  concurrency=1, cache=False, resume=False, tag="bmbench", max_seconds=max_seconds)


def make_llm(cfg: Any, conf: dict[str, Any]) -> Any:
    from sheldon.core.cache import DiskCache  # noqa: PLC0415

    from bmb_sheldon.transport import ResponsesLLM  # noqa: PLC0415

    base = os.environ.get("LITELLM_BASE_URL", "").strip()
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if not base or not key:
        raise RuntimeError("LITELLM_BASE_URL and LITELLM_API_KEY must be set (invalid api key: none given)")
    return ResponsesLLM(base_url=base, api_key=key, usd_per_mtok=conf.get("usd_per_mtok"), model=cfg.model,
                        effort=cfg.effort, max_tokens=cfg.max_tokens, thinking=cfg.thinking,
                        cache=DiskCache(enabled=False), concurrency=4, trace_prompts=cfg.trace_prompts)


def quiet_stream_shutdown(loop: asyncio.AbstractEventLoop) -> None:
    """httpx leaves the async generators of finished response streams to be closed when
    the event loop shuts down, which logs a harmless traceback per stream to stderr;
    drop exactly those, keep every other loop error."""

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        message = str(context.get("message", ""))
        source = getattr(getattr(context.get("asyncgen"), "ag_code", None), "co_filename", "")
        if "closing of asynchronous generator" in message and "httpcore" in source:
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def summarize_belief(belief: dict[str, Any] | None) -> dict[str, Any]:
    belief = belief or {}
    hyps = belief.get("hypotheses") or []
    return {"hypotheses": dict(Counter(h.get("status", "?") for h in hyps)),
            "n_hypotheses": len(hyps), "n_observations": len(belief.get("observations") or [])}


async def run_sheldon(prompt: str, conf: dict[str, Any], time_limit_s: float, out: Any, llm: Any = None,
                      task_id: str = "problem") -> dict[str, Any]:
    """Run one attempt and return (and print) the result line."""
    from bmb_sheldon import benchmark as _registers  # noqa: F401,PLC0415
    from bmb_sheldon.watchdog import TimeLimit  # noqa: PLC0415

    quiet_stream_shutdown(asyncio.get_running_loop())

    home = Path(conf.get("home") or "/home/agent/.sheldon")
    home.mkdir(parents=True, exist_ok=True)
    os.environ["SHELDON_RUNS"] = str(home / "runs")
    os.environ["SHELDON_DATA"] = str(home / "data")
    os.environ["SHELDON_CACHE"] = str(home / "cache" / "llm.db")
    task_file = home / "task.json"
    task_file.write_text(json.dumps({"id": task_id, "question": prompt}), encoding="utf-8")

    deadline = T0 + time_limit_s
    cfg = build_config(conf, task_file, max(1.0, deadline - time.monotonic()))
    from sheldon.core.io import read_jsonl  # noqa: PLC0415
    from sheldon.runner import run_dir, run_experiment  # noqa: PLC0415

    own_llm = llm is None
    llm = llm or make_llm(cfg, conf)
    emit(out, "start", sheldon_commit=SHELDON_COMMIT, runtime=__version__, run_id=cfg.run_id, model=cfg.model,
         effort=cfg.effort, mode=cfg.params.get("mode", "staged"), max_rounds=cfg.max_rounds,
         time_limit_s=time_limit_s)
    limit = TimeLimit(deadline).start()
    error = ""
    try:
        with contextlib.redirect_stdout(sys.stderr):  # stdout carries only the JSON lines
            await run_experiment(cfg, llm=llm)
    except Exception as e:  # noqa: BLE001 - reported in the result line
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc(file=sys.stderr)
    finally:
        ended = time.monotonic()
        limit.stop()
        if own_llm:
            with contextlib.suppress(Exception):
                await llm.aclose()
    rows = list(read_jsonl(run_dir(cfg) / "predictions.jsonl")) if (run_dir(cfg) / "predictions.jsonl").exists() else []
    pred = rows[-1] if rows else {}
    stop = str((pred.get("extra") or {}).get("stop_reason") or "")
    answer = pred.get("answer")
    result = {
        "answer": answer if isinstance(answer, str) else ("" if answer is None else json.dumps(answer)),
        "error": pred.get("error") or error or ("" if rows else "SHELDON wrote no prediction"),
        "rounds": pred.get("rounds", 0),
        "stop_reason": stop,
        "usage": pred.get("usage") or llm.total.to_dict(),
        "wall_s": round(ended - T0, 1),
        "time_limit_s": time_limit_s,
        "time_limit_hit": limit.hit or "max_seconds" in stop,
        "seconds_after_limit": round(max(0.0, ended - deadline), 1),
        "commands_killed": limit.killed,
        "run_dir": str(run_dir(cfg)),
        **summarize_belief(pred.get("belief")),
    }
    emit(out, "result", **result)
    return result


async def ping(conf: dict[str, Any], out: Any) -> dict[str, Any]:
    """Four tiny calls through the same transport SHELDON uses."""
    from pydantic import BaseModel  # noqa: PLC0415

    cfg = build_config(conf, Path("/dev/null"), 60)
    llm = make_llm(cfg, conf)
    token = "zebra-4471"
    tool = {"name": "read_token", "description": "Return the secret token.",
            "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}

    async def execute(_name: str, _args: dict[str, Any]) -> str:
        return token

    class Organism(BaseModel):
        species: str
        confidence: float = 0.5

    checks: dict[str, Any] = {}
    try:
        r = await llm.complete("Reply with exactly: OK", tag="ping")
        checks["plain"] = r.text.strip().strip(".") == "OK"
        c = await llm.converse("Call read_token, then reply with the token and nothing else.", tools=[tool],
                               execute=execute, max_iterations=3, tag="ping")
        checks["tool_round_trip"] = bool(c.tool_calls) and token in c.text
        c2 = await llm.converse("Now reply with that token spelled backwards, nothing else.", tools=[tool],
                                execute=execute, history=c.messages, max_iterations=2, tag="ping")
        checks["multi_turn"] = token[::-1] in c2.text.replace(" ", "")
        s = await llm.complete("Which species is the gene BRCA1 best known from? Answer as JSON.", schema=Organism,
                               tag="ping")
        checks["structured"] = s.parsed is not None and "sapiens" in s.parsed.species.lower()
        error = ""
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            await llm.aclose()
    ok = not error and len(checks) == 4 and all(checks.values())
    result = {"answer": "OK" if ok else "", "final_text": "OK" if ok else f"FAILED {checks} {error}".strip(),
              "error": error, "checks": checks, "model": cfg.model, "effort": cfg.effort,
              "usage": llm.total.to_dict()}
    emit(out, "result", **result)
    return result


def selftest(conf: dict[str, Any], out: Any) -> bool:
    """The full loop against SHELDON's FakeLLM, in a throwaway workspace."""
    from bmb_sheldon.selftest import ANSWER, COMMAND, MARK, QUESTION, fake_llm  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="bmb-sheldon-selftest-") as tmp:
        work = Path(tmp) / "workspace"
        (work / "data").mkdir(parents=True)
        (work / "data" / "sample.fa").write_text(">seq1\nACGT\n", encoding="utf-8")
        local = {**conf, "workdir": str(work), "home": str(Path(tmp) / "home")}
        sheldon = dict(local.get("sheldon") or {})
        llm = fake_llm(model=sheldon.get("model", "fake"))
        result = asyncio.run(run_sheldon(f"## Question\n\n{QUESTION}\n", local, 300, out, llm=llm))
        retrievals = list(Path(result["run_dir"]).glob("retrievals/*/r*.json"))
        outputs = [json.loads(p.read_text(encoding="utf-8")) for p in retrievals]
        ran = [o for o in outputs if (o.get("arguments") or {}).get("command") == COMMAND]
        checks = {
            "no error": not result["error"],
            "answer": result["answer"] == ANSWER,
            "shell ran in the workspace": any(f"{MARK} 1 files in {work.resolve()}" in str(o.get("summary")) for o in ran),
            "rounds": result["rounds"] >= 1,
            "trace written": bool(list(Path(result["run_dir"]).glob("traces/*.jsonl"))),
        }
        if Path("/etc/profile.d/00-oom-first.sh").exists():  # in the sandbox image
            checks["commands first in line for the OOM killer"] = any(
                "oom_score_adj=1000" in str(o.get("summary")) for o in ran)
    ok = all(checks.values())
    emit(out, "selftest", ok=ok, checks=checks)
    return ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bmb_sheldon.driver")
    p.add_argument("--time-limit", type=float, default=3600.0)
    p.add_argument("--task-id", default="problem")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--ping", action="store_true")
    p.add_argument("--version", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    out = sys.stdout
    if a.version:
        print(f"SHELDON {SHELDON_COMMIT[:7]} (bmb_sheldon {__version__})")
        return 0
    try:
        conf = json.loads(os.environ.get("BMB_SHELDON_CONFIG") or "{}")
    except json.JSONDecodeError as e:
        emit(out, "result", answer="", error=f"BMB_SHELDON_CONFIG is not JSON: {e}")
        return 2
    if a.selftest:
        return 0 if selftest(conf, out) else 1
    if a.ping:
        return 0 if asyncio.run(ping(conf, out))["answer"] == "OK" else 1
    prompt = sys.stdin.read()
    if not prompt.strip():
        emit(out, "result", answer="", error="no prompt on stdin")
        return 2
    try:
        result = asyncio.run(run_sheldon(prompt, conf, a.time_limit, out, task_id=a.task_id))
    except Exception as e:  # noqa: BLE001 - a config or setup failure, before SHELDON ran
        traceback.print_exc(file=sys.stderr)
        emit(out, "result", answer="", error=f"{type(e).__name__}: {e}")
        return 1
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
