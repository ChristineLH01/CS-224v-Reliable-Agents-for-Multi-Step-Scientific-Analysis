"""Replay SHELDON's early-stop check on a finished baseline run, without running SHELDON.

    uv run --group sheldon python scripts/replay_early_stop.py -r runs/<baseline SHELDON run>

For every attempt, the belief state at the end of rounds 1 and 2 is rebuilt from the
attempt's trace, and the runtime's own check (bmb_sheldon.adapted.early_stop: the same
prompt, model call and gate a live run makes) is run on it. Where the gate passes, the
attempt would have stopped there: its candidate answer is graded by the run's judge.

Rebuilding a round's state from the final snapshot is exact when no hypothesis was tested
in two rounds and no later round re-filed an earlier observation (which would rewrite its
verdicts); the script checks both and reports any attempt where either happened.

The pass bar, fixed before the replay: accuracy must not decrease. Attempts that stop are
scored on their candidate at the stop point, the rest on their full-run answer.

Output: runs/replays/early-stop__<run name>/ with one JSON file per check (its model reply
is reused on a rerun and the current gate applied to it again, so a rerun only pays for
what is missing), replay.json and report.md.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import gzip
import json
import sys
from pathlib import Path

import yaml
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bmbench" / "agents" / "sheldon_runtime"))

from sheldon.core.budget import Budget  # noqa: E402
from sheldon.core.trace import NullTrace  # noqa: E402
from sheldon.methods.sheldon.state import PENDING, SHELVED, TESTED, State  # noqa: E402

from bmb_sheldon import driver  # noqa: E402
from bmb_sheldon.adapted import early_stop  # noqa: E402
from bmbench.config import GraderConfig, load_dotenv, resolve  # noqa: E402
from bmbench.dataset import load_problems  # noqa: E402
from bmbench.grader import ANSWER_SCHEMA, judge, render  # noqa: E402


def read_events(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def state_at(final: dict[str, Any], events: list[dict[str, Any]], r: int) -> State:
    """The state as it stood when round r closed: hypotheses born by then with the
    statuses `round.done` recorded, observations filed by then with their verdicts, and
    local-test notes only for hypotheses tested by then."""
    snap = copy.deepcopy({k: final[k] for k in ("hypotheses", "observations", "h_regions", "o_regions",
                                                "aliases", "region_log", "tested_by_round") if k in final})
    done = next(e for e in events if e["kind"] == "round.done" and e["round"] == r)
    tested_at = {h: int(k) for k, ids in (snap.get("tested_by_round") or {}).items() for h in ids}
    hyps = []
    for h in snap["hypotheses"]:
        if h["born"] > r:
            continue
        status = TESTED if h["id"] in done["tested"] else SHELVED if h["id"] in done["shelved"] else PENDING
        if tested_at.get(h["id"], 99) > r:
            h["notes"] = ""
        if status == SHELVED and not (h["status"] == SHELVED and h["touched"] <= r):
            h["reason"] = ""
        if status != SHELVED:
            h["reason"] = ""
        h["status"], h["touched"] = status, min(h["touched"], r)
        hyps.append(h)
    alive = {h["id"] for h in hyps}
    obs = []
    for o in snap["observations"]:
        if o["round"] <= r:
            o["verdicts"] = {k: v for k, v in o["verdicts"].items() if k in alive}
            obs.append(o)
    snap.update(hypotheses=hyps, observations=obs,
                h_regions=[x for x in snap["h_regions"] if x.get("round", 0) <= r],
                o_regions=[x for x in snap["o_regions"] if x.get("round", 0) <= r],
                tested_by_round={k: v for k, v in (snap.get("tested_by_round") or {}).items() if int(k) <= r})
    return State.restore(snap)


def exactness(final: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    """Why rebuilding a round's state from the final snapshot might not be exact."""
    issues = []
    rounds_of = {o["id"]: o["round"] for o in final["observations"]}
    for e in events:
        if e["kind"] in ("candidate_analysis.iteration", "comparative_analysis.done"):
            for oid in e.get("observations") or []:
                if rounds_of.get(oid, e["round"]) < e["round"]:
                    issues.append(f"{oid} re-filed in round {e['round']}")
    tested = [h for ids in (final.get("tested_by_round") or {}).values() for h in ids]
    issues += [f"{h} tested in more than one round" for h in sorted({h for h in tested if tested.count(h) > 1})]
    return issues


