# Progress Report: SHELDON on BioMysteryBench

**Updated:** October 29, 2026

**Status:** Local baseline completed; one complete local SHELDON diagnostic succeeded on hb020; five-question evaluation pending.

**Current scope:** Automatic judge grading was removed at the user's request.
The adapter now runs tasks and saves answers, usage, errors and traces only.
Earlier judge-related descriptions below are historical, not current features.

## Objective

Evaluate whether SHELDON's hypothesis–observation search improves multi-step
bioinformatics analysis relative to a coding-agent baseline. Preserve the
upstream reasoning method and add only a benchmark-specific adapter.

## Completed work

- Downloaded the five official BioMysteryBench preview questions and recorded
  dataset provenance. Kept answer rubrics separate from solver inputs.
- Built a Codex CLI baseline runner with per-task answers, execution logs,
  timing, timeout tracking, model selection and CLI-path discovery.
- Completed one baseline trial per question and manually compared final
  answers with the official rubrics.
- Added a BioMysteryBench adapter to upstream SHELDON at commit
  `7fbf027d1fb414443612d4ad2661b396c8352cd3`. The method and stage prompts
  remain unchanged. Added data import, workspace preparation, configuration,
  a now-removed optional post-answer judge and trajectory audit.
- Passed six offline adapter tests covering answer blinding, workspace data
  access, grading boundaries, oversized audits, isolation failure and path
  traversal. Upstream staged/session smoke checks and config dry-run passed.
- Configured the supplied Stanford model proxy in local, Git-ignored `.env`.
  An authenticated model-list request succeeded; no inference request was made.

## Preliminary baseline results

The baseline used a shared local Conda environment, one trial per question and
a 60-minute timeout. Model and reasoning effort were not explicitly fixed.
Web search was available; filesystem isolation and network allowlists were not
enforced. All five CLI processes exited successfully, with no timeouts.

| Question | Human solvable | Final-answer match | Time | Main finding |
|---|---|---|---:|---|
| hb002: bacterium in a genome | Yes | 1/1, provisional | 75.91 s | Genome length and GC matched a publication; no BLAST confirmation |
| hb020: organism of a crystal structure | Yes | 1/1 | 95.84 s | Protein sequence extraction and human UniProt fragment match |
| recqgsfxqqodhjens: TF behind peaks | Yes | 0/1 | 41.62 s | Reference download failed; motif analysis incomplete |
| hb035: miRNA associated with genes | No | 0/1 | 44.66 s | Database access failed; agent abstained |
| hb024: microbiome hosts and tissues | No | 0/1 | 33.21 s | Agent stopped after sequence inventory |
| **Total** | **3 yes, 2 no** | **2/5 (40%)** | **291.24 s** | **58.25 s per trial on average** |

This is preliminary answer-match accuracy, not a certified benchmark score.
The hb002 source-publication lookup needs protocol review. The three unmatched
trials abstained rather than exhausting their time budgets. DNS failures
limited several analyses; these results do not establish intrinsic model limits.
API cost was not calculated.

## SHELDON pilot settings

| Item | Current configuration |
|---|---|
| Method / engine | Upstream SHELDON / native toolloop with added Responses transport |
| Solver | `gpt-5.6-terra`; reasoning effort explicitly sent as high |
| Scope | Start with hb020, then expand to five preview questions |
| Search limits | Up to 3 rounds, 40 model calls, 1800 seconds |
| Estimated solver cost cap | $2 per question; framework estimate, not a hard billing cap |
| Parallel tasks | 1 |
| Tools | Shell with local bioinformatics tools; no generic web-search/fetch probes |
| Model cache | Disabled |
| Judge | Removed; no automatic grading calls |
| Isolation | Local debugging mode; strict filesystem and network isolation pending |

Current outputs are answers and execution records. The required upstream score
interface returns status=not_evaluated with a zero placeholder; it is not an
accuracy score. Old score artifacts remain unchanged for provenance.

**Budget:** The supplied account has a user-reported $100 allocation. We have
not independently checked its balance. No new paid inference was initiated in
this setup step. Start with a small pilot, inspect proxy-reported usage and
costs, and expand only after validating data access and output quality. Judge
usage is additional to solver usage; the runner does not enforce the total
account budget. Framework time limits may be exceeded by an in-flight call.

## Next milestones

Latest verified milestone: native-tool SHELDON diagnostic hb020 completed in
107.161 seconds with 16 model calls, no recorded error and $0.193819 estimated
usage. Its final answer, Homo sapiens, matches the rubric text but is explicitly
low confidence and lacks sequence confirmation; independent grading and audit
remain pending. Run: `debug-20261029-233055-866892__54e7a3a379`. The transport
now supplies the submit tool explicitly requested by a schema-backed closing
prompt instead of leaving it unavailable after the search budget expires.
Finalization took about 7 seconds. This is a one-round, 15-search-call diagnostic
plus required closing call, not the formal evaluation configuration. Earlier
pending/failure descriptions below document preceding stages of integration.

Compatibility update: live attempts exposed two proxy limitations. Opus 5.5
rejected structured output translation; Terra rejected native function tools
with reasoning on Chat Completions. The pilot config now uses upstream's
existing `react` engine without changing the method or model backend. A live
three-call check passed structured output plus shell-tool execution and use of
the returned observation; estimated cost was $0.003775. This validates the
calling path, not completion of a BioMysteryBench task. The original Terra
failure reported about $0.00605; the earlier no-inference statement describes
the initial setup step only.

Subsequent update: Responses compatibility passed three synthetic requests.
An opt-in `openai-responses` backend has now been integrated and the current
config restored to native toolloop. Ten offline tests, staged/session smoke
checks and a three-call live SHELDON native-tool/schema test passed (estimated
$0.00199 for the integrated check). Search methods and stage prompts remain
unchanged; model communication code is modified. Earlier ReAct task debugging
timed out and is not a successful result. A full task on the new transport
remains pending.

Finalization follow-up: a native-tool task run reached its closing stage but
timed out. An offline replay reconstructed its final request; longer isolated
retries exposed a proxy response-size error. The same request completed in
29.86 seconds using streaming (estimated $0.034417), but its species answer
was incorrect. Streaming is now the default for the Responses backend and
passed the live native-tool/schema check again (estimated $0.001902). Eleven
offline tests and both upstream smoke checks pass. A fresh full-task run with
this workaround is still needed; no successful SHELDON benchmark result is
claimed.

1. Run one live SHELDON pilot and validate tool execution, database access,
   final answer extraction and hypothesis/observation traces.
2. Review completed answers and trajectories separately from solving. No
   automatic judge is part of the current implementation.
3. Run all five preview questions before scaling to five independent trials
   each. Use fresh workspaces and keep model caching off.
4. Align the direct baseline and SHELDON on models, tools, data revision,
   budgets and judging. Obtain the Claude Code harness before claiming a
   matched comparison; the reported 80% CC result is external, not reproduced.
5. Add enforced domain allowlists and isolated execution for formal results.

## Artifacts and references

- Adapter instructions: `upstream-sheldon/BIOMYSTERYBENCH.md`
- Pilot config: `upstream-sheldon/configs/experiments/biomysterybench_preview.yaml`
- Baseline details: `baseline/results.md` and `baseline/results.csv` (local artifacts)
- Upstream method: https://github.com/stanford-oval/sheldon
- Official preview: https://huggingface.co/datasets/Anthropic/BioMysteryBench-preview
- Model service documentation: https://models.genie.stanford.edu/

This document is suitable for sharing. It contains no API keys, credentials or
gold answer rubrics. No external document, message or GitHub update has been
published as part of preparing it.
