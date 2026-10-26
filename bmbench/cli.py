"""bmbench: prepare | tasks | build | doctor | run | grade | report | show | clean"""

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
from pathlib import Path

from bmbench.config import REPO_ROOT, load_config, load_dotenv, resolve
from bmbench.dataset import PREPARED_MARKER, dataset_dir, dir_size, load_problems, prepare, select_problems


def _ids(text: str | None) -> list[str] | None:
    return [t.strip() for t in text.split(",") if t.strip()] if text else None


def cmd_prepare(a: argparse.Namespace) -> None:
    ids = prepare(a.dataset, a.root, ids=_ids(a.ids), force=a.force, keep_zips=a.keep_zips)
    print(f"ready: {len(ids)} task(s) under {dataset_dir(a.dataset, a.root) / 'tasks'}")


def cmd_tasks(a: argparse.Namespace) -> None:
    problems = load_problems(a.dataset, a.root)
    base = dataset_dir(a.dataset, a.root) / "tasks"
    print(f"{'id':22s} {'solvable':8s} {'data':>9s}  question")
    for p in select_problems(problems, None, a.human_solvable):
        d = base / p.id
        size = f"{dir_size(d) / 1e6:.1f} MB" if (d / PREPARED_MARKER).exists() else "—"
        q = " ".join(p.question.split())
        print(f"{p.id:22s} {p.human_solvable:8s} {size:>9s}  {q[:90]}{'…' if len(q) > 90 else ''}")


def _host_claude_version() -> str | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def cmd_build(a: argparse.Namespace) -> None:
    cfg = load_config(a.config, a.set)
    version = a.claude_version or _host_claude_version() or "stable"
    uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (1000, 1000)
    common = ["--no-cache"] if a.no_cache else []
    if cfg.sandbox.platform:
        common += ["--platform", cfg.sandbox.platform]
    steps = [
        ["docker", "build", *common, "--build-arg", f"AGENT_UID={uid}", "--build-arg", f"AGENT_GID={gid}",
         "--build-arg", f"CLAUDE_CODE_VERSION={version}", "-t", cfg.sandbox.image, str(REPO_ROOT / "docker/sandbox")],
        ["docker", "build", *common, "-t", cfg.sandbox.proxy_image, str(REPO_ROOT / "docker/proxy")],
    ]
    if a.only:
        steps = [steps[0]] if a.only == "sandbox" else [steps[1]]
    for argv in steps:
        print("+", " ".join(argv))
        if subprocess.run(argv).returncode != 0:
            sys.exit(f"build failed: {' '.join(argv[:4])}")
    print(f"built {cfg.sandbox.image} (Claude Code {version}) and {cfg.sandbox.proxy_image}")


