# SHELDON runtime

BioMysteryBench experiments with a Codex CLI baseline and an adapted copy of
[Stanford SHELDON](https://github.com/stanford-oval/sheldon). This repository
contains the implementation, configuration, tests and progress documentation;
datasets, credentials and experiment outputs are kept local.

## SHELDON: additions and compatibility improvements

`upstream-sheldon/` contains the upstream source plus our local adaptations,
based on upstream commit `7fbf027d1fb414443612d4ad2661b396c8352cd3`.
The original MIT license and attribution are retained in that directory.
It is included as ordinary source files so cloning this repository also gets
our modifications; it is not a Git submodule.

| Change | Purpose |
|---|---|
| BioMysteryBench adapter | Load the five preview questions and seed each task workspace with its biological data |
| Opt-in `openai-responses` provider | Support native tools and explicit reasoning effort on the Stanford model proxy |
| Responses history preservation | Replay full response items, including encrypted reasoning, with tool results |
| Streaming and strict structured output | Address observed proxy response-size and output-format issues; validate results with upstream Pydantic models |
| Required submission handling | Supply the `submit_*` tool explicitly requested by a schema-backed closing prompt, even after search budget exhaustion |
| Local environment context | Tell the solver the Python path, available input filenames and macOS command constraints |
| Diagnostic runner | Print compact progress, enforce a process-group deadline and report task errors even when the upstream CLI exits 0 |
| Regression checks | Test data access, answer isolation, response translation, structured submissions and timeouts |

**Scope:** SHELDON's hypothesis/observation search algorithm, stage prompt
templates and engine implementations are unchanged. The model communication
layer and benchmark interface are modified. These are integration and
reliability changes, not evidence of improved scientific accuracy. Do not
describe this as an entirely unmodified copy of SHELDON.

An automatic judge was explored during development and has been removed.
The current pipeline saves answers and traces without post-answer judge calls.
The upstream framework still requires a score hook; its `not_evaluated` zero
is a compatibility placeholder, not an accuracy result.

## Run SHELDON on BioMysteryBench

```bash
conda create -n sheldon-runtime -c conda-forge python=3.11 pip -y
conda activate sheldon-runtime
pip install -r requirements.txt
conda install -c conda-forge -c bioconda biopython samtools bedtools blast -y

# Download the official preview and prepare local inputs.
python baseline/prepare.py

cd upstream-sheldon
pip install -e .
cp .env.example .env
```

Edit `.env` locally with your own credentials. For the Stanford proxy, set
`OPENAI_BASE_URL=https://models.genie.stanford.edu/v1` and your
`OPENAI_API_KEY`. Never commit or share the populated file. Model aliases and
access depend on your endpoint/account; the checked configuration uses
`gpt-5.6-terra`.

```bash
python scripts/prepare_biomysterybench.py
python -m unittest discover -s tests -v
sheldon smoke

# One-question diagnostic: separate short budget and 10-minute hard deadline.
python scripts/run_biomysterybench_pilot.py

# Five questions, one attempt per question; no automatic judge.
sheldon run \
  -c configs/experiments/biomysterybench_preview.yaml \
  --model gpt-5.6-terra \
  --tag terra-preview-trial1 \
  --echo
```

The preview config allows up to 3 rounds, 40 search model calls, 30 minutes and
$2 estimated solver cost per task, with one task in flight. These are harness
budgets rather than hard billing/process limits; a required closing call can
exceed the search-call budget. The diagnostic wrapper additionally enforces a
600-second local process deadline. See [adapter instructions](upstream-sheldon/BIOMYSTERYBENCH.md).

Answers, errors, usage and timing are in `runs/<run-id>/predictions.jsonl`;
traces are under `runs/<run-id>/traces/`. Use a fresh tag for an independent
trial. Reusing a tag can resume an existing run. Inspect each prediction's
`error` and `answer`; process exit status alone is not sufficient.

**Validation:** one full hb020 diagnostic completed in about 107 seconds with
a nonempty answer. This is not a five-question result or a guarantee of
repeated-run stability. Network restrictions are currently prompt instructions;
local filesystem access is not isolated by default. Formal comparisons with
the Claude Code Docker experiment need aligned environments and enforced
network rules.

Development history and known limitations: [progress report](docs/progress-report.md)
and [compatibility diagnosis](docs/sheldon-compatibility-diagnosis.md).

## Codex CLI baseline

The baseline is a preliminary local run, separate from the isolated Docker
evaluation used in the Claude Code experiment.

## Environment

```bash
conda create -n sheldon-runtime -c conda-forge python=3.11 pip -y
conda activate sheldon-runtime
pip install -r requirements.txt
conda install -c conda-forge -c bioconda biopython samtools bedtools blast -y
```

Install and authenticate Codex CLI separately. `baseline/run.py` searches PATH
and, on macOS, the VS Code Codex extension. Use `--codex /path/to/codex` or
`CODEX_BIN` to override the executable.

## Prepare and run

```bash
python baseline/prepare.py
python baseline/run.py hb020 --dry-run
python baseline/run.py hb020 --model YOUR_MODEL_ID --timeout 1800
python baseline/run.py all --model YOUR_MODEL_ID --timeout 1800
```

Replace `YOUR_MODEL_ID` with the model available to your account. `all` skips
previous trials with exit code 0. Existing results are protected against
overwrite; preserve them before rerunning a question.

Each task directory contains `answer.md`, `events.jsonl`, `stderr.log`, and
`status.json`. Exit code 0 means the CLI exited successfully, not that its answer
is correct. Grade separately against `answer_rubric` after all solving is done.

The current runner uses a shared local environment. Allowed domains and data
access restrictions are instructions, not enforced isolation. The runner does
not explicitly disable web search or fix reasoning effort. Do not claim exact
comparability with a containerized, network-restricted experiment.

## Prepare comparison inputs

```bash
python baseline/prepare_comparison.py
```

This creates clean inputs for five trials per question and a target experiment
manifest. It does not execute trials. Model, container image, prompts, judge,
resource limits, and network enforcement still need to be resolved.

## Files excluded from Git

Dataset downloads, extracted biological data, solver trajectories, answers,
local result reports, generated experiment manifests, environment files, and
credentials are excluded. Run preparation scripts on each machine to regenerate
local inputs. Never commit API keys or Codex authentication files.

Dataset and rules: https://huggingface.co/datasets/Anthropic/BioMysteryBench-preview