class Recorder(NullTrace):
    """The original trace's events (the check reads the round's comparative summary from
    them), and whatever the check logs."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.events = list(events)


async def run_check(llm: Any, question: str, events: list[dict[str, Any]], state: State, r: int) -> dict[str, Any]:
    upto = []
    for e in events:
        upto.append(e)
        if e["kind"] == "round.done" and e["round"] == r:
            break
    run = SimpleNamespace(ctx=SimpleNamespace(llm=llm, budget=Budget(), trace=Recorder(upto)),
                          task=SimpleNamespace(question=question), state=state, round=r)
    return await early_stop.check(run)


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("-r", "--run", required=True, help="a finished baseline SHELDON run directory")
    p.add_argument("--out", help="output directory (default runs/replays/early-stop__<run name>)")
    p.add_argument("--concurrency", type=int, default=6, help="stop checks at once")
    p.add_argument("--judge-concurrency", type=int, default=3)
    p.add_argument("--no-grade", action="store_true")
    a = p.parse_args()
    load_dotenv()

    run_dir = resolve(a.run)
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8"))
    agent = cfg["agent"]
    out_dir = resolve(a.out) if a.out else resolve("runs/replays") / f"early-stop__{run_dir.name}"
    (out_dir / "checks").mkdir(parents=True, exist_ok=True)
    conf = {"sheldon": {"model": agent["model"], "effort": agent.get("effort") or "high", **(agent.get("sheldon") or {})},
            "usd_per_mtok": agent.get("usd_per_mtok")}
    max_rounds = int((agent.get("sheldon") or {}).get("max_rounds") or 3)
    llm = driver.make_llm(driver.build_config(conf, Path("/dev/null"), 3600), conf)
    problems = {pr.id: pr for pr in load_problems(cfg["dataset"]["name"], cfg["dataset"].get("root", "data"))}
    grader = GraderConfig(**{k: v for k, v in (cfg.get("grader") or {}).items() if k in GraderConfig.__annotations__})

    attempts = []
    for adir in sorted((run_dir / "problems").glob("*/attempt-*")):
        trace = next(adir.glob("agent_session/sheldon/runs/*/traces/*.jsonl*"), None)
        if trace is None:
            continue
        events = read_events(trace)
        final = next(e for e in reversed(events) if e["kind"] == "sheldon.final")
        task = json.loads((adir / "agent_session" / "sheldon" / "task.json").read_text(encoding="utf-8"))
        result = json.loads((adir / "result.json").read_text(encoding="utf-8"))
        grade = json.loads((adir / "grade.json").read_text(encoding="utf-8"))
        done_t = {e["round"]: e["t"] for e in events if e["kind"] == "round.done"}
        attempts.append(dict(problem=adir.parent.name, attempt=int(adir.name.split("-")[1]), events=events,
                             final=final, question=task["question"], done_t=done_t,
                             wall_min=result.get("agent_seconds", 0) / 60, timed_out=bool(result.get("timed_out")),
                             baseline_correct=bool(grade.get("score")), baseline_answer=result.get("answer"),
                             exactness=exactness(final, events)))

    sem = asyncio.Semaphore(a.concurrency)

    async def one(at: dict[str, Any], r: int) -> None:
        path = out_dir / "checks" / f"{at['problem']}_a{at['attempt']}_r{r}.json"
        if path.is_file():  # the model's reply is reused; the gate is applied again, as it stands now
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("check") is not None:
                state = state_at(at["final"], at["events"], r)
                rec.update(early_stop.gate(state, early_stop.StopCheckOut(**rec["check"])).to_dict())
            at.setdefault("checks", {})[r] = rec
            return
        async with sem:
            rec = await run_check(llm, at["question"], at["events"], state_at(at["final"], at["events"], r), r)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        at.setdefault("checks", {})[r] = rec
        print(f"{at['problem']} a{at['attempt']} r{r}: {'PASS' if rec.get('passed') else 'continue'}", flush=True)

    # A live run checks after round r only when round r + 1 would open: r < max_rounds,
    # and the run was not out of budget (the baseline opened round r + 1).
    jobs = [one(at, r) for at in attempts for r in range(1, max_rounds) if r in at["done_t"] and r + 1 in at["done_t"]]
    await asyncio.gather(*jobs)
    await llm.aclose()

    jsem = asyncio.Semaphore(a.judge_concurrency)

    async def grade_stop(at: dict[str, Any]) -> None:
        stops = [r for r in sorted(at.get("checks", {})) if at["checks"][r].get("passed")]
        at["stop_round"] = stops[0] if stops else None
        if at["stop_round"] is None:
            return
        rec = at["checks"][at["stop_round"]]
        at["candidate"] = (rec.get("check") or {}).get("candidate_answer", "")
        path = out_dir / "checks" / f"{at['problem']}_a{at['attempt']}_grade.json"
        if path.is_file():
            g = json.loads(path.read_text(encoding="utf-8"))
        elif a.no_grade:
            return
        else:
            pr = problems[at["problem"]]
            prompt = render(resolve("prompts/grade_answer.md").read_text(encoding="utf-8"), question=pr.question,
                            rubric=pr.answer_rubric, answer=at["candidate"], final_message="")
            async with jsem:
                res = await judge(prompt, ANSWER_SCHEMA, grader)
            g = {"answer": at["candidate"], **res["verdict"], "judge": grader.model, "cost_usd": res["cost_usd"]}
            path.write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
        at["candidate_correct"] = bool(g.get("correct"))

    await asyncio.gather(*(grade_stop(at) for at in attempts))

    rows, saved_total = [], 0.0
    for at in attempts:
        r = at.get("stop_round")
        saved = (max(at["done_t"].values()) - at["done_t"][r]) / 60 if r else 0.0
        saved_total += saved
        score = at.get("candidate_correct") if r else at["baseline_correct"]
        rows.append({k: at.get(k) for k in ("problem", "attempt", "baseline_correct", "baseline_answer",
                                            "stop_round", "candidate", "candidate_correct", "wall_min",
                                            "timed_out", "exactness")}
                    | {"replay_correct": score, "minutes_saved": round(saved, 1),
                       "checks": {str(k): {kk: v.get(kk) for kk in ("passed", "failed", "evidence", "ignored", "check")}
                                  for k, v in sorted(at.get("checks", {}).items())}})
    n = len(rows)
    base = sum(bool(x["baseline_correct"]) for x in rows)
    replay = sum(bool(x["replay_correct"]) for x in rows)
    flips = [x for x in rows if x["stop_round"] and x["replay_correct"] != x["baseline_correct"]]
    wall = sum(x["wall_min"] for x in rows)
    summary = {"attempts": n, "baseline_correct": base, "replay_correct": replay,
               "passes_bar": replay >= base and all(x["candidate_correct"] is not None for x in rows if x["stop_round"]),
               "stopped": {str(r): sum(1 for x in rows if x["stop_round"] == r) for r in range(1, max_rounds)},
               "flips": [f"{x['problem']} a{x['attempt']}: "
                         f"{'right->wrong' if x['baseline_correct'] else 'wrong->right'}" for x in flips],
               "minutes_saved": round(saved_total, 1), "mean_wall_min": round(wall / n, 1),
               "mean_wall_min_after": round((wall - saved_total) / n, 1),
               "timeouts": sum(x["timed_out"] for x in rows),
               "timeouts_after": sum(x["timed_out"] and not x["stop_round"] for x in rows),
               "inexact_rebuilds": [f"{x['problem']} a{x['attempt']}" for x in rows if x["exactness"]]}
    (out_dir / "replay.json").write_text(json.dumps({"summary": summary, "attempts": rows}, indent=2,
                                                    ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.md").write_text(report(run_dir.name, summary, rows), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def report(run_name: str, s: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [f"# Early-stop replay on `{run_name}`", "",
             f"- Accuracy: baseline {s['baseline_correct']}/{s['attempts']}, with early stop "
             f"{s['replay_correct']}/{s['attempts']} ({'passes' if s['passes_bar'] else 'FAILS'} the bar: "
             "accuracy must not decrease)",
             f"- Stopped after round 1: {s['stopped'].get('1', 0)}; after round 2: {s['stopped'].get('2', 0)}",
             f"- Flips: {', '.join(s['flips']) or 'none'}",
             f"- Time: {s['minutes_saved']} min saved; mean {s['mean_wall_min']} -> {s['mean_wall_min_after']} min "
             f"per attempt; timeouts {s['timeouts']} -> {s['timeouts_after']}",
             f"- Rebuilds that may not be exact: {', '.join(s['inexact_rebuilds']) or 'none'}", "",
             "| attempt | baseline | stop | candidate | replay | saved | why it continued (first check) |",
             "|---|---|---|---|---|---|---|"]
    for x in rows:
        first = next(iter(x["checks"].values()), {})
        why = "; ".join(first.get("failed") or [])[:220] if not x["stop_round"] else ""
        lines.append(f"| {x['problem']} a{x['attempt']} | {'✓' if x['baseline_correct'] else '✗'} "
                     f"{str(x['baseline_answer'] or '')[:30]} | {x['stop_round'] or '-'} | "
                     f"{str(x['candidate'] or '')[:40]} | {'✓' if x['replay_correct'] else '✗'} | "
                     f"{x['minutes_saved']} | {why.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
