"""`candidate_scan`: before round 1, list every specific answer the data supports.

SHELDON's round-1 explore often proposes ways of finding the answer ("the miRNA with the
most validated targets") rather than answers, and method or data-quality questions take
hypothesis slots. This change adds one tool-using stage after plan: it looks at the data and
submits a ranked list of candidate answers, each grounded in what it saw, plus the method and
data-quality questions that could change them, filed as tests rather than candidates.

If the scan lists fewer than `candidate_scan_min` candidates, one tool-less follow-up call
asks it to extend the list; candidates added that way are marked `extended`, so recall can be
read with and without them.

Only the scan exists so far. With `candidate_scan_only: true` the run ends after the scan
and the prediction carries the candidate list (`extra["candidate_scan"]`): this is the
offline recall measurement (scripts/eval_candidate_recall.py grades each candidate on the
host). Feeding the candidates into explore, and the test routing, are not implemented
yet, so the adaptation refuses to run without `candidate_scan_only`.

Two modes (`candidate_scan_mode`):

  single      (default) one call looks at the data and lists candidates: the v1/v2 scan.
  structured  two isolated calls. `structure` maps the data (full tables written to files,
              features such as strong signals, families sharing a sub-signal, sample
              groupings) and must not propose answers or name features from memory.
              `identify` names what lies behind each feature by querying reference
              resources with its measured values, then composes candidates, each listing
              the features it `explains`. Naming builds on the data, not on a first guess.
              The record has the same shape as `single` (so recall is graded the same way),
              plus `features`, `tables` and `identifications`.

Params:
  candidate_scan_only            end the run after the scan (required for now)
  candidate_scan_mode            single | structured (default single)
  candidate_scan_max             most candidates asked for and kept (default 10)
  candidate_scan_min             fewest asked for; below it, one follow-up call (default 3, 0: off)
  candidate_scan_max_features    structured: most features the structure call may submit (default 12)
  candidate_scan_probe_calls     the stage's tool-call ceiling (SHELDON's `<stage>_probe_calls`);
                                 `candidate_scan_structure_probe_calls` and
                                 `candidate_scan_identify_probe_calls` set each structured call's
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from sheldon.core.types import Prediction, RunContext, Task, Usage
from sheldon.methods.base import timer
from sheldon.methods.sheldon import stages
from sheldon.methods.sheldon.run import Run
from sheldon.methods.stage import Prompt, ask, isolated, own_retrievals, split_source, submit_tool, will_search

NAME = "candidate_scan"
TAG = "candidate_scan"
EVENT = "candidate_scan.done"
STOP_REASON = "candidate_scan_only"
DEFAULT_MAX = 10
DEFAULT_MIN = 3
DEFAULT_PROBE_CALLS = 20
DEFAULT_MAX_FEATURES = 12
MODES = ("single", "structured")
STRUCTURE_TAG = f"{TAG}.structure"
IDENTIFY_TAG = f"{TAG}.identify"

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)), undefined=StrictUndefined, autoescape=False,
                   trim_blocks=True, lstrip_blocks=True)  # the settings of SHELDON's own prompts.py


class CandidateDraft(BaseModel):
    answer: str = Field(default="", description="One specific answer, in exactly the form the question asks for")
    evidence: str = Field(default="", description="What in the data supports it (file, column, value, count), "
                                                  "checkable by another reader")
    basis: Literal["data", "lookup", "prior"] = Field(
        default="data", description="data: the data points to it; lookup: an external resource; "
                                    "prior: background knowledge not yet checked against the data")
    sources: list[str] = Field(default_factory=list, description="Retrieval ids the evidence rests on")


class OpenTestDraft(BaseModel):
    question: str = Field(default="", description="A method or data-quality question to settle")
    kind: Literal["method", "data_quality"] = "method"
    affects: list[str] = Field(default_factory=list,
                               description="Candidates it could change, by position: c1 is the first listed")


class ScanOut(BaseModel):
    inventory: str = Field(default="", description="What the data holds, in a few lines")
    candidates: list[CandidateDraft] = Field(default_factory=list, description="Most supported first")
    tests: list[OpenTestDraft] = Field(default_factory=list)


SUBMIT_SCAN = submit_tool("submit_candidate_scan", "Submit the candidate answers and tests. Call once, at the end.",
                          ScanOut)


# -- structured mode: structure, then identify ------------------------------------------


class FeatureDraft(BaseModel):
    kind: Literal["signal", "family", "grouping", "contrast", "anomaly"] = Field(
        default="signal", description="signal: one strong unit; family: units sharing a sub-signal; grouping: "
                                      "samples or files that go together; contrast: a difference between "
                                      "conditions; anomaly: something unexpected given the task")
    description: str = Field(default="", description="The pattern, with values another reader can check "
                                                     "(m/z, counts, sample or file ids, sequence ids, gene ids)")
    values: str = Field(default="", description="What a reference lookup would query with: exact masses or "
                                                "formulas, sequence ids and the file holding them, gene ids, "
                                                "the member list of a group")
    salience: str = Field(default="", description="How strong or recurrent it is relative to the rest of the data")
    sources: list[str] = Field(default_factory=list, description="Retrieval ids the feature rests on")


class StructureOut(BaseModel):
    inventory: str = Field(default="", description="What the data holds, in a few lines")
    tables: list[str] = Field(default_factory=list, description="Paths of the full tables written, each with "
                                                                "one line on what it holds")
    features: list[FeatureDraft] = Field(default_factory=list, description="Most salient first")


class IdentificationDraft(BaseModel):
    feature: str = Field(default="", description="Feature id, f1 ...")
    entity: str = Field(default="", description="What lies behind the feature; empty if nothing was found")
    how: str = Field(default="", description="The resource and query used and what it returned; what was tried "
                                             "if nothing was found")
    basis: Literal["data", "lookup", "prior"] = "lookup"
    sources: list[str] = Field(default_factory=list, description="Retrieval ids")


class ExplainedCandidate(CandidateDraft):
    explains: list[str] = Field(default_factory=list, description="Feature ids this answer accounts for")


class IdentifyOut(BaseModel):
    identifications: list[IdentificationDraft] = Field(default_factory=list)
    candidates: list[ExplainedCandidate] = Field(default_factory=list, description="Most supported first")
    tests: list[OpenTestDraft] = Field(default_factory=list)


SUBMIT_STRUCTURE = submit_tool("submit_structure", "Submit the data's structure. Call once, at the end.",
                               StructureOut)
SUBMIT_IDENTIFY = submit_tool("submit_candidate_scan", "Submit the identifications, candidate answers and tests. "
                              "Call once, at the end.", IdentifyOut)


EXTEND = """You submitted this scan:

