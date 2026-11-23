"""Offline candidate recall: is the right answer among the candidates before round 1?

Two sources of candidates, graded the same way:

  scan       runs made with `candidate_scan` + `candidate_scan_only` (configs/
             sheldon_candidate_scan_recall_*.yaml): the scan's ranked candidate list
  baseline   any earlier SHELDON run: the hypotheses round-1 explore proposed (from the
             trace's round-1 `explore.done`), as the comparison for what SHELDON has today

Each candidate is graded on its own, as if it were the attempt's submitted answer, with the
official correctness prompt (prompts/grade_answer.md), judge and schema used by
bmbench.grader: "candidate correct" means exactly what "answer correct" means in a scored
run. Rubrics are read on the host from data/<dataset>/meta and never enter a sandbox.

Two looser checks, reported separately and never mixed into the recall:

  union      only for attempts with no correct single candidate: does the list taken
             together contain every part of a correct answer (a multi-part question whose
             parts were found but never combined)?
  concrete   baseline only: does a round-1 hypothesis commit to one specific answer, or is
             it a method / data-quality question?

Verdicts are cached in <out>/judgments.jsonl (keyed by question, text, check and judge), so a
rerun only pays for what is new, and the same candidate text is graded once per problem.

    .venv/bin/python scripts/eval_candidate_recall.py \
        --scan runs/candidate_scan_recall_preview_3attempts_20261115 \
        --scan runs/candidate_scan_recall_full_3attempts_20261115 \
        --baseline "runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts" \
        --out runs/candidate_recall_eval_20261115

`--no-judge` extracts and counts only (free), to check what would be graded.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bmbench.config import GraderConfig, load_dotenv, resolve  # noqa: E402
from bmbench.dataset import Problem, load_problems  # noqa: E402
from bmbench.grader import ANSWER_SCHEMA, judge, render  # noqa: E402

TOP_K = (1, 3, 5)

UNION_SCHEMA = {
    "type": "object",
    "properties": {"covered": {"type": "boolean"}, "parts": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["covered", "parts", "rationale"],
    "additionalProperties": False,
}
UNION_PROMPT = """You are checking a list of candidate answers to a bioinformatics benchmark question against the benchmark's official answer rubric.

<question>
{question}
</question>

<rubric>
{rubric}
</rubric>

<candidates>
{candidates}
</candidates>

No single candidate was judged correct on its own. Decide whether the candidates taken together contain every part of a correct answer: for a multi-part question, each required part appears in at least one candidate. Ignore the rule that an answer must commit to one candidate, and ignore the rubric's clause about cheating. Judge substance, not formatting, as a grader would. If the question has only one part, `covered` is false unless some candidate states it, which would already have been judged correct.

Return `covered`, `parts` (which candidate supplies which required part, or what is missing), and a one-sentence `rationale`."""

CONCRETE_SCHEMA = {
    "type": "object",
    "properties": {"names_answer": {"type": "boolean"}, "answer": {"type": "string"},
                   "kind": {"type": "string", "enum": ["answer", "method", "data_quality", "other"]}},
    "required": ["names_answer", "answer", "kind"],
    "additionalProperties": False,
}
CONCRETE_PROMPT = """Below is a question from a bioinformatics benchmark and one hypothesis an agent proposed before testing anything.

<question>
{question}
</question>

<hypothesis>
{hypothesis}
</hypothesis>

Does the hypothesis commit to one specific answer of the kind the question asks for (a named gene, species, compound, number, and so on)? A hypothesis that only describes how the answer would be found ("the miRNA with the most validated targets"), or that asks about method or data quality (normalization, a database version, contamination, batch effects), does not.

