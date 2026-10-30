# BioMysteryBench preview adapter

This adds a benchmark interface and configuration. SHELDON's method, stage
prompts, search logic and engines are unchanged. A new opt-in transport provider,
`openai-responses`, has been added; this is a source change to the model backend,
not an exact reproduction of the entire upstream package. The current config
uses upstream's native `toolloop` with Responses, not Codex CLI or ReAct.
It needs model API credentials in `.env` and explicitly sends reasoning effort.
Responses output items (including encrypted reasoning) are preserved across
tool-result turns. It uses store=false, no SDK retries, and a default 120-second
total deadline per request (`SHELDON_RESPONSES_TIMEOUT`). Harness retries default
to one attempt for this provider, configurable with SHELDON_RETRY_ATTEMPTS.
Responses now uses streaming by default. A finalization-only replay completed
with streaming after the same non-streaming request hit the proxy's response
size limit. The full terminal response (including reasoning items) is preserved.
Set SHELDON_RESPONSES_STREAM=0 only to diagnose the non-streaming path. This
transport test produced an incorrect species answer; it is not a successful
benchmark trial. The diagnostic wrapper explicitly enables streaming.
After a fresh streaming trial still timed out, a strict JSON-schema replay
returned in 28.52 seconds. Structured-output requests now use SDK-normalized
strict schemas with defaults removed on the wire; upstream Pydantic models and
stage prompts remain unchanged. The config records a transport revision so
new runs are distinguishable. Native function tools retain their original
optional-field schemas. This is a model-interface adaptation, not an exact
copy of the upstream communication code.
When a schema-backed completion's system prompt explicitly asks to call a
`submit_*` tool, the transport offers exactly that submission tool and decodes
its arguments into the original Pydantic model. This also covers required
finalization after the search budget is spent; no analysis tools are added.
An isolated finalization diagnostic using this protocol returned in 23.11s.
Further end-to-end validation is needed before claiming stable performance.
One fresh full diagnostic subsequently completed hb020 in 107.161 seconds,
16 calls and $0.193819 estimated usage, with a nonempty low-confidence answer
matching the rubric text. No independent judge/audit was run. This establishes
one end-to-end completion, not repeated-run stability or a benchmark score.

From this directory, with the `sheldon-runtime` Conda environment active:

```bash
pip install -e .
python scripts/prepare_biomysterybench.py
python -m unittest discover -s tests -p 'test_biomysterybench.py' -v
sheldon run -c configs/experiments/biomysterybench_preview.yaml --dry-run
```

Preparation imports `../baseline/source/problems.csv` and the task directories
under `../baseline/extracted`. Use `--baseline /path/to/baseline` for another
location. It refuses to overwrite existing data. The imported CSV contains gold
rubrics and must never be mounted in an agent's isolated workspace.

## One-question pilot

For the current macOS/proxy diagnostic, start with:

```bash
python scripts/run_biomysterybench_pilot.py
```

This uses a separate one-round native-tool debug config, prints compact event progress and
a heartbeat every 30 seconds, and terminates the local process group after 600
seconds. It preserves logs and uses a new tag each time. Total Responses request
deadline is 90 seconds, with SDK retries disabled. The
$0.50 solver estimate is not a hard billing cap, and
server-side processing may continue after cancellation. It is a wiring check,
not the configuration for reporting benchmark performance. Public file names,
runtime Python path and macOS command compatibility are included in task input.

The configured model is gpt-5.6-terra. To select a different model, supply `--model`:

```bash
sheldon run -c configs/experiments/biomysterybench_preview.yaml \
  --model YOUR_MODEL_ID --task hb020 --tag bmb-hb020-pilot
sheldon report
```

Defaults: staged SHELDON, up to 3 rounds, 40 model calls, $2 estimated solver
cost, 1800 seconds, one task at a time. The first binding budget stops search.
Time and cost are harness budgets, not strict external process/spend limits;
an in-flight call may overrun. Unknown model prices can make dollar accounting
inaccurate. API keys and endpoint URLs stay in `.env`, never in committed config.
There are no automatic judge calls.

The only configured generic probe is `run_shell`; hosted web search/fetch probes
are not provided. Conda must be activated so tools are on PATH. Commands may
query allowed biological databases. Actual domain filtering is NOT implemented
by this adapter; prompt instructions are not a network boundary.

## Outputs

Automatic judge grading has been removed. Runs save final answers, errors,
usage and traces. They do not make post-answer judge calls or audit trajectories.
The loader does not attach answer rubrics to Task objects.

The upstream framework requires score(), so the adapter retains a no-op
compatibility method. scores.jsonl has status=not_evaluated and a numeric zero
placeholder; it is not an accuracy score. Inspect predictions.jsonl for outputs.
Do not pass judge_model or judge_max_chars; they no longer enable any feature.
Old trial artifacts are preserved and may contain earlier grading statuses.

Run all five preview questions without a judge:

```bash
sheldon run -c configs/experiments/biomysterybench_preview.yaml \
  --model gpt-5.6-terra --tag terra-preview-answers --echo
```

## Isolation and repeated trials

`isolate: false` is for local debugging on macOS. Shell commands can read host
files, including keys or rubrics if they choose to; do not treat this as a strict
benchmark result. Linux `-p benchmark_opts.isolate=true` requests bubblewrap and
fails if unavailable; required Conda binaries/libraries may need explicit mounts.
Bubblewrap here does NOT enforce allowed network domains. Use an externally
restricted container/proxy for the formal comparison.

For five independent trials, use fresh tags/workspaces, keep `cache: false`,
and ensure each new tag is unused. A rerun with the same tag resumes prior work.
Never include previous answers, grading reports or inspected answer keys in
solver prompts. Full-set support is intentionally not claimed by this adapter.