{submitted}

It lists {n} candidate(s); at least {min_candidates} are needed. Return the whole list again: keep \
your candidates first, unchanged, then add specific answers that differ from them, in the form \
the question asks for, until there are at least {min_candidates}. Each added candidate is a real \
alternative answer (a different reading of the data, a different resource or definition, a close \
rival), not a restatement. Set `basis` honestly; an added candidate you did not check is `prior`. \
Keep the inventory and tests."""


def render_prompt(run: Run, max_candidates: int, tools: bool, min_candidates: int = DEFAULT_MIN,
                  max_calls: int = DEFAULT_PROBE_CALLS) -> Prompt:
    source = (PROMPT_DIR / "candidate_scan.prompt").read_text(encoding="utf-8")
    sections = {k: _env.from_string(v) for k, v in split_source(source).items()}
    context = dict(situation=run.situation().strip(), deliverable=run.deliverable().strip(),
                   requirements=run.requirements(), max_candidates=max_candidates,
                   min_candidates=max(1, min_candidates), max_calls=max_calls, tools=tools)
    return Prompt(system=sections["system"].render(**context).strip(),
                  user=sections["user"].render(**context).strip())


def retrieval_id(ref: str) -> str:
    """`r3`, `R3`, `[r3]`, `retrieval 3`, `3` -> `r3`: models cite retrievals every way.
    Anything else is returned stripped, unchanged."""
    m = re.fullmatch(r"\[?\s*(?:r|retrieval)?\s*(?:id)?\s*#?\s*(\d+)\s*\]?", str(ref).strip(), re.I)
    return f"r{m.group(1)}" if m else str(ref).strip()


def feature_id(ref: str) -> str:
    """`f2`, `F2`, `[f2]`, `feature 2`, `2` -> `f2`. Anything else is returned stripped."""
    m = re.fullmatch(r"\[?\s*(?:f|feature)?\s*#?\s*(\d+)\s*\]?", str(ref).strip(), re.I)
    return f"f{m.group(1)}" if m else str(ref).strip()


def clean_structure(out: StructureOut | None, max_features: int) -> dict[str, Any]:
    """Features numbered f1.. in the order given, empty ones dropped, cut to `max_features`."""
    out = out or StructureOut()
    features = []
    for f in out.features:
        if not f.description.strip() or len(features) >= max_features:
            continue
        features.append({"id": f"f{len(features) + 1}", "kind": f.kind, "description": f.description.strip(),
                         "values": f.values.strip(), "salience": f.salience.strip(),
                         "sources": [s.strip() for s in f.sources if s.strip()]})
    return {"inventory": out.inventory.strip(), "tables": [t.strip() for t in out.tables if t.strip()],
            "features": features}


def render_features(features: list[dict[str, Any]]) -> str:
    lines = []
    for f in features:
        lines.append(f"- [{f['id']}] ({f['kind']}) {f['description']}")
        if f["values"]:
            lines.append(f"  - lookup values: {f['values']}")
        if f["salience"]:
            lines.append(f"  - salience: {f['salience']}")
        if f["sources"]:
            lines.append(f"  - sources: {', '.join(f['sources'])}")
    return "\n".join(lines) or "(none found)"


def render_structured_prompt(name: str, run: Run, tools: bool, **extra: Any) -> Prompt:
    """`structure_scan.prompt` or `structure_identify.prompt`, with the task as the scan sees it."""
    source = (PROMPT_DIR / f"{name}.prompt").read_text(encoding="utf-8")
    sections = {k: _env.from_string(v) for k, v in split_source(source).items()}
    context = dict(situation=run.situation().strip(), deliverable=run.deliverable().strip(),
                   requirements=run.requirements(), tools=tools, **extra)
    return Prompt(system=sections["system"].render(**context).strip(),
                  user=sections["user"].render(**context).strip())


def _key(answer: str) -> str:
    return re.sub(r"\s+", " ", answer).strip().lower()


def clean(out: ScanOut | None, max_candidates: int, retrieval_ids: list[str] | None = None,
          first_scan: int | None = None) -> dict[str, Any]:
    """Number the candidates c1.. in the order given, after dropping empty and repeated
    answers and cutting to `max_candidates`. Test references are renumbered to match and
    dropped where they name no kept candidate. `sources_found` marks the cited retrieval
    ids this stage actually made (a `data` candidate citing none of its own is suspect).
    With `first_scan`, candidates past that position in `out` came from the follow-up call
    and are marked `extended`."""
    out = out or ScanOut()
    own = set(retrieval_ids or [])
    kept: list[dict[str, Any]] = []
    renumber: dict[str, str] = {}
    seen: dict[str, str] = {}
    dropped = 0
    for i, c in enumerate(out.candidates, start=1):
        key = _key(c.answer)
        if not key:
            dropped += 1
            continue
        if key in seen:
            renumber[f"c{i}"] = seen[key]
            dropped += 1
            continue
        if len(kept) >= max_candidates:
            dropped += 1
            continue
        cid = f"c{len(kept) + 1}"
        seen[key] = renumber[f"c{i}"] = cid
        sources = [s.strip() for s in c.sources if s.strip()]
        kept.append({"id": cid, "answer": c.answer.strip(), "evidence": c.evidence.strip(), "basis": c.basis,
                     "sources": sources, "sources_found": [s for s in sources if retrieval_id(s) in own],
                     "extended": first_scan is not None and i > first_scan})
        if isinstance(c, ExplainedCandidate):  # structured mode only, so `single` records keep their shape
            kept[-1]["explains"] = list(dict.fromkeys(feature_id(f) for f in c.explains if str(f).strip()))
    tests = []
    for t in out.tests:
        if not t.question.strip():
            continue
        affects = list(dict.fromkeys(renumber[a.strip().lower()] for a in t.affects
                                     if a.strip().lower() in renumber))
        tests.append({"id": f"t{len(tests) + 1}", "question": t.question.strip(), "kind": t.kind,
                      "affects": affects})
    return {"inventory": out.inventory.strip(), "candidates": kept, "tests": tests, "dropped": dropped}


def render_answer(record: dict[str, Any]) -> str:
    """The candidate list as the attempt's (ungraded) answer text."""
    lines = [f"{c['id']}. {c['answer']}  [{c['basis']}]" for c in record.get("candidates") or []]
    return "Candidate answers (scan only):\n" + ("\n".join(lines) if lines else "(none)")


