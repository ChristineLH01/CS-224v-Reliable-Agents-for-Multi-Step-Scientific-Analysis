"""Run every (problem, repeat) attempt: workspace → sandbox → agent → artifacts → grade.

An attempt directory is the unit of resumption: `result.json` with a terminal status
means done, and re-running the same config skips it. Problems run one after
another (all attempts at one problem, then the next), so each problem's data is
on disk only while its attempts need it.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from bmbench import __version__
from bmbench.agents.base import Agent, classify_error, make_agent
from bmbench.config import RunConfig, apply_overrides, resolve, secret_values
from bmbench.dataset import (
    PREPARED_MARKER,
    Problem,
    list_input_files,
    load_problems,
    prepare_meta,
    prepare_problems,
    select_problems,
    problem_data_dir,
)
from bmbench.grader import grade_attempt, render
from bmbench.sandbox import DATA_DIR, WORKDIR, DockerSandbox, image_info, run_cmd, sanitize_name
from bmbench.trajectory import Segment, final_answer, render_transcript, tool_counts

#: statuses that count as a finished attempt (re-runs skip them)
TERMINAL = {"completed", "no_answer", "timeout", "agent_error"}
#: statuses that stop the whole run: nothing after them can succeed
FATAL = {"auth_error", "rate_limited"}


class RunAborted(RuntimeError):
    pass


class PromptChanged(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def attempt_dir(run_dir: Path, problem_id: str, attempt: int) -> Path:
    return run_dir / "problems" / problem_id / f"attempt-{attempt}"


def problem_prompt(problem_dir: Path, prompt: str) -> Path:
    """One prompt file per problem, shared by all its attempts: the question, its data
    and the run's settings are the same for every attempt. A different prompt for a
    later attempt means the settings changed under the same run name."""
    problem_dir.mkdir(parents=True, exist_ok=True)
    path = problem_dir / "prompt.md"
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(prompt)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != prompt:
            raise PromptChanged(
                f"{path} differs from the prompt this run would send now: a setting it states "
                "(time limit, CPUs, memory, allowed domains) or prompts/problem.md changed since the earlier "
                "attempts. Use a new --run-name, or restore the settings."
            ) from None
    return path


def effective_domains(problem: Problem, cfg: RunConfig,
                      agent_domains: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """(domains the agent is told about, domains the proxy admits: those plus the
    infrastructure every agent needs and this agent's own model API)."""
    sb = cfg.sandbox
    told = list(problem.allowed_domains if sb.override_allowed_domains is None else sb.override_allowed_domains)
    told += [d for d in sb.extra_allowed_domains if d not in told]
    admitted = told + [d for d in (*sb.infra_domains, *agent_domains) if d not in told]
    return told, admitted


def fmt_memory(mem: str) -> str:
    m = str(mem).strip().lower()
    for suffix, unit in (("gb", "GB"), ("g", "GB"), ("mb", "MB"), ("m", "MB")):
        if m.endswith(suffix) and m[: -len(suffix)].replace(".", "", 1).isdigit():
            return f"{m[: -len(suffix)]} {unit}"
    return str(mem)


def render_problem_prompt(problem: Problem, cfg: RunConfig, n_files: int) -> str:
    told, _ = effective_domains(problem, cfg)
    template = resolve(cfg.prompt_template).read_text(encoding="utf-8")
    cpus = cfg.sandbox.cpus
    return render(
        template,
        question=problem.question,
        data_dir=DATA_DIR,
        workdir=WORKDIR,
        n_files=f"{n_files} file" + ("" if n_files == 1 else "s"),
        cpus=int(cpus) if float(cpus).is_integer() else cpus,
        memory=fmt_memory(cfg.sandbox.memory),
        allowed_domains=", ".join(told) if told else "none (no internet access)",
        time_limit_min=max(1, round(cfg.sandbox.timeout_s / 60)),
    )


def clone_tree(src: Path, dst: Path) -> None:
    """Copy-on-write clone where the filesystem supports it (APFS, btrfs, XFS)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    cmds = (
        [["cp", "-cR", str(src), str(dst)]] if sys.platform == "darwin"
        else [["cp", "-a", "--reflink=auto", str(src), str(dst)]]
    )
    for cmd in cmds:
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            break
        shutil.rmtree(dst, ignore_errors=True)
    else:
        shutil.copytree(src, dst, symlinks=True)
    (dst / PREPARED_MARKER).unlink(missing_ok=True)


def rmtree(path: Path) -> None:
    """Delete a tree, also where the agent took away its own write permission."""
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        for dirpath, _dirs, _files in os.walk(path):
            os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IRWXU)
        shutil.rmtree(path, ignore_errors=True)


def remove_workspace(workspace: Path, listing: Path) -> None:
    """Record every file the attempt left behind (path, bytes), then delete them all."""
    rows = []
    for dirpath, _dirs, files in os.walk(workspace):
        for fn in files:
            path = Path(dirpath) / fn
            try:
                size = path.lstat().st_size
            except OSError:
                continue
            rows.append((path.relative_to(workspace).as_posix(), size))
    with open(listing, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["path", "bytes"])
        w.writerows(sorted(rows))
    rmtree(workspace)


def remove_inputs(data_dir: Path, inputs: list[str]) -> None:
    """Delete the cloned input files but keep anything the agent added next to them."""
    for rel in inputs:
        (data_dir / rel).unlink(missing_ok=True)
    for dirpath, _dirs, _files in sorted(os.walk(data_dir), key=lambda t: -len(t[0])):
        try:
            os.rmdir(dirpath)  # only succeeds when empty
        except OSError:
            pass


def scrub_secrets(root: Path, secrets: list[str], max_bytes: int = 50 * 1024 * 1024) -> None:
    if not secrets:
        return
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            path = Path(dirpath) / fn
            try:
                if path.is_symlink() or path.stat().st_size > max_bytes:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            new = data
            for s in secrets:
                new = new.replace(s.encode(), b"[REDACTED]")
            if new != data:
                path.write_bytes(new)


def classify(segments: list[Segment], answer: str | None) -> tuple[str, str]:
    """(status, error kind) for a finished attempt."""
    errors = "\n".join(s.error_text for s in segments if s.error_text)
    kind = classify_error(errors) or ""
    if answer:
        return "completed", kind
    if kind == "auth":
        return "auth_error", kind
    if kind == "rate_limit":
        return "rate_limited", kind
    if any(s.timed_out for s in segments):
        return "timeout", kind
    if not any(s.got_result for s in segments) or any(s.is_error for s in segments):
        return "agent_error", kind
    return "no_answer", kind


async def run_attempt(cfg: RunConfig, agent: Agent, problem: Problem, attempt: int,
                    run_dir: Path, *, force: bool = False) -> dict[str, Any]:
    adir = attempt_dir(run_dir, problem.id, attempt)
    result_path = adir / "result.json"
    if result_path.is_file() and not force:
        prior = json.loads(result_path.read_text(encoding="utf-8"))
        if prior.get("status") in TERMINAL:
            prior["_skipped"] = True
            return prior
    sb = cfg.sandbox
    src = problem_data_dir(cfg.dataset.name, problem.id, cfg.dataset.root)
    if not (src / PREPARED_MARKER).exists():
        raise RuntimeError(f"data for {problem.id} is missing under {src}")
    inputs = list_input_files(src)
    prompt_path = problem_prompt(adir.parent, render_problem_prompt(problem, cfg, len(inputs)))

    if adir.exists():
        if run_dir.resolve() not in adir.resolve().parents:
            raise RuntimeError(f"refusing to delete {adir}: outside the run directory")
        rmtree(adir)
    adir.mkdir(parents=True)
    workspace = adir / "workspace"
    workspace.mkdir()
    readonly = None
    if sb.input_mode == "copy":
        clone_tree(src, workspace / "data")
    elif sb.input_mode == "readonly":
        (workspace / "data").mkdir()
        readonly = src
    else:
        raise ValueError(f"sandbox.input_mode must be 'copy' or 'readonly', not {sb.input_mode!r}")

    _, admitted = effective_domains(problem, cfg, agent.infra_domains)
    session_id = str(uuid.uuid4())
    started = now()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    segments: list[Segment] = []
    finalized = False
    nudges = 0
    harness_error = ""

    box = DockerSandbox(
        name=f"bmb-{sanitize_name(cfg.run_name)[:24]}-{problem.id[:20]}-a{attempt}-{session_id[:6]}",
        cfg=sb, workspace=workspace, allowed_domains=admitted, readonly_data=readonly,
        run_label=sanitize_name(cfg.run_name),
    )

    async def invoke(kind: str, stdin: Path, timeout_s: float, resume: bool) -> Segment:
        spent = sum(s.cost_usd for s in segments)
        budget = None
        if agent.cfg.max_budget_usd is not None:
            budget = max(0.0, agent.cfg.max_budget_usd - spent)
        argv = agent.command(timeout_s=int(timeout_s), session_id=session_id, resume=resume, budget_usd=budget)
        out, err = adir / f"stream.{kind}.jsonl", adir / f"stderr.{kind}.log"
        res = await box.exec(argv, stdin_path=stdin, stdout_path=out, stderr_path=err,
                             deadline_s=timeout_s + 120, env=agent.env(), secret_env=agent.secret_env)
        seg = Segment(kind=kind, exit_code=res.code, timed_out=res.timed_out, seconds=res.seconds)
        seg = agent.parse(out, err, seg)
        segments.append(seg)
        return seg

    try:
        await box.start()
        seg = await invoke("main", prompt_path, sb.timeout_s, resume=False)
        while True:
            answer, _ = final_answer(segments)
            if answer or classify_error(seg.error_text) in ("auth", "rate_limit"):
                break
            if seg.timed_out:
                if agent.cfg.finalize_on_timeout and not finalized:
                    await box.kill_agent_processes()
                    finalize = adir / "finalize.md"
                    finalize.write_text(resolve("prompts/finalize.md").read_text(encoding="utf-8"), encoding="utf-8")
                    finalized = True
                    seg = await invoke("finalize", finalize, agent.cfg.finalize_timeout_s, resume=True)
                    continue
                break
            remaining = sb.timeout_s - sum(s.seconds for s in segments if s.kind != "finalize")
            if seg.got_result and not seg.is_error and nudges < agent.cfg.max_nudges and remaining > 60:
                nudges += 1
                nudge = adir / "nudge.md"
                nudge.write_text(resolve("prompts/nudge.md").read_text(encoding="utf-8"), encoding="utf-8")
                seg = await invoke(f"nudge{nudges}", nudge, remaining, resume=True)
                continue
            break
    except asyncio.CancelledError:
        harness_error = "interrupted"
        raise
    except Exception as e:  # noqa: BLE001 - recorded, and the attempt is retried on the next run
        harness_error = f"{type(e).__name__}: {e}"
    finally:
        try:
            for d in agent.session_dirs:
                await box.copy_out(d, adir / "agent_session" / Path(d).name.lstrip("."))
            await box.proxy_log(adir / "network.log")
        except Exception:  # noqa: BLE001
            pass
        await box.stop()
        if not sb.keep_workspace:
            remove_workspace(workspace, adir / "workspace_files.tsv")
        elif sb.input_mode == "copy" and not sb.keep_input_data:
            remove_inputs(workspace / "data", inputs)

        answer, final_text = final_answer(segments)
        status, err_kind = classify(segments, answer)
        if harness_error:
            status = "harness_error"
        usage_tokens = agent.session_usage(adir / "agent_session") or _sum_usage(segments)
        # a segment killed before its result event never reported its dollar cost;
        # agents priced from their session's token totals do not have that gap
        cost = agent.cost_from_tokens(usage_tokens)
        cost_incomplete = cost is None and any(not s.got_result and s.steps for s in segments)
        if cost is None:
            cost = sum(s.cost_usd for s in segments)
        # what the audit and transcript see: the invocations plus subagent work that
        # only the agent's session records hold (the answer comes from the main thread)
        recorded = segments + agent.subagent_segments(adir / "agent_session")
        result = {
            "problem_id": problem.id,
            "attempt": attempt,
            "human_solvable": problem.human_solvable,
            "status": status,
            "error_kind": err_kind,
            "harness_error": harness_error,
            "answer": answer,
            "final_text": final_text,
            "agent": agent.name,
            "model": agent.cfg.model,
            "effort": agent.cfg.effort,
            "session_id": session_id,
            "cost_usd": round(cost, 6),
            "cost_incomplete": cost_incomplete,
            "num_turns": sum(s.num_turns for s in segments),
            "agent_seconds": round(sum(s.seconds for s in segments), 1),
            "wall_seconds": round(loop.time() - t0, 1),
            "tokens": usage_tokens,
            "timed_out": any(s.timed_out for s in segments),
            "finalized_after_timeout": finalized,
            "nudges": nudges,
            "tool_counts": tool_counts(recorded),
            "rate_limit": next((s.rate_limit for s in reversed(segments) if s.rate_limit), {}),
            "segments": [s.summary() for s in recorded],
            "started_at": started,
            "finished_at": now(),
        }
        (adir / "trajectory.json").write_text(
            json.dumps({"segments": [asdict(s) for s in recorded]}, ensure_ascii=False), encoding="utf-8"
        )
        (adir / "transcript.md").write_text(render_transcript(problem.id, attempt, recorded), encoding="utf-8")
        result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        scrub_secrets(adir, secret_values())
    return result


def _sum_usage(segments: list[Segment]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: sum(int((s.usage or {}).get(k) or 0) for s in segments) for k in keys}


# -- run-level bookkeeping -------------------------------------------------------


async def write_manifest(cfg: RunConfig, agent: Agent, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_dict = cfg.to_dict()
    (run_dir / "config.yaml").write_text(yaml.safe_dump(cfg_dict, sort_keys=False), encoding="utf-8")
    img = await image_info(cfg.sandbox.image)
    if img is None:
        raise RuntimeError(f"sandbox image {cfg.sandbox.image} not found: run `bmbench build`")
    proxy = await image_info(cfg.sandbox.proxy_image) if cfg.sandbox.network == "allowlist" else None
    if cfg.sandbox.network == "allowlist" and proxy is None:
        raise RuntimeError(f"proxy image {cfg.sandbox.proxy_image} not found: run `bmbench build`")
    version = ""
    if agent.version_command():
        _, version, _ = await run_cmd("docker", "run", "--rm", "-u", "agent", cfg.sandbox.image,
                                      *agent.version_command(), check=False)
    lock = run_dir / "conda-env.lock.yml"
    if not lock.exists():
        _, text, _ = await run_cmd("docker", "run", "--rm", cfg.sandbox.image, "cat",
                                   "/opt/sandbox/conda-env.lock.yml", check=False)
        lock.write_text(text, encoding="utf-8")
    entry = {
        "started_at": now(),
        "bmbench_version": __version__,
        "config_sha256": hashlib.sha256(json.dumps(cfg_dict, sort_keys=True).encode()).hexdigest()[:16],
        "agent": agent.name,
        "agent_version": version.strip(),
        "sandbox_image": img,
        "proxy_image": proxy,
        "host": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
        "argv": sys.argv,
    }
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"invocations": []}
    manifest["invocations"].append(entry)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _short(text: str | None, n: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def finished(adir: Path) -> bool:
    path = adir / "result.json"
    return path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("status") in TERMINAL


class ProblemData:
    """Each problem's input data, on disk only while its attempts need it.

    A problem's data downloads when its first attempt is about to start. The next
    problem's download starts with the current problem's last attempt, so it
    overlaps with that attempt. With `dataset.delete_after_problem`, the data is deleted
    once every attempt of the problem has a final status (an attempt stopped by a usage
    limit keeps it, for the resumed run).
    """

    def __init__(self, cfg: RunConfig, run_dir: Path, pending: dict[str, int]) -> None:
        self.cfg, self.run_dir = cfg, run_dir
        self.order = [t for t, n in pending.items() if n]  # problems with attempts to run
        self.pending = dict(pending)
        self.started: Counter[str] = Counter()
        self.left = dict(pending)
        self.fetches: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()  # one download at a time

    def _dir(self, problem_id: str) -> Path:
        return problem_data_dir(self.cfg.dataset.name, problem_id, self.cfg.dataset.root)

    def _fetch(self, problem_id: str) -> asyncio.Task[None]:
        if problem_id not in self.fetches:
            task = asyncio.create_task(self._download(problem_id))
            # a prefetch nobody waits for (the run stopped) must not warn at exit
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self.fetches[problem_id] = task
        return self.fetches[problem_id]

    async def _download(self, problem_id: str) -> None:
        if (self._dir(problem_id) / PREPARED_MARKER).exists():
            return
        async with self.lock:
            print(f"  downloading data for {problem_id}")
            await asyncio.to_thread(prepare_problems, self.cfg.dataset.name, self.cfg.dataset.root, [problem_id])

    async def acquire(self, problem_id: str) -> None:
        """Wait until the problem's data is on disk."""
        self.started[problem_id] += 1
        fetch = self._fetch(problem_id)
        if self.started[problem_id] == self.pending[problem_id]:
            i = self.order.index(problem_id)
            if i + 1 < len(self.order):
                self._fetch(self.order[i + 1])
        await asyncio.shield(fetch)

    def release(self, problem_id: str) -> None:
        self.left[problem_id] -= 1
        if self.left[problem_id] or not self.cfg.dataset.delete_after_problem:
            return
        if all(finished(attempt_dir(self.run_dir, problem_id, k)) for k in range(1, self.cfg.n_attempts + 1)):
            rmtree(self._dir(problem_id))
            print(f"  deleted data for {problem_id}")


async def run(cfg: RunConfig, *, force: bool = False, grade: bool | None = None) -> Path:
    try:
        all_problems = load_problems(cfg.dataset.name, cfg.dataset.root)
    except FileNotFoundError:
        prepare_meta(cfg.dataset.name, cfg.dataset.root)
        all_problems = load_problems(cfg.dataset.name, cfg.dataset.root)
    problems = select_problems(all_problems, cfg.dataset.problem_ids, cfg.dataset.human_solvable)
    if not problems:
        raise RuntimeError("no problems selected")
    agent = make_agent(cfg.agent)
    run_dir = cfg.run_dir
    await write_manifest(cfg, agent, run_dir)
    grade = cfg.grader.auto if grade is None else grade

    # every attempt at one problem, then the next problem
    jobs = [(p, k) for p in problems for k in range(1, cfg.n_attempts + 1)]
    todo = {(p.id, k) for p, k in jobs if force or not finished(attempt_dir(run_dir, p.id, k))}
    data = ProblemData(cfg, run_dir, {p.id: sum(1 for k in range(1, cfg.n_attempts + 1) if (p.id, k) in todo)
                                   for p in problems})
    total = len(jobs)
    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    abort: list[str] = []
    done = 0
    print(f"run {cfg.run_name}: {len(problems)} problems × {cfg.n_attempts} attempts = {total} "
          f"({total - len(todo)} already done) → {run_dir}")

    async def one(problem: Problem, k: int) -> dict[str, Any] | None:
        nonlocal done
        async with sem:
            if abort:
                return None
            adir = attempt_dir(run_dir, problem.id, k)
            needs_data = (problem.id, k) in todo
            if needs_data:
                try:
                    await data.acquire(problem.id)
                except Exception as e:  # noqa: BLE001
                    abort.append(f"data download failed for {problem.id}: {_short(str(e), 300)}")
                    return None
            try:
                result = await run_attempt(cfg, agent, problem, k, run_dir, force=force)
            except PromptChanged as e:
                abort.append(str(e))
                return None
            finally:
                if needs_data:
                    data.release(problem.id)
            done += 1
            idx = done  # `done` moves on while this attempt is being graded
            skipped = result.pop("_skipped", False)
            g = None
            if grade and result.get("status") in TERMINAL:
                try:
                    g = await grade_attempt(adir, problem, cfg.grader, cfg.sandbox.image)
                except Exception as e:  # noqa: BLE001 - grading can be redone with `bmbench grade`
                    print(f"    grading failed for {problem.id} r{k}: {e}")
            mark = "" if g is None else (" ✓" if g.get("score") else " ✗") + (" [cheated]" if g.get("cheated") else "")
            tag = "skip (done)" if skipped else result["status"]
            window = ((result.get("rate_limit") or {}).get("unifiedWindows") or {}).get("five_hour") or {}
            used = f", 5h-window {100 * window['utilization']:.0f}% used" if window.get("utilization") is not None else ""
            print(f"[{idx}/{total}] {problem.id} r{k}: {tag}, {result.get('agent_seconds', 0) / 60:.1f} min, "
                  f"${result.get('cost_usd', 0):.2f}{used}, answer={_short(result.get('answer'))!r}{mark}"
                  + (f"  ({result['harness_error']})" if result.get("harness_error") else ""))
            if result["status"] in FATAL:
                abort.append(f"{result['status']} on {problem.id} r{k}: {_short(result['segments'][-1]['error_text'] if result['segments'] else '', 300)}")
            return result

    await asyncio.gather(*(one(p, k) for p, k in jobs))
    from bmbench.report import write_report  # noqa: PLC0415

    write_report(run_dir)
    if abort:
        raise RunAborted(
            abort[0] + "\nStopped early. Fix the cause (auth token, usage limit, download) "
            "and re-run the same command to resume."
        )
    return run_dir


async def grade_run(run_dir: Path, *, force: bool = False, problems: list[str] | None = None,
                    concurrency: int = 2, overrides: list[str] | None = None) -> None:
    cfg = load_run_config(run_dir, overrides)
    problems = {p.id: p for p in load_problems(cfg.dataset.name, cfg.dataset.root)}
    sem = asyncio.Semaphore(concurrency)
    attempt_dirs = sorted((run_dir / "problems").glob("*/attempt-*"))

    async def one(adir: Path) -> None:
        problem_id = adir.parent.name
        if problems and problem_id not in problems:
            return
        async with sem:
            try:
                g = await grade_attempt(adir, problems[problem_id], cfg.grader, cfg.sandbox.image, force=force)
                if g is not None:
                    print(f"{problem_id} {adir.name}: score={g['score']} correct={g['correct']} cheated={g['cheated']}")
            except Exception as e:  # noqa: BLE001
                print(f"{problem_id} {adir.name}: grading failed: {e}")

    await asyncio.gather(*(one(t) for t in attempt_dirs))


def load_run_config(run_dir: Path, overrides: list[str] | None = None) -> RunConfig:
    from bmbench.config import _build  # noqa: PLC0415

    data = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8"))
    return _build(RunConfig, apply_overrides(data, overrides or []), "")
