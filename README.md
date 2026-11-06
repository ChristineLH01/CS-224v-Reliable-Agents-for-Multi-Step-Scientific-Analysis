# Reliable Agents for Multi-Step Scientific Analysis: BioMysteryBench

`bmbench` is an evaluation harness that runs coding agents, unchanged, on
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
**SHELDON** (the [hypothesis-driven reasoning harness](https://github.com/stanford-oval/sheldon),
unmodified; see [SHELDON](#sheldon)). The sandbox, prompt, judge and metrics do not depend on the
agent, so the agents are compared under the same conditions.

**Terms:** a *problem* is one BioMysteryBench item (its question plus its data); an *attempt* is
one independent run of the agent on a problem, in a fresh sandbox.

## Setup

| Part | Setting |
|---|---|
| Agents | Each runs headless and unchanged, with its own tools, context management and subagents, and web search off. **Claude Code:** `claude-opus-5-5`, effort `high`, on a Claude subscription. **Codex:** `gpt-6.1-sol`, effort `high`, through the LiteLLM proxy. **SHELDON:** `gpt-6-luna`, effort `high`, through the LiteLLM proxy, 3 rounds (details in [SHELDON](#sheldon)). |
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
uv run bmbench run -c configs/claude_code_full.yaml      # 90 problems × 3 attempts (codex_full.yaml, sheldon_full.yaml)

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
share a folder. SHELDON's names add its round budget: `preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`.

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

## Repository layout

```
bmbench/            the harness
  cli.py              commands: prepare, problems, build, doctor, run, grade, report, show, clean
  config.py           run configuration (YAML + --set overrides)
  dataset.py          download and load BioMysteryBench
  sandbox.py          Docker container, network and proxy per attempt
  agents/             agent adapters (claude_code.py, codex.py, sheldon.py)
    sheldon_runtime/    bmb_sheldon: SHELDON's benchmark adapter, transport and driver (runs in the sandbox)
  runner.py           runs the attempts
  trajectory.py       parses the agent's output into steps and transcripts
  grader.py           the judge: answer check and cheating audit
  metrics.py, report.py
configs/            run settings per agent, for the preview and the full set
prompts/            problem prompt, timeout and nudge messages, judge prompts
docker/             sandbox image (tools, Claude Code, Codex, SHELDON) and proxy image
tests/              unit tests (uv run --group dev --group sheldon pytest)
data/, runs/        downloaded benchmark and run outputs (not in git)
```

## Data terms

BioMysteryBench is CC-BY-4.0 and for **evaluation only**: do not use it to train, fine-tune or
distill models. The answer key stays in `data/` on the host and is never given to an agent.