def scan_mode(param: Any) -> str:
    mode = str(param("candidate_scan_mode", "single")).strip().lower()
    if mode not in MODES:
        raise ValueError(f"candidate_scan: unknown candidate_scan_mode {mode!r}; use {' or '.join(MODES)}")
    return mode


async def scan(run: Run) -> tuple[dict[str, Any], Usage]:
    """One tool-using stage call; never raises past a usable empty record."""
    if scan_mode(run.param) == "structured":
        return await structured_scan(run)
    ctx = run.ctx
    max_candidates = int(run.param("candidate_scan_max", DEFAULT_MAX))
    sub = isolated(ctx)
    start = len(run.record())
    tools = will_search(ctx, run.param, TAG)
    min_candidates = int(run.param("candidate_scan_min", DEFAULT_MIN))
    max_calls = int(run.param(f"{TAG}_probe_calls", DEFAULT_PROBE_CALLS))
    prompt = render_prompt(run, max_candidates, tools, min_candidates, max_calls)
    got = await ask(sub, param=run.param, schema=ScanOut, tool=SUBMIT_SCAN, tag=TAG, prompt=prompt,
                    default_steps=DEFAULT_PROBE_CALLS)
    retrievals = own_retrievals(sub, run.record(), start)
    ids = [r.id for r in retrievals]
    out = got.parsed if isinstance(got.parsed, ScanOut) else None
    usage = got.usage
    record = clean(out, max_candidates, ids)
    first = len(record["candidates"])
    extended = 0
    if out is not None and 0 < min_candidates and first < min(min_candidates, max_candidates):
        more, u = await extend(run, prompt, out, first, min_candidates)
        usage += u
        if more is not None:
            # the scan's own list first, whatever the follow-up did to it, then its additions
            merged = ScanOut(inventory=out.inventory, candidates=[*out.candidates, *more.candidates],
                             tests=out.tests or more.tests)
            record = clean(merged, max_candidates, ids, first_scan=len(out.candidates))
            extended = len(record["candidates"]) - first
    record.update(parsed=out is not None, retrievals=len(retrievals), stopped_by=got.stopped_by,
                  searched=got.searched, first_scan_candidates=first, extended_candidates=extended,
                  usage=usage.to_dict())
    ctx.trace.log(EVENT, **{k: v for k, v in record.items() if k != "inventory"})
    return record, usage