Return `names_answer`; `answer`, the specific answer it commits to, or "" if none; and `kind`: answer, method, data_quality or other. Do not judge whether the answer is correct."""


@dataclass
class Item:
    rank: int
    text: str
    basis: str = ""
    correct: bool | None = None
    normalized: str = ""
    names_answer: bool | None = None
    kind: str = ""
    #: scan only: cites at least one retrieval this attempt actually made
    verified_source: bool | None = None
    #: scan only: added by the follow-up call that extends a short list
    extended: bool = False


@dataclass
class Attempt:
    source: str
    run: str
    problem: str
    attempt: str
    items: list[Item] = field(default_factory=list)
    final_correct: bool | None = None
    scan: dict[str, Any] = field(default_factory=dict)
    covered_by_union: bool | None = None
    union_parts: str = ""
    note: str = ""
    #: whether there is anything to grade: a scan record (even an empty list, which is a
    #: miss) or round-1 hypotheses; an interrupted attempt has none and is left out
    gradable: bool = False


# -- extraction ------------------------------------------------------------------------


def attempt_dirs(run: Path) -> list[Path]:
    return sorted(p for p in run.glob("problems/*/attempt-*") if p.is_dir())


def prediction(attempt: Path) -> dict[str, Any]:
    rows = sorted(attempt.glob("agent_session/sheldon/runs/*/predictions.jsonl"))
    if not rows:
        return {}
    lines = [line for line in rows[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def trace_events(attempt: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(attempt.glob("agent_session/sheldon/runs/*/traces/*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            events += [json.loads(line) for line in f if line.strip()]
    return events


def retrieval_id(ref: str) -> str:
    """`r3`, `R3`, `[r3]`, `retrieval 3`, `3` -> `r3` (as candidate_scan.retrieval_id)."""
    m = re.fullmatch(r"\[?\s*(?:r|retrieval)?\s*(?:id)?\s*#?\s*(\d+)\s*\]?", str(ref).strip(), re.I)
    return f"r{m.group(1)}" if m else str(ref).strip()


def retrieval_ids(attempt: Path) -> set[str]:
    """The ids of every retrieval the attempt made. A scan-only run makes no other tool
    calls (plan has none), so these are the scan's own."""
    return {p.stem for p in attempt.glob("agent_session/sheldon/runs/*/retrievals/*/r*.json")}


def final_correct(attempt: Path) -> bool | None:
    path = attempt / "grade.json"
    if not path.is_file():
        return None
    return bool(json.loads(path.read_text(encoding="utf-8")).get("correct"))


def scan_attempt(run: Path, attempt: Path) -> Attempt:
    a = Attempt("scan", run.name, attempt.parent.name, attempt.name.removeprefix("attempt-"))
    result = json.loads((attempt / "result.json").read_text(encoding="utf-8")) if (attempt / "result.json").is_file() else {}
    record = (result.get("sheldon") or {}).get("candidate_scan") or (prediction(attempt).get("extra") or {}).get("candidate_scan")
    if not record:
        a.note = "no candidate_scan record" + (f" ({result.get('status')}: {result.get('error') or result.get('harness_error')})"
                                               if result.get("status") != "completed" else "")
        return a
    # Re-checked here rather than read from `sources_found`: runs before the fix matched
    # cited ids only when written exactly `r3`, and missed `retrieval 3` or `3`.
    own = retrieval_ids(attempt)
    a.gradable = True
    a.items = [Item(i, c["answer"], c.get("basis", ""),
                    verified_source=any(retrieval_id(s) in own for s in c.get("sources") or []),
                    extended=bool(c.get("extended")))
               for i, c in enumerate(record.get("candidates") or [], start=1)]
    usage = record.get("usage") or {}
    a.scan = {"n_tests": len(record.get("tests") or []), "retrievals": record.get("retrievals"),
              "parsed": record.get("parsed"), "stopped_by": record.get("stopped_by"),
              "cost_usd": usage.get("cost_usd"), "attempt_cost_usd": result.get("cost_usd"),
              "unverified_candidates": sum(1 for i in a.items if not i.verified_source)}
    return a