async def _doctor(a: argparse.Namespace) -> int:
    from bmbench.grader import ANSWER_SCHEMA, judge, judge_endpoint  # noqa: PLC0415
    from bmbench.sandbox import DockerSandbox, docker_ok, image_info, run_cmd  # noqa: PLC0415

    cfg = load_config(a.config, a.set)
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
    _, ver, _ = await run_cmd("docker", "run", "--rm", "-u", "agent", cfg.sandbox.image, "claude", "--version", check=False)
    report(bool(ver.strip()), "Claude Code in image", ver.strip())
    auth = [k for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY") if os.environ.get(k)]
    report(bool(auth), "agent auth in environment/.env", ", ".join(auth) or "set CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) or ANTHROPIC_API_KEY")
    try:
        problems = load_problems(cfg.dataset.name, cfg.dataset.root)
        base = dataset_dir(cfg.dataset.name, cfg.dataset.root) / "tasks"
        ready = [p.id for p in problems if (base / p.id / PREPARED_MARKER).exists()]
        rest = "" if len(ready) == len(problems) else " (the rest downloads during the run, one question at a time)"
        report(True, f"dataset {cfg.dataset.name}", f"{len(problems)} problems; data on disk for {len(ready)}{rest}")
    except FileNotFoundError:
        report(True, f"dataset {cfg.dataset.name}", "not downloaded yet; `run` fetches it")
    if cfg.dataset.name == "full":
        report(bool(os.environ.get("HF_TOKEN")), "HF_TOKEN for the gated full set", "" if os.environ.get("HF_TOKEN") else "missing")
    try:
        url, _key = judge_endpoint()
        report(True, f"judge endpoint ({cfg.grader.model})", url)
    except RuntimeError as e:
        report(False, "judge endpoint", str(e))
    else:
        if a.auth_check:
            prompt = ("Grading test. Expected answer: CTCF. Submitted answer: ctcf. Decide whether the "
                      "submission is correct; keep the rationale to one sentence.")
            try:
                res = await judge(prompt, ANSWER_SCHEMA, cfg.grader, retries=0)
                v = res["verdict"]
                report(v.get("correct") is True, f"judge {cfg.grader.model} answers in the schema",
                       f"correct={v.get('correct')}, ${res['cost_usd']:.4f}")
            except RuntimeError as e:
                report(False, f"judge {cfg.grader.model}", str(e)[:400])

    if cfg.sandbox.network == "allowlist":
        with tempfile.TemporaryDirectory(prefix="bmb-doctor-") as tmp:
            box = DockerSandbox(name="bmb-doctor", cfg=cfg.sandbox, workspace=Path(tmp),
                                allowed_domains=["ftp.ncbi.nlm.nih.gov", *cfg.sandbox.infra_domains])
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
                        report(False, "Claude auth through the sandbox", "no auth configured")
                    else:
                        tmp_prompt = Path(tmp) / "p.txt"
                        tmp_prompt.write_text("Reply with exactly: OK", encoding="utf-8")
                        out_path, err_path = Path(tmp) / "o.json", Path(tmp) / "e.txt"
                        argv = ["claude", "-p", "--model", cfg.agent.model, "--effort", "low", "--tools", "",
                                "--output-format", "json", "--no-session-persistence"]
                        await box.exec(argv, stdin_path=tmp_prompt, stdout_path=out_path, stderr_path=err_path,
                                       deadline_s=180)
                        text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
                        try:
                            data = json.loads(text.strip().splitlines()[-1])
                            ok = not data.get("is_error") and "OK" in str(data.get("result"))
                            detail = f"{cfg.agent.model}: {str(data.get('result'))[:80]!r}"
                        except (json.JSONDecodeError, IndexError):
                            ok, detail = False, (text + err_path.read_text(encoding="utf-8", errors="replace"))[-400:]
                        report(ok, "Claude auth + API through the proxy", detail)
            finally:
                await box.stop()
    print("all checks passed" if not bad else f"{bad} problem(s) found")
    return 1 if bad else 0


def cmd_doctor(a: argparse.Namespace) -> None:
    sys.exit(asyncio.run(_doctor(a)))


def cmd_run(a: argparse.Namespace) -> None:
    from bmbench.runner import RunAborted, run  # noqa: PLC0415

    overrides = list(a.set or [])
    if a.tasks:
        overrides.append(f"dataset.task_ids=[{a.tasks}]")
    if a.trials:
        overrides.append(f"n_trials={a.trials}")
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
        sys.exit("\ninterrupted; re-run the same command to resume (finished trials are kept)")
    print(f"\nreport: {run_dir / 'report.md'}")


def cmd_grade(a: argparse.Namespace) -> None:
    from bmbench.report import write_report  # noqa: PLC0415
    from bmbench.runner import grade_run  # noqa: PLC0415

    run_dir = resolve(a.run)
    asyncio.run(grade_run(run_dir, force=a.regrade, tasks=_ids(a.tasks), concurrency=a.concurrency,
                          overrides=a.set))
    write_report(run_dir)
    print(f"report: {run_dir / 'report.md'}")


def cmd_report(a: argparse.Namespace) -> None:
    from bmbench.report import write_report  # noqa: PLC0415

    run_dir = resolve(a.run)
    write_report(run_dir)
    print((run_dir / "report.md").read_text(encoding="utf-8"))


def cmd_show(a: argparse.Namespace) -> None:
    tdir = resolve(a.run) / "trials" / a.task / f"r{a.trial}"
    if not tdir.is_dir():
        sys.exit(f"no such trial: {tdir}")
    if a.full:
        print((tdir / "transcript.md").read_text(encoding="utf-8"))
        return
    result = json.loads((tdir / "result.json").read_text(encoding="utf-8"))
    grade = json.loads((tdir / "grade.json").read_text(encoding="utf-8")) if (tdir / "grade.json").is_file() else {}
    for k in ("status", "answer", "cost_usd", "num_turns", "agent_seconds", "timed_out",
              "finalized_after_timeout", "nudges", "tool_counts"):
        print(f"{k:24s} {result.get(k)}")
    for k in ("score", "correct", "cheated", "normalized_answer", "answer_rationale", "cheat_evidence"):
        if k in grade:
            print(f"{k:24s} {grade.get(k)}")
    print(f"\ntranscript: {tdir / 'transcript.md'}  (use --full to print it)")


def cmd_clean(_a: argparse.Namespace) -> None:
    from bmbench.sandbox import remove_all  # noqa: PLC0415

    removed = asyncio.run(remove_all())
    print("\n".join(removed) if removed else "nothing to clean")


def main(argv: list[str] | None = None) -> None:
    # Progress lines must appear as trials finish, also when stdout is a file or pipe.
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
    sp.add_argument("--ids", help="comma-separated task ids (default: all)")
    sp.add_argument("--force", action="store_true", help="re-extract tasks already prepared")
    sp.add_argument("--keep-zips", action="store_true")
    sp.set_defaults(fn=cmd_prepare)

    sp = sub.add_parser("tasks", help="list problems (never prints the rubric)")
    sp.add_argument("--dataset", choices=["preview", "full"], default="preview")
    sp.add_argument("--root", default="data")
    sp.add_argument("--human-solvable", choices=["yes", "no"])
    sp.set_defaults(fn=cmd_tasks)

    sp = sub.add_parser("build", help="build the sandbox and proxy images")
    with_config(sp)
    sp.add_argument("--claude-version", help="Claude Code version to install (default: the host's)")
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("--only", choices=["sandbox", "proxy"])
    sp.set_defaults(fn=cmd_build)

    sp = sub.add_parser("doctor", help="check docker, images, auth, judge, data and the network allowlist")
    with_config(sp)
    sp.add_argument("--auth-check", action="store_true",
                    help="also send one tiny prompt to the agent (through the sandbox) and to the judge "
                         "(costs a few tokens)")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("run", help="run trials (resumable)")
    with_config(sp)
    sp.add_argument("--tasks", help="comma-separated task ids")
    sp.add_argument("--trials", type=int, help="trials per task")
    sp.add_argument("--run-name")
    sp.add_argument("--concurrency", type=int)
    sp.add_argument("--force", action="store_true", help="redo finished trials")
    sp.add_argument("--no-grade", action="store_true", help="skip automatic grading")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("grade", help="grade (or re-grade) a run's trials")
    sp.add_argument("-r", "--run", required=True, help="run directory, e.g. runs/<run_name>")
    sp.add_argument("--regrade", action="store_true")
    sp.add_argument("--tasks")
    sp.add_argument("--concurrency", type=int, default=2)
    sp.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override the run's saved config, e.g. --set grader.model=gpt-6-sol")
    sp.set_defaults(fn=cmd_grade)

    sp = sub.add_parser("report", help="recompute metrics and print the report")
    sp.add_argument("-r", "--run", required=True)
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("show", help="summarize one trial")
    sp.add_argument("-r", "--run", required=True)
    sp.add_argument("task")
    sp.add_argument("trial", type=int)
    sp.add_argument("--full", action="store_true", help="print the whole transcript")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("clean", help="remove leftover bmbench containers and networks")
    sp.set_defaults(fn=cmd_clean)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