async def structured_scan(run: Run) -> tuple[dict[str, Any], Usage]:
    """`structure`, then `identify`, each an isolated tool-using call; the record has the
    `single` shape plus the features, tables and identifications. Never raises past a
    usable empty record: a failed structure call leaves identify with no features."""
    ctx = run.ctx
    max_candidates = int(run.param("candidate_scan_max", DEFAULT_MAX))
    min_candidates = int(run.param("candidate_scan_min", DEFAULT_MIN))
    max_features = int(run.param("candidate_scan_max_features", DEFAULT_MAX_FEATURES))
    tools = will_search(ctx, run.param, TAG)

    def calls(sub: str) -> int:  # what `ask` enforces for a call tagged candidate_scan.<sub>
        return int(run.param(f"{TAG}_{sub}_probe_calls", run.param(f"{TAG}_probe_calls", DEFAULT_PROBE_CALLS)))

    usage = Usage()
    sub, start = isolated(ctx), len(run.record())
    prompt = render_structured_prompt("structure_scan", run, tools, max_features=max_features,
                                      max_calls=calls("structure"))
    got = await ask(sub, param=run.param, schema=StructureOut, tool=SUBMIT_STRUCTURE, tag=STRUCTURE_TAG,
                    prompt=prompt, default_steps=DEFAULT_PROBE_CALLS)
    usage += got.usage
    first_retrievals = own_retrievals(sub, run.record(), start)
    structure = clean_structure(got.parsed if isinstance(got.parsed, StructureOut) else None, max_features)
    structure_meta = {"structure_parsed": isinstance(got.parsed, StructureOut), "structure_stopped_by": got.stopped_by,
                      "structure_retrievals": len(first_retrievals)}
    ctx.trace.log(f"{TAG}.structure_done", features=structure["features"], tables=structure["tables"],
                  **structure_meta)

    sub, start = isolated(ctx), len(run.record())
    prompt = render_structured_prompt(
        "structure_identify", run, tools, inventory=structure["inventory"], tables=structure["tables"],
        features=render_features(structure["features"]), max_candidates=max_candidates,
        min_candidates=max(1, min_candidates), max_calls=calls("identify"))
    asked = await ask(sub, param=run.param, schema=IdentifyOut, tool=SUBMIT_IDENTIFY, tag=IDENTIFY_TAG,
                      prompt=prompt, default_steps=DEFAULT_PROBE_CALLS)
    usage += asked.usage
    ids = [r.id for r in first_retrievals + own_retrievals(sub, run.record(), start)]
    out = asked.parsed if isinstance(asked.parsed, IdentifyOut) else None
    candidates, tests = (list(out.candidates), list(out.tests)) if out else ([], [])
    # model_construct keeps the ExplainedCandidate objects, so clean() carries `explains`
    record = clean(ScanOut.model_construct(inventory=structure["inventory"], candidates=candidates, tests=tests),
                   max_candidates, ids)
    first = len(record["candidates"])
    extended = 0
    if out is not None and 0 < min_candidates and first < min(min_candidates, max_candidates):
        plain = ScanOut(inventory=structure["inventory"], tests=tests,
                        candidates=[CandidateDraft(**c.model_dump(exclude={"explains"})) for c in candidates])
        more, u = await extend(run, prompt, plain, first, min_candidates)
        usage += u
        if more is not None:
            merged = ScanOut.model_construct(inventory=structure["inventory"], tests=tests or more.tests,
                                             candidates=[*candidates, *more.candidates])
            record = clean(merged, max_candidates, ids, first_scan=len(candidates))
            extended = len(record["candidates"]) - first
    identifications = [{"feature": feature_id(i.feature), "entity": i.entity.strip(), "how": i.how.strip(),
                        "basis": i.basis, "sources": [s.strip() for s in i.sources if s.strip()]}
                       for i in (out.identifications if out else []) if i.feature.strip() or i.entity.strip()]
    record.update(mode="structured", features=structure["features"], tables=structure["tables"],
                  identifications=identifications, parsed=out is not None, retrievals=len(ids),
                  stopped_by=asked.stopped_by, searched=got.searched or asked.searched,
                  first_scan_candidates=first, extended_candidates=extended, usage=usage.to_dict(), **structure_meta)
    ctx.trace.log(EVENT, **{k: v for k, v in record.items() if k != "inventory"})
    return record, usage