def baseline_attempt(run: Path, attempt: Path) -> Attempt:
    a = Attempt("baseline", run.name, attempt.parent.name, attempt.name.removeprefix("attempt-"),
                final_correct=final_correct(attempt))
    hyps = {h["id"]: h for h in (prediction(attempt).get("belief") or {}).get("hypotheses") or []}
    added = next((e.get("added") or [] for e in trace_events(attempt)
                  if e.get("kind") == "explore.done" and e.get("round") == 1), None)
    if added is None:  # no trace: round-1 hypotheses that are not refinements
        added = [h["id"] for h in hyps.values() if h.get("born") == 1 and not h.get("parent")]
        a.note = "round 1 read from the belief state (no trace)"
    order = sorted(added, key=lambda hid: int(re.sub(r"\D", "", hid) or 0))
    a.items = [Item(i, hyps[hid]["statement"]) for i, hid in enumerate((h for h in order if h in hyps), start=1)]
    a.gradable = bool(a.items)
    if not a.items:
        a.note = "no round-1 hypotheses found"
    return a


# -- judging ---------------------------------------------------------------------------


class Judge:
    def __init__(self, cfg: GraderConfig, cache_path: Path, jobs: int) -> None:
        self.cfg, self.path = cfg, cache_path
        self.sem = asyncio.Semaphore(jobs)
        self.cache: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, asyncio.Task] = {}
        self.cost = 0.0
        self.calls = 0
        if cache_path.is_file():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["key"]] = row["verdict"]

    def key(self, check: str, problem: Problem, text: str) -> str:
        norm = re.sub(r"\s+", " ", text).strip().lower()
        raw = "\x00".join([check, self.cfg.model, str(self.cfg.effort), problem.id, problem.question, norm])
        return hashlib.sha256(raw.encode()).hexdigest()

    async def ask(self, check: str, problem: Problem, text: str, prompt: str, schema: dict) -> dict[str, Any]:
        k = self.key(check, problem, text)
        if k in self.cache:
            return self.cache[k]
        if k not in self.pending:  # one call for a text asked twice at once
            self.pending[k] = asyncio.ensure_future(self._call(k, check, problem, text, prompt, schema))
        return await self.pending[k]

    async def _call(self, k: str, check: str, problem: Problem, text: str, prompt: str, schema: dict) -> dict[str, Any]:
        async with self.sem:
            res = await judge(prompt, schema, self.cfg)
        self.cost += float(res.get("cost_usd") or 0.0)
        self.calls += 1
        verdict = res["verdict"]
        self.cache[k] = verdict
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": k, "check": check, "problem": problem.id, "text": text[:2000],
                                "verdict": verdict, "cost_usd": res.get("cost_usd")}, ensure_ascii=False) + "\n")
        return verdict

    async def correct(self, problem: Problem, text: str) -> dict[str, Any]:
        template = resolve("prompts/grade_answer.md").read_text(encoding="utf-8")
        prompt = render(template, question=problem.question, rubric=problem.answer_rubric, answer=text,
                        final_message="(none: this is one candidate, graded on its own)")
        return await self.ask("correct", problem, text, prompt, ANSWER_SCHEMA)

    async def union(self, problem: Problem, texts: list[str]) -> dict[str, Any]:
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, start=1))
        prompt = render(UNION_PROMPT, question=problem.question, rubric=problem.answer_rubric, candidates=listing)
        return await self.ask("union", problem, listing, prompt, UNION_SCHEMA)

    async def concrete(self, problem: Problem, text: str) -> dict[str, Any]:
        prompt = render(CONCRETE_PROMPT, question=problem.question, hypothesis=text)
        return await self.ask("concrete", problem, text, prompt, CONCRETE_SCHEMA)


async def grade(a: Attempt, problem: Problem, j: Judge) -> None:
    async def one(item: Item) -> None:
        v = await j.correct(problem, item.text)
        item.correct, item.normalized = bool(v.get("correct")), str(v.get("normalized_answer", ""))
        if a.source == "baseline":
            c = await j.concrete(problem, item.text)
            item.names_answer, item.kind = bool(c.get("names_answer")), str(c.get("kind", ""))

    await asyncio.gather(*(one(i) for i in a.items))
    if a.items and not any(i.correct for i in a.items) and len(a.items) > 1:
        u = await j.union(problem, [i.text for i in a.items])
        a.covered_by_union, a.union_parts = bool(u.get("covered")), str(u.get("parts", ""))


# -- reporting -------------------------------------------------------------------------


def first_hit(a: Attempt) -> int | None:
    return next((i.rank for i in a.items if i.correct), None)


