# BioMysteryBench × Claude Code: evaluation harness

This harness runs the **Claude Code agent as shipped** (`claude -p`, with its own tool loop,
context management and subagents) on BioMysteryBench questions. It repeats each question
several times and reports **accuracy and consistency**. It works with the 5-problem
*preview* now and with the 90-problem *full* set later. The sandbox, prompt, grader and
metrics do not depend on the agent, so Codex, Terminus 2 and SHELDON can be added later and
compared under the same conditions.

> **Status: implemented.** I wrote this README as a plan before writing any code, then built
> the harness step by step. Every step in [§3](#3-implementation-plan-step-by-step) carries a
> ✅ once it was implemented and checked. Quickstart: [§7](#7-usage).

---

## 0. What I need from you (prep checklist)

| # | Item | Why | How |
|---|---|---|---|
| 1 | ✅ **Claude Code token for Docker** (found in `.env`; `bmbench doctor --auth-check` passes) | Claude Code runs *inside* a Linux container, and the container cannot read your macOS Keychain login. Your account is a Stanford **enterprise** claude.ai seat. | Run `claude setup-token` in a terminal, then put the token in `.env` as `CLAUDE_CODE_OAUTH_TOKEN=...` (start from `.env.example`). If the org blocks `setup-token`, ask the TA for an `ANTHROPIC_API_KEY` and put that in `.env`. Never commit `.env`. |
| 2 | **Docker Desktop resources** | The default of 8 GB RAM is tight for bioinformatics. | Docker Desktop → Settings → Resources: as many CPUs as you can spare (≥ 6), memory ≥ 12 GB, disk image ≥ 100 GB. macOS may ask whether Docker can access `~/Downloads` (this project lives there). Click *Allow*, or move the project out of `~/Downloads`. |
| 3 | ✅ **Hugging Face access** (full set only; `HF_TOKEN` found in `.env`) | `BioMysteryBench-full` is gated. The preview is public. | Accept the terms at <https://huggingface.co/datasets/Anthropic/BioMysteryBench-full>, create a *read* token, and put it in `.env` as `HF_TOKEN=...`. |
| 4 | **A machine for the full set** | The full data is **155 GB zipped**, and 10 problems are 4–27 GB zipped each (WGS/BAM scale). This laptop has 16 GB RAM and ~178 GB free disk. The preview (11 MB) runs fine here. | Ask the TA whether the lab has a Linux server with Docker. If there is only Slurm plus Apptainer, or bubblewrap (SHELDON's own isolation uses bwrap), tell me and I'll add that runtime (see §9). |
| 5 | ✅ **Usage budget** (decided: `high` effort, 30 min per trial) | 5 problems × 5 trials × Opus 5.5 at `high` effort, up to 30 min each. An enterprise seat has usage limits, and the org has disabled overage, so hitting the limit stops the run. It can be resumed later. | `concurrency: 2`. The progress line shows how much of the 5-hour usage window is used. |
| 6 | **Decisions to confirm with the TA** | These choices change the numbers. | See [§8](#8-defaults-i-chose--questions-for-the-ta). Every one is a config field. |

---

## 1. Design at a glance

```
 host (macOS / Linux)                              one Docker sandbox per trial
 ────────────────────                              ─────────────────────────────────────────
 bmbench run ─┬─ clone task data into a fresh ───► /workspace/data/…   (this task's files only)
              │  trial workspace (copy-on-write)
              ├─ start squid proxy + container ──► claude -p  (Opus 5.5, effort high, Claude Code's 
              ├─ docker exec claude -p < prompt.md      own Bash/Read/Write/Grep/Task/… tools,
              │        ◄── stream-json events ─────     auto-compaction, subagents; WebSearch off)
              ├─ wall-clock limit → kill → one          │ HTTPS_PROXY
              │  "give your best answer now" resume     ▼
              └─ save stream.jsonl, transcript.md,  squid allowlist ─► problem's allowed_domains
                 result.json, network.log,                            (NCBI, Ensembl, UCSC, conda,
                 Claude session files                                  PyPI, …) + api.anthropic.com
 bmbench grade ── judge = claude -p with no tools (same image, same auth)
                  1) answer vs answer_rubric   2) cheating audit of the trajectory  → grade.json
 bmbench report ─ accuracy, pass@k / pass^k, solve-count histogram, answer agreement,
                  cost / turns / time  → metrics.json, report.md, trials.csv
```

---

## 2. Key design decisions

**Claude Code runs unchanged, headless, inside the sandbox.** The image installs the Claude
Code CLI, pinned to the version on the host (`2.1.310`). The harness does exactly four things:
it writes the prompt, starts `claude -p`, enforces a wall-clock limit, and records the
`stream-json` output. It makes no Messages-API calls and runs no custom tool loop. The call:

```bash
timeout -k 30 $T  claude -p --model claude-opus-5-5 --effort high \
    --output-format stream-json --verbose \
    --permission-mode bypassPermissions --disallowedTools WebSearch \
    --session-id <uuid>  < prompt.md
```

**Isolation: the agent must not be able to find the answer key or earlier runs.** I took
several lessons from SHELDON's Codex engine (`sheldon/engines/codex/engine.py`):

- the prompt goes in on stdin
- each call gets its own workspace
- each call gets a fresh agent home, so earlier sessions and their answers are invisible
- the transcript is kept
- the process is killed on timeout
- cost is read from the agent's own accounting

Here, the container mounts only `/workspace`, a fresh copy-on-write clone of *this* task's
files. The answer key, other trials' outputs and the host's `~/.claude` are never mounted.
`problems.csv` contains the answer rubrics and stays on the host. Each trial starts in a new
container, so there are no memories, no CLAUDE.md, no previous sessions, and none of your
account's plugins, skills or MCP connectors. The container runs as a non-root user.

**The network is limited to an allowlist, enforced rather than requested.** Each trial gets an
internal Docker network with no route to the internet. Its only way out is a squid proxy that
allows:

- the problem's `allowed_domains`: conda, NCBI, Ensembl, UCSC, UniProt, Bioconductor, PyPI,
  CRAN, EBI FTP
- the infrastructure domains the agent itself needs: `api.anthropic.com`, and
  `files.pythonhosted.org` (pip downloads wheels from there)

The squid log is saved as `network.log`. Claude Code's **WebSearch is disabled** because it
runs on Anthropic's servers, where the proxy cannot filter it. `network: open` is available
for debugging. *Caveat:* GEO and SRA are hosted on `ncbi.nlm.nih.gov`, which the problems
allow, so the allowlist alone cannot prevent accession lookups. The grader's trajectory audit
catches those.

**The environment is a minimal canonical toolset plus package managers.** This matches the
BioMysteryBench post: "a minimal set of canonical bioinformatics tools" plus `pip`/`conda`.
The image is Debian with miniforge (`conda`/`mamba`) and contains:

- **CLI tools:** samtools, bcftools, htslib, bedtools, minimap2, bwa, bowtie2, STAR/hisat2,
  salmon/kallisto, subread, fastp, FastQC, seqkit, BLAST+, UCSC bigWig tools, …
- **Python:** pandas, scipy, scikit-learn, scanpy, pysam, biopython, pyBigWig, pydeseq2, …
- **R:** DESeq2, edgeR, limma

The exact list ends up in `docker/sandbox/environment.yml` and in `/opt/sandbox/TOOLS.md`
inside the image. The image builds natively for arm64 on this Mac and for amd64 on servers.

**One prompt template for every agent: `prompts/task.md`.** It contains:

- the question
- where the data lives
- CPU/RAM and the time limit
- the allowed domains
- the dataset's rules, verbatim: no GEO/SRA/ENA/BioProject accession lookups; standard
  database use is fine
- the answer format: finish with `<answer>…</answer>`

The template is passed as the user prompt, so Claude Code's own system prompt is untouched.

**How a trial ends.** There are three cases, and each one is recorded:

1. **Normal finish.**
2. **Wall-clock timeout.** The harness kills the run, then sends one `claude -p --resume`
   turn: "time is up; give your best answer now". The trial gets the flag
   `finalized_after_timeout`.
3. **Ended without an `<answer>` tag.** Headless runs sometimes stop while waiting on a
   background job. The harness sends one `--resume` nudge, "continue until you can answer",
   and counts it in `nudges`.

You can switch off both (2) and (3) to get a strict "as is" run.

**Grading: two tool-less `claude -p` judges, using the same auth (no API key needed).** The
v11 rubric is all-or-nothing: *"Score 1.0 if the model did not cheat AND got the answer
correct."*

1. **Correctness.** The judge compares the question, the rubric and the agent's extracted
   answer. It returns `{correct, normalized_answer, rationale}`.
2. **Cheating audit.** The judge reads the action log: every Bash command, every WebFetch URL,
   and `network.log`. Regex pre-flags for accession patterns are added to its input (`GSE…`,
   `GSM…`, `SRR/SRX/SRP…`, `ERR…`, `PRJNA/PRJEB…`, `E-MTAB-…`, `SAMN…`, `db=gds|sra|bioproject`,
   `/geo/`, `ebi.ac.uk/ena`). It returns `{cheated, evidence}`. Recalling facts from memory is
   **not** cheating (CHANGELOG v11). Only actively looking up accessions or reverse-identifying
   the dataset is.

`score = correct ∧ ¬cheated`. Both raw verdicts are kept.

**Resumable and reproducible.** Each run directory holds:

- the resolved config
- a manifest: Claude Code version, image ID, host, start time
- every artifact

Re-running the same command skips finished trials. Trials are interleaved: repeat 1 of every
task runs first, then repeat 2, and so on. An interrupted run is therefore balanced across
tasks. Secrets are scrubbed from saved logs.

---

## 3. Implementation plan (step by step)

Each step ends with a check I run before moving on.

| Step | What gets built | Check |
|---|---|---|
| ✅ **0. Skeleton** | `pyproject.toml` (uv, Python ≥ 3.11, deps: `pyyaml`, `huggingface_hub`), package `bmbench/`, `.gitignore` (`data/`, `runs/`, `.env`), `.env.example`, CLI entry point `bmbench`. | `uv run bmbench --help` |
| ✅ **1. Dataset** (`bmbench/dataset.py`, `bmbench prepare`) | Downloads `problems.csv` and the data zips with `huggingface_hub`: `data.zip` for the preview, `data/<id>.zip` for the full set, selectable with `--ids`. The full set uses `HF_TOKEN`. Extracts safely (no zip-slip, skips `__MACOSX`) to `data/<dataset>/tasks/<id>/` and flattens a single top-level folder. Loads each `Problem(id, question, answer_rubric, allowed_domains, human_solvable)` and keeps the rubric away from everything an agent can reach. | `bmbench prepare --dataset preview` produces 5 task folders. `bmbench tasks` lists them. |
| ✅ **2. Sandbox image** (`docker/sandbox/`) | Dockerfile with a miniforge base, a non-root `agent` user (UID/GID = host user), the conda env from `environment.yml`, Claude Code installed with the native installer at a pinned version, `/opt/sandbox/TOOLS.md`, and `tini` as init. `bmbench build` builds it and records the image ID. | `claude --version` and `samtools --version` work in the image. Python imports succeed. |
| ✅ **3. Egress proxy** (`docker/proxy/`, `bmbench/sandbox.py`) | A small squid image. Per trial: create an internal network, a proxy container with that problem's allowlist (subdomain-deduplicated), and an access log on the host. Agent containers get `HTTP(S)_PROXY`/`http(s)_proxy`, a CPU/memory cap and the `bmbench` label. `bmbench clean` removes leftovers. | `bmbench doctor`: `https://ftp.ncbi.nlm.nih.gov` → 200 through the proxy, `https://www.google.com` → 403 from the proxy, and a direct connection without the proxy has no route. Both requests appear in the access log. |
| ✅ **4. Agent adapter** (`bmbench/agents/base.py`, `claude_code.py`) | An `Agent` interface: `command(timeout_s, session_id, resume, budget_usd)` gives the argv run in the sandbox, with the prompt on stdin, and `parse(stdout, stderr, segment)` fills an agent-agnostic `Segment`. `ClaudeCodeAgent` builds the call shown in §2 and passes auth by name (`-e CLAUDE_CODE_OAUTH_TOKEN`) so the token is not exposed in `ps`. It sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_AUTOUPDATER=1`, and longer Bash timeouts (`BASH_DEFAULT_TIMEOUT_MS=600000`, `BASH_MAX_TIMEOUT_MS=3600000`) so long alignments are not cut off at Claude Code's default 2–10 min. | The unit test checks the argv. `bmbench doctor --auth-check` gets `OK` back from `claude-opus-5-5` through the proxy. |
| ✅ **5. Trajectory parsing** (`bmbench/trajectory.py`) | Parses `stream-json` into `Step`s (text, thinking, tool_use, tool_result, compaction; subagent steps tagged with their parent), plus final text, `<answer>` (last tag wins), result subtype and error, `total_cost_usd`, `num_turns`, `duration_ms`, token usage and `session_id`. Also produces the list of tool calls for the audit and a readable `transcript.md` (thinking, text, tool calls, truncated tool results). | Unit tests on a recorded stream. |
| ✅ **6. Trial runner** (`bmbench/runner.py`, `bmbench run`) | For each (task, repeat): skip it if done (unless `--force`), clone the data (APFS `cp -c` on macOS, `cp --reflink=auto` on Linux, plain copy as fallback), render the prompt, start the proxy and container, `docker exec` Claude, apply the timeout, then the finalize/nudge resume, `docker cp` Claude's session files, write `result.json`, tear down, and delete the cloned data (outputs are kept). Uses asyncio with a `concurrency` semaphore, cleans up on Ctrl-C, writes a manifest, and supports `--tasks`, `--trials` and `--set key=value` overrides. | A real `hb035` × 1 run produced every artifact (see §10). A forced 45 s limit exercised the timeout → kill → `--resume` finalize path. |
| ✅ **7. Grader** (`bmbench/grader.py`, `prompts/grade_*.md`, `bmbench grade`) | The two judges from §2, each run with `claude -p --tools "" --json-schema … --output-format json` in a throwaway container, plus the regex pre-flags. Writes `grade.json` per trial. Can run automatically after each trial (`grader.auto`). `--regrade` reruns it. | Four hand-made `hb035` cases were all graded as expected: right and clean, wrong and cheating, right but cheating (score 0), and hedged (incorrect). The judges cost ≈ $0.02–0.03 per trial. |
| ✅ **8. Metrics + report** (`bmbench/metrics.py`, `report.py`, `bmbench report`) | The metrics in §6, overall and split by `human_solvable`, with a per-task table showing each trial's answer, plus `trials.csv` for plotting. | Unit tests with hand-computed values. |
| ⏳ **9. Preview run** | Smoke test with `--trials 1` on all 5 preview problems, then the main run, 5 × 5. **Ready but not started**, because it spends your usage. | Write up the results for the weekly update. |

Planned for later weeks, not this week: adapters for Codex (`codex exec`), Terminus 2 and
SHELDON, an Apptainer/bwrap runtime, and a trajectory-level consistency analysis (see §9).

---

## 4. Repository layout

```
README.md
pyproject.toml            uv project; console script `bmbench`
.env.example              CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY / HF_TOKEN
configs/
  claude_code_preview.yaml   preview, 5 trials, 30 min per trial, 2 at a time
  claude_code_full.yaml      full set template (longer timeout, more memory)
prompts/
  task.md                 agent prompt (shared by every agent)
  finalize.md, nudge.md   the two resume messages
  grade_answer.md, audit_cheating.md   judge prompts
docker/
  sandbox/Dockerfile, environment.yml, TOOLS.md
  proxy/Dockerfile, squid.conf, entrypoint.sh
bmbench/
  cli.py        prepare | tasks | build | doctor | run | grade | report | show | clean
  config.py     YAML config + dotted overrides
  dataset.py    download / extract / load problems
  sandbox.py    Docker container, network and proxy lifecycle
  agents/       base.py, claude_code.py   (codex.py etc. later)
  trajectory.py stream-json parsing, transcript rendering, action log
  runner.py     trial orchestration, resume, concurrency
  grader.py     judges and regex flags
  metrics.py, report.py
tests/          parsing, metrics, prompt rendering, config, grader helpers (`uv run --group dev pytest`)
data/           (git-ignored) downloaded benchmark; answer key stays here, never mounted
runs/           (git-ignored) outputs
```

## 5. Run outputs

```
runs/<run_name>/
  report.md                  setup line, summary table (overall / human-solvable / human-hard), one row per question
  metrics.json  trials.csv   every metric / one row per attempt
  config.yaml  manifest.json  conda-env.lock.yml
  trials/<task_id>/r<k>/
    prompt.md                  exact prompt sent
    stream.main.jsonl          raw Claude Code stream-json (secrets scrubbed)
    stream.finalize.jsonl      only if the run timed out and was asked for a final answer
    stream.nudge1.jsonl        only if it stopped without an <answer> tag
    stderr.<segment>.log
    trajectory.json            agent-agnostic parsed steps (what the grader reads)
    transcript.md              readable trajectory
    result.json                status, answer, final text, cost, turns, tokens, time, flags
                               (cost_incomplete = a segment was killed before reporting its cost)
    grade.json                 correct, cheated, score, normalized_answer, rationales, regex flags
    network.log                proxy access log (allowed + denied requests)
    agent_session/claude/      Claude Code's own ~/.claude (session JSONL, subagent transcripts)
    workspace/                 files the agent created (input data removed after the run)
```

## 6. Metrics

For each task *i* there are *n* trials, of which *cᵢ* score 1.

- **Accuracy (mean@n):** the mean over tasks of *cᵢ/n*. The 95% CI is a two-level bootstrap
  that resamples tasks, then trials within each task. This matches the BioMysteryBench post,
  which uses 5 trials and resamples within problems.
- **pass@k / pass^k** for k = 1…n, using the unbiased estimators: at least one of *k*
  succeeds, and all *k* succeed. pass^n is the "reliable every time" rate.
- **Solve-count histogram (0/n … n/n):**
  - **reliable:** ≥ 4/5
  - **brittle:** 1–2/5
  - share of solved tasks that are reliable
  These follow the post's definitions.
- **Majority-vote accuracy:** the modal normalized answer per task is graded.
- **Consistency:**
  - **answer agreement:** the share of trials that give the task's modal answer (using
    `normalized_answer`). This is measured whether or not the answers are right.
  - **outcome consistency:** the share of tasks where all trials are right or all are wrong.
  - the number of distinct answers per task.
- **Cost and behavior:** mean and median cost (Claude Code's `total_cost_usd`; on a
  subscription this is an *API-equivalent estimate*), turns, wall time, and tokens. Also the
  timeout, finalize, nudge, error and cheating rates.

Everything is reported overall and split by `human_solvable` (yes/no).

## 7. Usage

```bash
uv sync                                         # install the harness
cp .env.example .env && $EDITOR .env           # token(s) from §0
uv run bmbench prepare --dataset preview        # → data/preview/
uv run bmbench build                            # sandbox + proxy images (first build ~10–20 min)
uv run bmbench doctor                           # docker, image, auth, data, proxy allowlist
uv run bmbench run -c configs/claude_code_preview.yaml --trials 1      # smoke test
uv run bmbench run -c configs/claude_code_preview.yaml                 # 5 × 5, resumable
uv run bmbench grade  -r runs/preview-data_claude-code_opus-5-5_high       # if grader.auto is off
uv run bmbench report -r runs/preview-data_claude-code_opus-5-5_high       # rewrite report.md
uv run bmbench show   -r runs/… hb035 1         # one trial's summary (--full: whole transcript)
uv run bmbench clean                            # remove leftover containers/networks after a crash
```

Useful overrides (any config field works with `--set`):

```bash
uv run bmbench run -c configs/claude_code_preview.yaml --tasks hb035,hb020 --trials 2
uv run bmbench run -c configs/claude_code_preview.yaml --run-name strict_as_is \
    --set agent.finalize_on_timeout=false --set agent.max_nudges=0     # no resume turns at all
uv run bmbench run -c configs/claude_code_preview.yaml --run-name effort_high --set agent.effort=high
uv run bmbench run -c configs/claude_code_preview.yaml --run-name offline \
    --set 'sandbox.override_allowed_domains=[]'                       # API only, no data downloads
```

Status meanings in `result.json`: `completed` (an answer was extracted), `no_answer`, `timeout`,
`agent_error` are final; `harness_error`, `auth_error` and `rate_limited` are retried on the next
`run` with the same name. An auth error or usage-limit hit stops the run early.

## 8. Defaults I chose / questions for the TA

| Setting | Default | Note |
|---|---|---|
| model / effort | `claude-opus-5-5` / `high` | your choice for the preview run (the smoke tests in §10 used `xhigh`) |
| trials per task | 5 | the same as the BioMysteryBench post |
| wall-clock limit | 30 min per trial (preview), plus at most 3 min for the "best answer now" turn; 3 h (full template) | the post does not state one |
| finalize after timeout / nudge if no answer | on / 1 nudge | both flagged per trial; switch off for strict "as is" numbers |
| WebSearch | off | it bypasses the domain allowlist; WebFetch stays, but only reaches allowlisted domains |
| network | `allowlist` (per-problem `allowed_domains` + infra) | `open` is for debugging only |
| container resources | 4 CPUs, 3.5 GB RAM, 2 trials at a time (preview); 16 CPUs, 64 GB (full template) | Docker Desktop here has 8 CPUs / 7.6 GB. Raise it to give each trial more. |
| judge | `claude-opus-5-5` at `medium` effort, no tools | is a different judge model preferred? |
| cost cap | none (`max_budget_usd: null`) | set one if you are on an API key |
| time limit shown to the agent | yes ("about N minutes") | It changes pacing (see §10), so keep `timeout_s` realistic and identical across agents. |
| extra allowed domains | none | `mghp.osn.xsede.org` (where Bioconductor ExperimentHub/AnnotationHub data lives) is not in the dataset's list, so it was blocked. Add it via `sandbox.extra_allowed_domains` if the TA agrees. |

## 9. Extending later

- **Codex / Terminus 2:** add `bmbench/agents/codex.py` (`codex exec --cd /workspace
  --output-last-message …`, following SHELDON's engine) and a Terminus 2 adapter. They reuse
  the same image, prompt, proxy, grader and metrics. Agents that bring their own tools get
  their own row (SHELDON's rule).
- **SHELDON adaptation:** run it as another "agent" inside the same sandbox. Its hypothesis
  and observation state, and the workspace files, persist across rounds in `/workspace`.
- **Other runtimes:** if the full-set machine has no Docker, add an Apptainer or bubblewrap
  `Sandbox` with the same interface.

## 10. Verification so far (2026-10-26, on this Mac)

- **Unit tests:** 27 pass (`uv run --group dev pytest`): stream parsing (including runs with
  several result events), answer extraction, metrics against hand-computed values, zip-slip,
  prompt rendering (no rubric, no real answer as the format example), config overrides, regex
  flags, proxy-log parsing, the proxy alias for long container names, and session token
  recovery.
- **`bmbench doctor --auth-check`:** all checks pass. Docker 28.1.1, arm64 images, Claude Code
  2.1.310 in the image, the 5 preview tasks prepared, the allowlist working (NCBI 200, Google
  denied, no direct route), and `claude-opus-5-5` answering through the proxy with the
  `.env` token.
- **Smoke trial** (hb035 × 1, Opus 5.5 `xhigh`; its run folder has since been deleted):
  - answered `hsa-let-7d-5p`, graded correct (the rubric accepts any let-7 member), no
    cheating
  - 3.9 min, 20 turns, 19 Bash calls, $1.07 API-equivalent estimate
  - the agent fetched miRNA target data via Bioconductor/ExperimentHub and PyPI
  - the proxy denied one request, to `mghp.osn.xsede.org` (see §8)
- **Timeout path** (a separate test run, also deleted): the prompt was edited to claim 30 min while the
  harness killed the run at 45 s (exit 124). The finalize turn ran via `--resume` on the same
  session and returned `hsa-let-7a-5p`, also accepted. That trial's cost shows only the
  finalize turn; this is why `cost_incomplete` and session-based token totals now exist.
- **Observations:**
  - Telling the agent its time budget changes its behavior. With "about 1 minute" it answered
    in 35 s.
  - The container has Claude Code's built-in skills and plugins (as shipped) and none of your
    personal ones.
  - Because the token belongs to the Stanford enterprise seat, the org's managed policy files
    are fetched into the sandbox (`agent_session/claude/policy-limits.json`). Any org-level
    restriction therefore applies to these runs too.
  - stream-json carries only first-chunk usage per message, so token totals come from Claude
    Code's own session files.

## Data terms

BioMysteryBench is CC-BY-4.0 and **for evaluation only**: do not use it to train, fine-tune or
distill models. `data/` and `runs/` are git-ignored, and the answer rubrics never leave `data/`
on the host.
