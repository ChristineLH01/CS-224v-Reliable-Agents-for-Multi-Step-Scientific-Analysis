"""bmbench: prepare | problems | build | doctor | run | grade | report | show | clean"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from bmbench.config import REPO_ROOT, SECRET_ENV, load_config, load_dotenv, resolve
from bmbench.dataset import PREPARED_MARKER, dataset_dir, dir_size, load_problems, prepare, select_problems


def _ids(text: str | None) -> list[str] | None:
    return [t.strip() for t in text.split(",") if t.strip()] if text else None


def cmd_prepare(a: argparse.Namespace) -> None:
    ids = prepare(a.dataset, a.root, ids=_ids(a.ids), force=a.force, keep_zips=a.keep_zips)
    print(f"ready: {len(ids)} problem(s) under {dataset_dir(a.dataset, a.root) / 'problems'}")


def cmd_problems(a: argparse.Namespace) -> None:
    problems = load_problems(a.dataset, a.root)
    base = dataset_dir(a.dataset, a.root) / "problems"
    print(f"{'id':22s} {'solvable':8s} {'data':>9s}  question")
    for p in select_problems(problems, None, a.human_solvable):
        d = base / p.id
        size = f"{dir_size(d) / 1e6:.1f} MB" if (d / PREPARED_MARKER).exists() else "—"
        q = " ".join(p.question.split())
        print(f"{p.id:22s} {p.human_solvable:8s} {size:>9s}  {q[:90]}{'…' if len(q) > 90 else ''}")


def _host_version(cli: str) -> str | None:
    exe = shutil.which(cli)
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def cmd_build(a: argparse.Namespace) -> None:
    cfg = load_config(a.config, a.set)
    claude = a.claude_version or _host_version("claude") or "stable"
    codex = a.codex_version or _host_version("codex")
    uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (1000, 1000)
    common = ["--no-cache"] if a.no_cache else []
    if cfg.sandbox.platform:
        common += ["--platform", cfg.sandbox.platform]
    versions = ["--build-arg", f"CLAUDE_CODE_VERSION={claude}"]
    if codex:  # otherwise the Dockerfile's pinned default
        versions += ["--build-arg", f"CODEX_VERSION={codex}"]
    steps = [
        ["docker", "build", *common, "--build-arg", f"AGENT_UID={uid}", "--build-arg", f"AGENT_GID={gid}",
         *versions, "-t", cfg.sandbox.image, str(REPO_ROOT / "docker/sandbox")],
        ["docker", "build", *common, "-t", cfg.sandbox.proxy_image, str(REPO_ROOT / "docker/proxy")],
    ]
    if a.only:
        steps = [steps[0]] if a.only == "sandbox" else [steps[1]]
    for argv in steps:
        print("+", " ".join(argv))
        if subprocess.run(argv).returncode != 0:
            sys.exit(f"build failed: {' '.join(argv[:4])}")
    print(f"built {cfg.sandbox.image} (Claude Code {claude}, Codex {codex or 'Dockerfile default'}) "
          f"and {cfg.sandbox.proxy_image}")


async def _doctor(a: argparse.Namespace) -> int:
    from bmbench.agents.base import make_agent  # noqa: PLC0415
    from bmbench.grader import ANSWER_SCHEMA, judge, judge_endpoint, uses_claude  # noqa: PLC0415
    from bmbench.sandbox import DockerSandbox, docker_ok, image_info, run_cmd  # noqa: PLC0415
    from bmbench.trajectory import Segment  # noqa: PLC0415

    cfg = load_config(a.config, a.set)
    agent = make_agent(cfg.agent)
    bad = 0

    def report(ok: bool, what: str, detail: str = "") -> None:
        nonlocal bad
        bad += 0 if ok else 1
        print(f"  [{'ok' if ok else '!!'}] {what}" + (f": {detail}" if detail else ""))

    ok, info = await docker_ok()
    report(ok, "docker daemon", info)
    if not ok:
        return 1
    for img in (cfg.sandbox.image, cfg.sandbox.proxy_image):
        meta = await image_info(img)
        report(meta is not None, f"image {img}", (meta or {}).get("architecture", "missing: run `bmbench build`"))
    _, ver, _ = await run_cmd("docker", "run", "--rm", "-u", "agent", cfg.sandbox.image, *agent.version_command(),
                              check=False)
    report(bool(ver.strip()), f"{agent.name} in image", ver.strip())
    auth = [k for k in agent.secret_env if os.environ.get(k)]
    report(bool(auth), f"{agent.name} credentials in environment/.env",
           ", ".join(auth) or f"set one of {', '.join(agent.secret_env)}")
    try:
        problems = load_problems(cfg.dataset.name, cfg.dataset.root)
        base = dataset_dir(cfg.dataset.name, cfg.dataset.root) / "problems"
        ready = [p.id for p in problems if (base / p.id / PREPARED_MARKER).exists()]
        rest = "" if len(ready) == len(problems) else " (the rest downloads during the run, one problem at a time)"
        report(True, f"dataset {cfg.dataset.name}", f"{len(problems)} problems; data on disk for {len(ready)}{rest}")
    except FileNotFoundError:
        report(True, f"dataset {cfg.dataset.name}", "not downloaded yet; `run` fetches it")
    if cfg.dataset.name == "full":
        report(bool(os.environ.get("HF_TOKEN")), "HF_TOKEN for the gated full set", "" if os.environ.get("HF_TOKEN") else "missing")
    judge_ok = True
    if uses_claude(cfg.grader):
        claude_auth = [k for k in SECRET_ENV if os.environ.get(k)]
        judge_ok = bool(claude_auth)
        report(judge_ok, f"judge {cfg.grader.model} via claude -p (Claude subscription)",
               ", ".join(claude_auth) or f"set one of {', '.join(SECRET_ENV)}")
    else:
        try:
            url, _key = judge_endpoint()
            report(True, f"judge {cfg.grader.model} via LiteLLM", url)
        except RuntimeError as e:
            judge_ok = False
            report(False, "judge endpoint", str(e))
    if judge_ok and a.auth_check:
        prompt = ("Grading test. Expected answer: CTCF. Submitted answer: ctcf. Decide whether the "
                  "submission is correct; keep the rationale to one sentence.")
        try:
            res = await judge(prompt, ANSWER_SCHEMA, cfg.grader, cfg.sandbox.image, retries=0)
            v = res["verdict"]
            report(v.get("correct") is True, f"judge {cfg.grader.model} answers in the schema",
                   f"correct={v.get('correct')}, ${res['cost_usd']:.4f}")
        except RuntimeError as e:
            report(False, f"judge {cfg.grader.model}", str(e)[:400])

    if cfg.sandbox.network == "allowlist":
        with tempfile.TemporaryDirectory(prefix="bmb-doctor-") as tmp:
            box = DockerSandbox(name="bmb-doctor", cfg=cfg.sandbox, workspace=Path(tmp),
                                allowed_domains=["ftp.ncbi.nlm.nih.gov", *cfg.sandbox.infra_domains,
                                                 *agent.infra_domains])
            try:
                await box.start()
                probe = (
                    "import sys, urllib.request\n"
                    "u = sys.argv[1]\n"
                    "try:\n"
                    "    print(urllib.request.urlopen(u, timeout=15).status)\n"
                    "except Exception as e:\n"
                    "    print('blocked', type(e).__name__)\n"
                )
                checks = [
                    ("allowed domain via proxy", "https://ftp.ncbi.nlm.nih.gov/", True, {}),
                    ("other domain via proxy", "https://www.google.com/", False, {}),
                    ("direct connection (no proxy)", "https://www.google.com/", False,
                     {"https_proxy": "", "HTTPS_PROXY": "", "http_proxy": "", "HTTP_PROXY": ""}),
                ]
                for label, url, expect_ok, env in checks:
                    env_args = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
                    _, out, _ = await run_cmd("docker", "exec", "-u", "agent", *env_args, box.name,
                                              "python", "-c", probe, url, check=False, timeout=60)
                    got_ok = out.strip().startswith("2")
                    report(got_ok == expect_ok, f"network: {label}", f"{url} -> {out.strip()}")
                if a.auth_check:
                    if not auth:
                        report(False, f"{agent.name} auth through the sandbox", "no credentials configured")
                    else:
                        # the real agent invocation, with a one-word prompt
                        tmp_prompt = Path(tmp) / "p.txt"
                        tmp_prompt.write_text("Reply with exactly: OK", encoding="utf-8")
                        out_path, err_path = Path(tmp) / "o.jsonl", Path(tmp) / "e.txt"
                        argv = agent.command(timeout_s=150, session_id=str(uuid.uuid4()))
                        res = await box.exec(argv, stdin_path=tmp_prompt, stdout_path=out_path, stderr_path=err_path,
                                             deadline_s=180, env=agent.env(), secret_env=agent.secret_env)
                        seg = agent.parse(out_path, err_path, Segment(kind="doctor", exit_code=res.code))
                        ok = "OK" in seg.final_text and not seg.is_error
                        detail = f"{cfg.agent.model}: {seg.final_text.strip()[:80]!r}" if ok else seg.error_text[-400:]
                        report(ok, f"{agent.name} auth + model API through the proxy", detail)
            finally:
                await box.stop()
    print("all checks passed" if not bad else f"{bad} problem(s) found")
    return 1 if bad else 0


def cmd_doctor(a: argparse.Namespace) -> None:
    sys.exit(asyncio.run(_doctor(a)))


def cmd_run(a: argparse.Namespace) -> None:
    from bmbench.runner import RunAborted, run  # noqa: PLC0415

    overrides = list(a.set or [])
    if a.problems:
        overrides.append(f"dataset.problem_ids=[{a.problems}]")
    if a.attempts:
        overrides.append(f"n_attempts={a.attempts}")
    if a.run_name:
        overrides.append(f"run_name={a.run_name}")
    if a.concurrency:
        overrides.append(f"concurrency={a.concurrency}")
    cfg = load_config(a.config, overrides)
    try:
        run_dir = asyncio.run(run(cfg, force=a.force, grade=False if a.no_grade else None))
    except RunAborted as e:
        sys.exit(f"\nrun aborted: {e}")
    except KeyboardInterrupt:
        sys.exit("\ninterrupted; re-run the same command to resume (finished attempts are kept)")
    print(f"\nreport: {run_dir / 'report.md'}")


def cmd_grade(a: argparse.Namespace) -> None:
    from bmbench.report import write_report  # noqa: PLC0415
    from bmbench.runner import grade_run  # noqa: PLC0415

    run_dir = resolve(a.run)
    asyncio.run(grade_run(run_dir, force=a.regrade, problems=_ids(a.problems), concurrency=a.concurrency,
                          overrides=a.set))
    write_report(run_dir)
    print(f"report: {run_dir / 'report.md'}")


def cmd_report(a: argparse.Namespace) -> None:
    from bmbench.report import write_report  # noqa: PLC0415

    run_dir = resolve(a.run)
    write_report(run_dir)
    print((run_dir / "report.md").read_text(encoding="utf-8"))


def cmd_show(a: argparse.Namespace) -> None:
    adir = resolve(a.run) / "problems" / a.problem / f"attempt-{a.attempt}"
    if not adir.is_dir():
        sys.exit(f"no such attempt: {adir}")
    if a.full:
        print((adir / "transcript.md").read_text(encoding="utf-8"))
        return
    result = json.loads((adir / "result.json").read_text(encoding="utf-8"))
    grade = json.loads((adir / "grade.json").read_text(encoding="utf-8")) if (adir / "grade.json").is_file() else {}
    for k in ("status", "answer", "cost_usd", "num_turns", "agent_seconds", "timed_out",
              "finalized_after_timeout", "nudges", "tool_counts"):
        print(f"{k:24s} {result.get(k)}")
    for k in ("score", "correct", "cheated", "normalized_answer", "answer_rationale", "cheat_evidence"):
        if k in grade:
            print(f"{k:24s} {grade.get(k)}")
    print(f"\ntranscript: {adir / 'transcript.md'}  (use --full to print it)")


def cmd_clean(_a: argparse.Namespace) -> None:
    from bmbench.sandbox import remove_all  # noqa: PLC0415

    removed = asyncio.run(remove_all())
    print("\n".join(removed) if removed else "nothing to clean")


def main(argv: list[str] | None = None) -> None:
    # Progress lines must appear as attempts finish, also when stdout is a file or pipe.
    sys.stdout.reconfigure(line_buffering=True)
    load_dotenv()
    p = argparse.ArgumentParser(prog="bmbench", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_config(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-c", "--config", default="configs/claude_code_preview.yaml")
        sp.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a config field, e.g. --set agent.effort=high (repeatable)")

    sp = sub.add_parser("prepare", help="download + extract a dataset")
    sp.add_argument("--dataset", choices=["preview", "full"], default="preview")
    sp.add_argument("--root", default="data")
    sp.add_argument("--ids", help="comma-separated problem ids (default: all)")
    sp.add_argument("--force", action="store_true", help="re-extract problems already prepared")
    sp.add_argument("--keep-zips", action="store_true")
    sp.set_defaults(fn=cmd_prepare)

    sp = sub.add_parser("problems", help="list problems (never prints the rubric)")
    sp.add_argument("--dataset", choices=["preview", "full"], default="preview")
    sp.add_argument("--root", default="data")
    sp.add_argument("--human-solvable", choices=["yes", "no"])
    sp.set_defaults(fn=cmd_problems)

    sp = sub.add_parser("build", help="build the sandbox and proxy images")
    with_config(sp)
    sp.add_argument("--claude-version", help="Claude Code version to install (default: the host's)")
    sp.add_argument("--codex-version", help="Codex CLI version to install (default: the host's, else the Dockerfile's)")
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("--only", choices=["sandbox", "proxy"])
    sp.set_defaults(fn=cmd_build)

    sp = sub.add_parser("doctor", help="check docker, images, auth, judge, data and the network allowlist")
    with_config(sp)
    sp.add_argument("--auth-check", action="store_true",
                    help="also send one tiny prompt to the agent (through the sandbox) and to the judge "
                         "(costs a few tokens)")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("run", help="run attempts (resumable)")
    with_config(sp)
    sp.add_argument("--problems", help="comma-separated problem ids")
    sp.add_argument("--attempts", type=int, help="attempts per problem")
    sp.add_argument("--run-name")
    sp.add_argument("--concurrency", type=int)
    sp.add_argument("--force", action="store_true", help="redo finished attempts")
    sp.add_argument("--no-grade", action="store_true", help="skip automatic grading")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("grade", help="grade (or re-grade) a run's attempts")
    sp.add_argument("-r", "--run", required=True, help="run directory, e.g. runs/<run_name>")
    sp.add_argument("--regrade", action="store_true")
    sp.add_argument("--problems")
    sp.add_argument("--concurrency", type=int, default=2)
    sp.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override the run's saved config, e.g. --set grader.model=gpt-6-sol")
    sp.set_defaults(fn=cmd_grade)

    sp = sub.add_parser("report", help="recompute metrics and print the report")
    sp.add_argument("-r", "--run", required=True)
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("show", help="summarize one attempt")
    sp.add_argument("-r", "--run", required=True)
    sp.add_argument("problem")
    sp.add_argument("attempt", type=int)
    sp.add_argument("--full", action="store_true", help="print the whole transcript")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("clean", help="remove leftover bmbench containers and networks")
    sp.set_defaults(fn=cmd_clean)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
