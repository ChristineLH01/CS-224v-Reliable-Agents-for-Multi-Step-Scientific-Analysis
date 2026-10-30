# SHELDON runtime

Local Codex CLI runner for the five official BioMysteryBench preview tasks.
This is a preliminary local baseline, not the isolated Docker evaluation used
in the Claude Code experiment.

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