async def extend(run: Run, prompt: Prompt, out: ScanOut, n: int, min_candidates: int) -> tuple[ScanOut | None, Usage]:
    """One tool-less call asking a short list to grow to `min_candidates`. Never raises."""
    ctx = run.ctx
    ask_more = EXTEND.format(submitted=out.model_dump_json(indent=1), n=n, min_candidates=min_candidates)
    try:
        res = await ctx.llm.complete(f"{prompt.user}\n\n---\n\n{ask_more}", system=prompt.system, schema=ScanOut,
                                     tag=f"{TAG}.extend")
    except Exception as e:  # noqa: BLE001 - the scan's own list stands
        ctx.trace.log(f"{TAG}.extend_error", error=f"{type(e).__name__}: {e}")
        return None, Usage()
    ctx.budget.charge(res.usage)
    return (res.parsed if isinstance(res.parsed, ScanOut) else None), res.usage


async def scan_only(task: Task, ctx: RunContext, param: Any, analysis: Any = None) -> Prediction:
    """plan, then the scan, then stop: the recall measurement. No round and no finalize."""
    scan_mode(param)  # an unknown mode fails before the plan call is paid for
    run = Run(ctx=ctx, task=task, param=param)
    if analysis is not None:
        analysis.run = run
    usage = Usage()
    with timer() as t:
        usage += await stages.plan(run)
        record, u = await scan(run)
        usage += u
    ctx.trace.log("sheldon.final", round=0, stop_reason=STOP_REASON, candidates=len(record["candidates"]))
    answer = render_answer(record)
    return Prediction(task_id=task.id, answer=answer, attempts=[answer], belief={}, rounds=0, usage=usage,
                      wall_s=t.elapsed, extra={"stop_reason": STOP_REASON, NAME: record})