def attempt_row(a: Attempt) -> dict[str, Any]:
    hit = first_hit(a)
    # an empty scan list is a graded miss; `all` over no items is True
    graded = a.gradable and all(i.correct is not None for i in a.items) and (bool(a.items) or a.source == "scan")
    row: dict[str, Any] = {
        "source": a.source, "run": a.run, "problem": a.problem, "attempt": a.attempt, "n_items": len(a.items),
        "hit": (hit is not None) if graded else "", "hit_rank": hit or "",
        **{f"hit_top{k}": (hit is not None and hit <= k) if graded else "" for k in TOP_K},
        "hit_without_extension": any(i.correct and not i.extended for i in a.items) if graded else "",
        "n_extended": sum(1 for i in a.items if i.extended),
        "hit_answer": next((i.text for i in a.items if i.correct), ""),
        "hit_basis": next((i.basis for i in a.items if i.correct), ""),
        "covered_by_union": "" if a.covered_by_union is None else a.covered_by_union,
        "final_correct": "" if a.final_correct is None else a.final_correct,
        "n_naming_answer": sum(1 for i in a.items if i.names_answer) if a.source == "baseline" and graded else "",
        "note": a.note,
    }
    row.update({f"scan_{k}": v for k, v in a.scan.items()})
    return row


def frac(rows: list[dict[str, Any]], key: str) -> str:
    graded = [r for r in rows if r.get(key) != ""]
    return f"{sum(1 for r in graded if r[key] is True)}/{len(graded)}" if graded else "–"


def summary_md(rows: list[dict[str, Any]], args: argparse.Namespace, j: Judge | None) -> str:
    problems = sorted({r["problem"] for r in rows})
    scan = [r for r in rows if r["source"] == "scan"]
    base = [r for r in rows if r["source"] == "baseline"]
    out = ["# Candidate recall before round 1", "",
           "Each candidate graded on its own with the official correctness prompt and judge "
           f"({args.grader_model}, effort {args.effort}). Counts are attempts with at least one correct "
           "candidate / graded attempts. Three attempts per question: descriptive only.", "",
           "Scan runs: " + (", ".join(f"`{p}`" for p in args.scan) or "none"),
           "Baseline runs (round-1 explore hypotheses): " + (", ".join(f"`{p}`" for p in args.baseline) or "none"), ""]
    head = ["problem", "scan any", *(f"scan top{k}" for k in TOP_K), "scan union", "scan mean n",
            "base round-1", "base union", "base naming an answer", "base final correct"]
    out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def line(name: str, s: list[dict[str, Any]], b: list[dict[str, Any]]) -> str:
        n = [r["n_items"] for r in s if r["hit"] != ""]
        naming = [r for r in b if r["n_naming_answer"] != ""]
        named = f"{sum(r['n_naming_answer'] for r in naming)}/{sum(r['n_items'] for r in naming)}" if naming else "–"
        cells = [name, frac(s, "hit"), *(frac(s, f"hit_top{k}") for k in TOP_K), frac(s, "covered_by_union"),
                 f"{sum(n) / len(n):.1f}" if n else "–", frac(b, "hit"), frac(b, "covered_by_union"), named,
                 frac(b, "final_correct")]
        return "| " + " | ".join(cells) + " |"

    for p in problems:
        out.append(line(p, [r for r in scan if r["problem"] == p], [r for r in base if r["problem"] == p]))
    out.append(line("**all**", scan, base))
    out += ["", "- `union` counts only attempts with no single correct candidate; it is a looser check, "
                "not part of recall.",
            "- `base naming an answer`: round-1 hypotheses the judge says commit to one specific answer "
            "(the rest are methods or data-quality questions) / all round-1 hypotheses.",
            "- `base final correct`: that baseline attempt's own graded final answer (grade.json), for "
            "contrast with whether the answer was already on the table in round 1."]
    costs = [r.get("scan_cost_usd") for r in scan if isinstance(r.get("scan_cost_usd"), (int, float))]
    if any(r.get("n_extended") for r in scan):
        out.append(f"- Scan recall counting only the scan's own candidates (not the follow-up's additions): "
                   f"{frac(scan, 'hit_without_extension')}.")
    if costs:
        out.append(f"- Scan stage cost: mean ${sum(costs) / len(costs):.3f} per attempt over {len(costs)} attempts.")
    suspect = sum(int(r.get("scan_unverified_candidates") or 0) for r in scan)
    if scan:
        total = sum(int(r.get("n_items") or 0) for r in scan)
        counts = sorted(int(r["n_items"]) for r in scan if r.get("note", "") == "")
        out.append(f"- Candidates citing none of the attempt's own retrievals: {suspect}/{total} (check these by "
                   "hand; `basis` labels are not consistent enough to rely on).")
        if counts:
            dist = {n: counts.count(n) for n in sorted(set(counts))}
            out.append("- Candidates per scan attempt: " + ", ".join(f"{n}: {k} attempt(s)" for n, k in dist.items()))
    missing = [f"{r['source']}:{r['problem']}/{r['attempt']} ({r['note']})" for r in rows if r["note"]]
    if missing:
        out += ["", "Notes: " + "; ".join(missing)]
    if j is not None:
        out += ["", f"Judge calls this invocation: {j.calls}, ${j.cost:.2f} (cached verdicts are free)."]
    return "\n".join(out) + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# -- main ------------------------------------------------------------------------------


