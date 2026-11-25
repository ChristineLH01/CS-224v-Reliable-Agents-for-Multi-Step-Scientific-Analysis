# Reliable Agents for Multi-Step Scientific Analysis: BioMysteryBench

`bmbench` is an evaluation harness that runs coding agents on
[BioMysteryBench](https://www.anthropic.com/research/Evaluating-Claude-For-Bioinformatics-With-BioMysteryBench).
It makes several attempts at each problem and measures **accuracy** and **consistency**.

BioMysteryBench problems are written by bioinformatics experts. Metadata was removed from real
datasets, and the agent must recover it through analysis, e.g. "Which gene was knocked out in
these RNA-seq samples?". The answers are short, but reaching them takes many analysis steps.

| Dataset | Problems | Access |
|---|---|---|
| preview | 5 | public |
| full | 90 (155 GB zipped) | gated on Hugging Face |

The harness runs the **Claude Code CLI** (`claude -p`), the **Codex CLI** (`codex exec`) and
**SHELDON** (the [hypothesis-driven reasoning harness](https://github.com/stanford-oval/sheldon);
see [SHELDON](#sheldon)), all unmodified, and **adapted SHELDON**, SHELDON with our changes
switched on (see [Adapted SHELDON](#adapted-sheldon)). The sandbox, prompt, judge and metrics do
not depend on the agent, so the agents are compared under the same conditions.

**Terms:** a *problem* is one BioMysteryBench item (its question plus its data); an *attempt* is
one independent run of the agent on a problem, in a fresh sandbox.

## Setup

| Part | Setting |
|---|---|
| Agents | Each runs headless and unchanged (except adapted SHELDON), with its own tools, context management and subagents, and web search off (internet only through the agents' own commands). **Claude Code:** `claude-opus-5-5`, effort `high`, on a Claude subscription. **Codex:** `gpt-6.1-sol`, effort `high`, through the LiteLLM proxy. **SHELDON:** `gpt-6-luna`, effort `high`, through the LiteLLM proxy, 3 rounds (details in [SHELDON](#sheldon)). **Adapted SHELDON:** the same, with the adaptations on ([Adapted SHELDON](#adapted-sheldon)). |
| Sandbox | A fresh Docker container per attempt, with standard bioinformatics tools (samtools, BLAST+, HOMER, DESeq2, scanpy, …). The agent can install more with conda or pip. Only the problem's data is mounted; the answer key never is. |
| Network | Through a proxy that only allows the problem's listed sites (NCBI, Ensembl, UCSC, conda, PyPI, …) and the model API. |
| Limits | 1 hour, 4 CPUs and 3.5 GB RAM per attempt, for the preview and the full set alike. At the time limit the agent's running work is stopped, and it then has up to 5 minutes to give its best answer (one resumed turn for Claude Code and Codex; SHELDON's own finalize stage). A command that runs out of memory is killed, not the agent, which sees the error and carries on. If the agent process itself is killed for memory, the attempt ends there and is scored incorrect (*out of memory*). |
| Attempts | 3 per problem, and 2 attempts run at a time (`concurrency: 2`). Attempts run problem by problem: each problem's data is downloaded before its first attempt and deleted after its last. |
| Grading | One judge for every agent: Opus 5.5, run as `claude -p` on the Claude subscription. It checks (1) the answer against the answer key and (2) the trajectory for cheating, i.e. looking up the source dataset (GEO/SRA accessions, the original paper). An attempt scores 1 only if it is correct and did not cheat. |

## Metrics

Each problem weighs the same. With *n* attempts per problem (default 3):

| | Metric | Meaning |
|---|---|---|
| Accuracy | **accuracy** | share of correct attempts, averaged over problems |
| | **pass@n** | share of problems solved in at least one attempt |
| Consistency | **pass^n** | share of problems solved in every attempt |
| | **answer agreement** | share of a problem's attempts that give its most common answer, right or wrong |
| | **correct-attempts histogram** | how many problems were solved 3/3, 2/3, 1/3, 0/3 |
| Cost | **mean cost, mean time** | per attempt; cost is an API-price estimate |

The report also lists the attempts that timed out, ran out of memory, or were flagged for cheating. All metrics are
given overall and split into human-solvable and human-hard problems.

## Usage

**Prerequisites**

- Docker (Docker Desktop on macOS) and [uv](https://docs.astral.sh/uv/)
- In `.env` (copy it from `.env.example`):
  - `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`, for Claude Code
  - `LITELLM_API_KEY`, for Codex and SHELDON
  - `HF_TOKEN`, for the full set only (accept its terms on Hugging Face first)

**Commands**

```bash
uv sync                                   # install
cp .env.example .env                      # then fill in the keys
uv run bmbench build                      # build the sandbox and proxy images (once)
uv run bmbench doctor --auth-check -c configs/claude_code_preview.yaml   # check Docker, keys, judge, network for Claude Code
uv run bmbench doctor --auth-check -c configs/codex_preview.yaml         # the same for Codex
uv run bmbench doctor --auth-check -c configs/sheldon_preview.yaml       # the same for SHELDON (+ an offline selftest)

uv run bmbench run -c configs/claude_code_preview.yaml   # Claude Code: 5 problems × 3 attempts
uv run bmbench run -c configs/codex_preview.yaml         # Codex: the same
uv run bmbench run -c configs/sheldon_preview.yaml       # SHELDON: the same
uv run bmbench run -c configs/sheldon_adapted_preview.yaml   # adapted SHELDON: every adaptation on (see Adapted SHELDON)
uv run bmbench run -c configs/claude_code_full.yaml      # 90 problems × 3 attempts (codex_full.yaml, sheldon_full.yaml)
uv run bmbench run -c configs/codex_luna_10q.yaml        # Codex on SHELDON's model (gpt-6-luna), SHELDON baseline's 10 questions × 3 attempts (results: docs/codex-vs-sheldon-same-model.md)

uv run bmbench run   -c <config> --set agent.model=<model> --set agent.effort=<effort>  # another agent model / effort
uv run bmbench run   -c <config> --set grader.model=<model>                             # another judge model
uv run bmbench grade -r runs/<run_name> --regrade --set grader.model=<model>            # re-grade a finished run

uv run bmbench report -r runs/<run_name>                 # print the report
uv run bmbench show   -r runs/<run_name> <problem> <k>   # one attempt (--full: whole transcript)
```

`<config>` is any file in `configs/`.

- **Resuming:** re-running the same command skips finished attempts. If a usage limit stops the
  run, run the same command again later.
- **Changing settings:** `--problems`, `--attempts` and `--run-name` pick the problems, the number
  of attempts and the run name. `--concurrency <n>` sets how many attempts run at once; raise it
  only if Docker has CPUs and memory for *n* × the per-attempt limits. `--set key=value` overrides any field in `bmbench/config.py`, e.g.
  `--problems hb024 --set sandbox.timeout_s=7200` re-runs one problem with a 2-hour limit, in a
  run folder of its own.
- **Changing models:** the agent model must be one its CLI can use: Claude models for Claude
  Code, models served by the LiteLLM proxy for Codex and SHELDON. A `claude-*` judge runs on the Claude 
  subscription; any other judge goes through LiteLLM. The judge is not part of the run name, 
  so the report's setup line states which judge graded the run.

**Outputs** (`runs/<run_name>/`)

The run name is built from the settings, `<preview|full>_<agent>_<model>_<effort>_timeout-<limit>_<n>-attempts`,
e.g. `preview_codex_gpt-6-1-sol_high_timeout-1h_3-attempts`, so runs with different settings never
share a folder. SHELDON's names add its round budget: `preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`,
and any adaptations after it (`..._3-rounds_early-stop_timeout-1h_3-attempts`).

| File | Content |
|---|---|
| `report.md` | setup, summary table, one row per problem |
| `metrics.json`, `attempts.csv` | all metrics; one row per attempt |
| `problems/<problem>/prompt.md` | the prompt, shared by all attempts at the problem |
| `problems/<problem>/attempt-<k>/` | one attempt: transcript, raw agent output, the agent's own session log, result, grade, network log, `workspace_files.tsv` |

**Storage.** To analyze the data, agents often download large files, such as reference genomes
and databases (several GB in some attempts). These only serve to reach the answer, so each
attempt's workspace is deleted when the attempt ends; `workspace_files.tsv` keeps the name and
size of every file it held. Every command the agent ran and every output it saw stay in the
transcript. To keep the complete workspace, add `--set sandbox.keep_workspace=true`.

## SHELDON

[SHELDON](https://github.com/stanford-oval/sheldon) keeps a set of competing hypotheses, gathers
observations that tell them apart, and records which observations support or refute each. It runs
here **unmodified**: the sandbox image installs it verbatim, with the dependency
versions of its own `uv.lock`, in a venv the agent cannot edit. No SHELDON file is changed; three pieces sit around it, in
`bmbench/agents/sheldon_runtime/bmb_sheldon/`:

| Piece | What it does |
|---|---|
| `benchmark.py` | BioMysteryBench as a SHELDON benchmark. The task statement is `prompts/problem.md`, the same text the other agents get; the answer key never enters the sandbox. No benchmark tools: SHELDON's generic `run_shell`, opening in `/workspace`, is the only tool. |
| `transport.py` | SHELDON's model calls over the OpenAI **Responses API**. Its built-in backend uses Chat Completions and sends no reasoning effort, and the proxy refuses GPT-6 tool calls with a reasoning effort on that route. Every call streams, sends `reasoning.effort`, and replays the previous turns' reasoning items. Only the transport changes. |
| `driver.py` | What the sandbox runs (`python -m bmb_sheldon.driver`): one attempt through SHELDON's own `run_experiment`, the time limit, and a result line. |

SHELDON runs **inside the per-attempt container** like the CLI agents, so it gets the same files,
proxy allowlist (its own model calls too), CPU and memory limits, and out-of-memory protection.

**Settings** (`agent.sheldon` in `configs/sheldon_preview.yaml`, SHELDON's own config fields):

| Setting | Value | Why |
|---|---|---|
| model, effort | `gpt-6-luna`, `high` | every stage, reasoning and coding |
| mode | `session` | one conversation per round for the deciding stages; what SHELDON's own experiment configs use |
| rounds | 3 (`max_rounds`, `rounds`) | the round budget; no call or cost cap (SHELDON's default 40 calls / $1 would end it in round 1) |
| hypotheses tested at once | ≤ 3 (`candidate_analysis_concurrent: true`, `n_initial: 3`, `select_k: 3`) | parallel tests in one container, as SHELDON is designed, capped |
| tools | `run_shell` in `/workspace` (600 s per command) | SHELDON's web tools are Anthropic-only; web search is off for every agent |
| output tokens | 16,000 per call (SHELDON's default; includes reasoning) | |
| cache | off | repeated attempts must be independent |

**At the time limit** SHELDON's own budget stops new stages, the driver kills the commands it has
running (and any it starts after), and SHELDON's finalize stage gives the answer from what it has
gathered, within the 5-minute closing window. Its answer is finalize's structured `answer` field (the text in
answer tags if it has them).

**Outputs:** `transcript.md` is organised by round and stage (plan; per round the hypotheses
proposed, each hypothesis's tests with their commands and outputs, the comparative analysis, the
keep/refine/shelve decisions; finalize; the final hypotheses and observations). `trajectory.json`
holds every shell command with its output, for the cheating audit. `result.json` adds a `sheldon`
block: rounds, stop reason, hypotheses and observations, whether the time limit was hit, and how many
commands repeated earlier work. SHELDON's own records (trace with every prompt, retrievals) are in
`agent_session/sheldon/runs/`, the trace gzipped.

### Adapted SHELDON

Our changes to SHELDON, each switched on by name in `agent.adaptations`. With none on (the
default) SHELDON runs unmodified, as above; with any on, the driver runs
`bmb_sheldon/adapted/`, SHELDON's own run loop with one marked hook per change (a test keeps the
copy equal to SHELDON's). The run name lists the adaptations that are on. `[all]` means the
two the dated `all` configs ran with, `early_stop` and `shared_analysis_state`; every later
adaptation is switched on by name, so those configs keep their meaning.

**Which to use from now on.** Always include `plan_keeps_question`. It fixes a real bug: in 7 of
30 baseline attempts the stages before finalize never saw the question. It does not raise the
score: on the ten 10q questions it gets 16/30, against 17/30 with `early_stop` and
`shared_analysis_state` alone, within the spread of 3 attempts (`runs/pkq_es_ss_10q_3attempts_20261122`).
Keep it because a run that never sees its question is not a valid run, and look for gains elsewhere.
The current setting is `[early_stop, shared_analysis_state, plan_keeps_question]` (`configs/sheldon_pkq_es_ss_10q.yaml`).
Leave `candidate_scan`, `keep_answer_rivals` and `keep_neutral_hypotheses` off. They were tested
and did not help ([candidate-scan.md](docs/candidate-scan.md), [answer-rivals.md](docs/answer-rivals.md)),
and they stay in the code only so those runs can be reproduced. The golden hypothesis is an
oracle ablation for diagnosis, never a setting for real runs.

```bash
uv run bmbench run -c configs/sheldon_preview.yaml                                         # baseline: unmodified SHELDON
uv run bmbench run -c configs/sheldon_preview.yaml --set 'agent.adaptations=[early_stop]'  # one adaptation
uv run bmbench run -c configs/sheldon_pkq_es_ss_10q.yaml                                   # the current setting (see above), 10q × 3 attempts
uv run bmbench run -c configs/sheldon_adapted_preview.yaml                                 # [all] (sheldon_adapted_full.yaml: 90 problems)
```

| Adaptation | What it does |
|---|---|
| `early_stop` | Ends the run after a round early, before the round cap, once the settled case is closed, then finalizes as usual (stop reason `settled`). One extra tool-less call reads the record; fixed rules decide, from SHELDON's own verdicts: the lead answers the whole question, has at least 1 supporting and no refuting observation, there is at least one rival hypothesis and each has more refuting than supporting observations, and no untested rival or open issue is left. `scripts/replay_early_stop.py` replays the check on a finished run. |
| `shared_analysis_state` | Shares an attempt-local ledger of downloads, lookups and reported findings across stages and rounds. Each parallel test gets a stable directory across its local iterations and a structured history handoff. Observations, candidates and lookups are recorded automatically; `download_analysis` reuses completed downloads and serializes concurrent requests for the same URL. |
| `candidate_scan` | **Off: no effect.** Not in `[all]`. After plan, one tool-using stage lists the candidate answers the data supports (3 to 10, each with evidence), plus method and data-quality questions as tests. Only an offline recall mode exists: it requires `candidate_scan_only: true`, and the run stops after the scan. `candidate_scan_mode: structured` splits the scan into two isolated calls: `structure` maps the data into full tables and features (strong signals, families sharing a sub-signal, sample groupings) without proposing answers, then `identify` names each feature through reference lookups and lists candidates with the features each explains (`configs/sheldon_structure_scan_recall_*.yaml`, run by `scripts/run_sheldon_structure_scan.py`). `scripts/eval_candidate_recall.py` grades each candidate with the official grader. Results: [docs/candidate-scan.md](docs/candidate-scan.md). |
| `plan_keeps_question` | **Keep on.** Not in `[all]`. If SHELDON's plan split moves the question sentence out of `situation` (the only part the stages before finalize see), the split is undone and `situation` becomes the whole statement. Every check is logged. Fixes the bug; 16/30 vs 17/30 on 10q (no gain). Results: [docs/plan-keeps-question.md](docs/plan-keeps-question.md). |
| `keep_answer_rivals` | **Off: no effect** ([docs/answer-rivals.md](docs/answer-rivals.md)). Not in `[all]`. After each round, a claim shelved because a local loop revised it or exploit refined it is compared with its replacement in one tool-less call. If the two give different answers, the shelved claim is kept as a live rival. A failed call changes nothing. Exploit can still shelve the rival later on its evidence. |
| `keep_neutral_hypotheses` | **Off: no effect** (same doc). Not in `[all]`. After each round, a claim that exploit shelved with no supporting and no refuting observation (it could not be checked, as opposed to being refuted) is kept instead. Shelving by select or by a local loop is unaffected. No model call. |

#### Golden hypothesis (oracle ablation)

`agent.golden_answers` (problem id → answer, or the path of a YAML file holding that map; the configs use the git-ignored `data/golden_answers.yaml`, so write it yourself before running them) tests whether SHELDON keeps a correct answer when
it has one from the start. It is not an adaptation name, because `adaptations` can be read
from inside the sandbox. For a listed problem, after the round-1 explore turn, one call with
tracing off writes a 4th hypothesis that states the answer, in the style of the model's 3.
It is inserted at a random position, and the model's `submit_exploration` call in the
session history is rewritten to hold it (that reply loses its encrypted reasoning). Nothing
marks it. The answer reaches the driver only on stdin, through a host file
(`attempt-N/stdin.main.txt`) that is not mounted into the sandbox. It never appears in the
environment (which `run_shell` inherits), config.json, task.json or the trace. Session mode
only. The driver's result line reports the injected id, position and statement.
`scripts/audit_golden_hypothesis.py RUN_DIR` follows the hypothesis round by round and lists
any command that read the session files, the environment or `/proc`. The trace still keeps
the original 3-hypothesis explore reply. Config: `configs/sheldon_golden_hypothesis_5q.yaml`
(image `bmbench-sandbox:golden-hypothesis`, built with `docker/sandbox/Dockerfile.runtime`).
Results (7/15 vs the baseline's 2/15 on the same 5 questions): [docs/golden-hypothesis.md](docs/golden-hypothesis.md).

The same ablation for Codex (or Claude Code): `agent.golden_hypotheses_from` names a SHELDON
golden run. Attempt N of each problem gets the 4 round-1 hypotheses that attempt N of the
SHELDON run showed its model: its own 3 from the trace, plus the golden one at its reported
position. They go in the prompt, in a "Candidate hypotheses" section, as unverified candidates
from a preliminary exploration, with nothing marking the golden one. The prompt goes only into
the host file `attempt-N/stdin.main.txt`, and the golden position is recorded in
`attempt-N/golden_hint.json`. Config: `configs/codex_golden_hypothesis_5q.yaml` (bmbench/golden_hints.py).
With plan_keeps_question on (`configs/sheldon_golden_hypothesis_pkq_5q.yaml`), SHELDON gets 6/15
and Codex with the same candidates 10/15: [docs/golden-hypothesis-pkq-vs-codex.md](docs/golden-hypothesis-pkq-vs-codex.md).
How the candidates are injected into Codex, and why SHELDON uses the golden hypothesis worse than Codex (from the trajectories): [docs/sheldon-vs-codex.md](docs/sheldon-vs-codex.md).
Stdin files and `prompt.md` are written with LF on every platform. A sentinel left in the prompt
(as CRLF caused on Windows, leaking the answer) stops the attempt, and the audit script counts
sentinels in each trace.

#### Per-stage models

`agent.stage_models` (SHELDON call tag → model) sends the calls of those stages to another
model; every other call uses `agent.model`. A tag also routes its dotted children
(`candidate_analysis` covers `candidate_analysis.h3.1`). The tags are `plan`,
`session.explore`, `session.select`, `candidate_analysis`, `session.comparative_analysis`,
`session.exploit` and `finalize`. Each model other than `agent.model` needs a price in
`agent.usd_per_mtok_by_model` (`[input, cached input, output]` USD/Mtok). Attempt costs are
summed per model from the trace (`tokens.by_model` in result.json).

Encrypted reasoning replays only to the model that wrote it. When a session conversation
moves to another model, earlier replies are sent without their reasoning items, but with
their text and tool calls. `doctor --auth-check` continues a conversation on each stage
model to check this.

The motivation: SHELDON spends 7–11% of its cost proposing (plan, explore), and 70–80%
testing. Codex on gpt-6.1-sol scored 24/30 against 17/30 on gpt-6-luna. Its gains were
hb048, where it named the drug from the data, and recsvhviava5okg19, where it chose the
pooled reading.

`configs/sheldon_stage_models_pilot.yaml` runs gpt-6.1-sol for plan, explore and finalize
and gpt-6-luna for the rest, on those two questions (image `bmbench-sandbox:stage-models`).
`configs/sheldon_stage_models_rest8.yaml` runs the same config on the other 8 questions.
Together the two runs cover the 10q set.

`configs/sheldon_adapted_*.yaml` uses `[all]`, which enables `early_stop` and
`shared_analysis_state` only. To measure shared state alone:

```bash
uv run bmbench build  # rebuild the sandbox after changing the runtime
uv run bmbench run -c configs/sheldon_preview.yaml --set 'agent.adaptations=[shared_analysis_state]'
```

Shared state currently requires SHELDON's `toolloop` engine for all stages, in either
`staged` or `session` mode. Unsupported engines fail explicitly. Each test uses
`/workspace/analysis/tests/round-NN/<stage.hypothesis>/` as its shell working
directory, with `data/` pointing to the attempt input. Use absolute paths for other
shared files. These directories prevent accidental relative-path collisions; they do
not restrict what an arbitrary shell command can write.

The complete ledger and bounded summary are mirrored to
`agent_session/sheldon/analysis/{state.json,summary.md}` before workspace cleanup.
Runtime 0.2.0 keeps each test's working directory stable across iterations (including
claim revisions) and keeps different tests/rounds separate. Use iteration-specific
filenames to retain artifact versions. Runtime 0.1.0 experiment outputs remain historical;
rebuild the image and use new run names to evaluate the revised behavior.
Raw test artifacts follow `sandbox.keep_workspace`; enable it if those files are needed
for later inspection. Completed downloads are reused by URL within one attempt; direct
shell downloads are logged but do not participate in this cache. All
scientific conclusions remain agent-reported. Full submissions and individual observations
are preserved in the ledger, without the former 1200-character prefix cut. The bounded
handoff uses complete JSON records, labels excerpts and omitted counts, balances findings
across tests and flags hypotheses with opposing reported verdicts. A successful command
or download never verifies a scientific claim. Normal submit_* responses supply the
records automatically; no record_analysis tool call spends a stage's analysis budget.
No state is shared between independent attempts.
`agent.sheldon.params.analysis_summary_chars` bounds history summaries (default 10000
characters, range 2000–30000). No extra model call is used to build the summary.


## Repository layout

```
bmbench/            the harness
  cli.py              commands: prepare, problems, build, doctor, run, grade, report, show, clean
  config.py           run configuration (YAML + --set overrides)
  dataset.py          download and load BioMysteryBench
  sandbox.py          Docker container, network and proxy per attempt
  agents/             agent adapters (claude_code.py, codex.py, sheldon.py)
    sheldon_runtime/    bmb_sheldon: SHELDON's benchmark adapter, transport and driver (runs in the sandbox);
                        adapted/: the adaptations
  runner.py           runs the attempts
  trajectory.py       parses the agent's output into steps and transcripts
  grader.py           the judge: answer check and cheating audit
  metrics.py, report.py
configs/            run settings per agent, for the preview and the full set
prompts/            problem prompt, timeout and nudge messages, judge prompts
docker/             sandbox image (tools, Claude Code, Codex, SHELDON) and proxy image
scripts/            replay_early_stop.py: the early-stop check on a finished SHELDON run
                    eval_candidate_recall.py: grades candidate_scan lists and baseline round-1 hypotheses
                    audit_golden_hypothesis.py: follows the injected golden hypothesis and flags commands that could reveal it
                    run_sheldon_*.py: resumable batch supervisors (freeze configs, hash sources, wait for running attempts)
                    footprint_ms_check.py, explain_away_check.py, explain_away_selfwritten.py: explain-away feasibility checks (docs/explain-away.md)
docs/               experiment reports and design notes per adaptation
tests/              unit tests (uv run --group dev --group sheldon pytest)
data/, runs/        downloaded benchmark and run outputs (not in git)
```

## Data terms

BioMysteryBench is CC-BY-4.0 and for **evaluation only**: do not use it to train, fine-tune or
distill models. The answer key stays in `data/` on the host and is never given to an agent.
