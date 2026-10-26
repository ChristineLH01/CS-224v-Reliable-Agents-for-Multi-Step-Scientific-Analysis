# Reliable Agents for Multi-Step Scientific Analysis: BioMysteryBench

`bmbench` is an evaluation harness that runs coding agents, unchanged, on
[BioMysteryBench](https://www.anthropic.com/research/Evaluating-Claude-For-Bioinformatics-With-BioMysteryBench).
It repeats each question several times and measures **accuracy** and **consistency**.

BioMysteryBench questions are written by bioinformatics experts. Metadata was removed from real
datasets, and the agent must recover it through analysis, e.g. "Which gene was knocked out in
these RNA-seq samples?". The answers are short, but reaching them takes many analysis steps.

| Dataset | Questions | Access |
|---|---|---|
| preview | 5 | public |
| full | 90 (155 GB zipped) | gated on Hugging Face |

The harness currently runs the **Claude Code CLI** (`claude -p`). The sandbox, prompt, judge and
metrics do not depend on the agent, so other agents (Codex, Terminus 2, SHELDON) can be added
and compared under the same conditions.

## Setup

| Part | Setting |
|---|---|
| Agent | Claude Code, headless and unchanged: its own tools, context management and subagents. Model `claude-opus-5-5`, effort `high`, web search off. |
| Sandbox | A fresh Docker container per trial, with standard bioinformatics tools (samtools, BLAST+, HOMER, DESeq2, scanpy, …). The agent can install more with conda or pip. Only the question's data is mounted; the answer key never is. |
| Network | Through a proxy that only allows the question's listed sites (NCBI, Ensembl, UCSC, conda, PyPI, …) and the model API. |
| Limits | 1 hour per trial. 4 CPUs / 3.5 GB RAM (preview), 16 CPUs / 64 GB (full). After a timeout, the agent gets one turn to give its best answer. |
| Trials | 5 per question. Questions run one at a time: each question's data is downloaded before its trials and deleted after them. |
| Grading | A GPT-6 Sol judge (through the Stanford LiteLLM proxy) checks (1) the answer against the answer key and (2) the trajectory for cheating, i.e. looking up the source dataset (GEO/SRA accessions, the original paper). A trial scores 1 only if it is correct and did not cheat. |

## Metrics

Each question weighs the same. With *n* trials per question (default 5):

| | Metric | Meaning |
|---|---|---|
| Accuracy | **accuracy** | share of correct trials, averaged over questions |
| | **pass@n** | share of questions solved in at least one trial |
| Consistency | **pass^n** | share of questions solved in every trial |
| | **answer agreement** | share of a question's trials that give its most common answer, right or wrong |
| | **correct-trials histogram** | how many questions were solved 5/5, 4/5, … 0/5 |
| Cost | **mean cost, mean time** | per trial; cost is Claude Code's API-price estimate |

The report also lists the trials that timed out or were flagged for cheating. All metrics are
given overall and split into human-solvable and human-hard questions.

## Usage

**Prerequisites**

- Docker (Docker Desktop on macOS) and [uv](https://docs.astral.sh/uv/)
- In `.env` (copy it from `.env.example`):
  - `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`
  - `LITELLM_API_KEY` for the judge
  - `HF_TOKEN`, for the full set only (accept its terms on Hugging Face first)

**Commands**

```bash
uv sync                                   # install
cp .env.example .env                      # then fill in the keys
uv run bmbench build                      # build the sandbox and proxy images (once)
uv run bmbench doctor --auth-check        # check Docker, keys, judge and network

uv run bmbench run -c configs/claude_code_preview.yaml   # 5 questions × 5 trials
uv run bmbench run -c configs/claude_code_full.yaml      # 90 questions × 5 trials

uv run bmbench report -r runs/<run_name>                 # print the report
uv run bmbench show   -r runs/<run_name> <question> <k>  # one trial (--full: whole transcript)
```

- **Resuming:** re-running the same command skips finished trials. If a usage limit stops the
  run, run the same command again later.
- **Changing settings:** any config field can be overridden, e.g.
  `--tasks hb024 --run-name hb024_2h --set sandbox.timeout_s=7200` re-runs one question with a
  2-hour limit.

**Outputs** (`runs/<run_name>/`)

| File | Content |
|---|---|
| `report.md` | setup, summary table, one row per question |
| `metrics.json`, `trials.csv` | all metrics; one row per trial |
| `trials/<question>/r<k>/` | prompt, transcript, raw agent output, result, grade, network log |

## Repository layout

```
bmbench/            the harness
  cli.py              commands: prepare, tasks, build, doctor, run, grade, report, show, clean
  config.py           run configuration (YAML + --set overrides)
  dataset.py          download and load BioMysteryBench
  sandbox.py          Docker container, network and proxy per trial
  agents/             agent adapters (claude_code.py)
  runner.py           runs the trials
  trajectory.py       parses the agent's output into steps and transcripts
  grader.py           the judge: answer check and cheating audit
  metrics.py, report.py
configs/            run settings for the preview and the full set
prompts/            task prompt, timeout/nudge messages, judge prompts
docker/             sandbox image (tools, Claude Code) and proxy image
tests/              unit tests (uv run --group dev pytest)
data/, runs/        downloaded benchmark and run outputs (not in git)
```

## Data terms

BioMysteryBench is CC-BY-4.0 and for **evaluation only**: do not use it to train, fine-tune or
distill models. The answer key stays in `data/` on the host and is never given to an agent.