def load_all_problems() -> dict[str, Problem]:
    found: dict[str, Problem] = {}
    for name in ("preview", "full"):
        try:
            found.update({p.id: p for p in load_problems(name)})
        except FileNotFoundError:
            pass
    return found


async def main_async(args: argparse.Namespace) -> int:
    problems = load_all_problems()
    attempts: list[Attempt] = []
    for path in args.scan:
        attempts += [scan_attempt(Path(path), d) for d in attempt_dirs(Path(path))]
    for path in args.baseline:
        attempts += [baseline_attempt(Path(path), d) for d in attempt_dirs(Path(path))]
    if args.problems:
        attempts = [a for a in attempts if a.problem in set(args.problems)]
    unknown = sorted({a.problem for a in attempts} - set(problems))
    if unknown:
        raise SystemExit(f"no rubric for: {', '.join(unknown)} (run `bmbench prepare` for its dataset)")
    if not attempts:
        raise SystemExit("no attempts found under the given runs")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    j = None
    if not args.no_judge:
        load_dotenv()
        cfg = GraderConfig(model=args.grader_model, effort=args.effort or None)
        j = Judge(cfg, out / "judgments.jsonl", args.jobs)
        await asyncio.gather(*(grade(a, problems[a.problem], j) for a in attempts))
    rows = [attempt_row(a) for a in attempts]
    write_csv(out / "attempts.csv", rows)
    items = [{"source": a.source, "run": a.run, "problem": a.problem, "attempt": a.attempt, **asdict(i)}
             for a in attempts for i in a.items]
    write_csv(out / "items.csv", items or [{"source": ""}])
    (out / "summary.md").write_text(summary_md(rows, args, j), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"args": vars(args), "attempts": rows}, indent=2, default=str),
                                      encoding="utf-8")
    n_items = sum(len(a.items) for a in attempts)
    print(f"{len(attempts)} attempts, {n_items} candidates/hypotheses -> {out}/summary.md")
    if j is not None:
        print(f"judge: {j.calls} new calls, ${j.cost:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scan", action="append", default=[], help="a candidate-scan run folder (repeatable)")
    p.add_argument("--baseline", action="append", default=[], help="an earlier SHELDON run folder (repeatable)")
    p.add_argument("--out", required=True, help="output folder (judgments.jsonl there is the cache)")
    p.add_argument("--problems", nargs="*", help="only these problem ids")
    p.add_argument("--grader-model", default=GraderConfig().model)
    p.add_argument("--effort", default=GraderConfig().effort or "")
    p.add_argument("--jobs", type=int, default=3, help="judge calls at once")
    p.add_argument("--no-judge", action="store_true", help="extract and count only")
    args = p.parse_args(argv)
    if not args.scan and not args.baseline:
        p.error("give at least one --scan or --baseline run")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
