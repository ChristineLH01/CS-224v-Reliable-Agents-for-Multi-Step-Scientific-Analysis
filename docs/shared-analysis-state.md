# Shared analysis state

The `shared_analysis_state` adaptation extends SHELDON's existing hypothesis, observation and retrieval records with an attempt-local analysis ledger, bounded structured handoffs, a URL download cache and separate working directories for parallel tests. It can be enabled independently or together with `early_stop`.

```bash
uv run bmbench build
uv run bmbench run -c configs/sheldon_preview.yaml --set 'agent.adaptations=[shared_analysis_state]'
uv run bmbench run -c configs/sheldon_adapted_preview.yaml
```

The adapted preview/full configs use `[all]`, which now enables both adaptations. Baseline configs still run unmodified SHELDON. Runtime 0.2.0 requires the toolloop engine and supports staged/session modes.

Complete submissions and individual observations are retained in `analysis/state.json`. Handoffs preserve provenance, label excerpts/omissions and report conflicting verdicts without treating successful commands as verified scientific findings. Routine recording is automatic and consumes no extra model or bookkeeping tool call; handoff text still adds input tokens.

Each test retains a directory across local iterations and claim revisions, while different tests and rounds remain separate. Inputs are accessible through data/ and absolute paths. This prevents accidental relative-path collisions, but is not a filesystem access restriction. `download_analysis` caches successful HTTP(S) downloads by exact URL and serializes concurrent requests; arbitrary shell/Python queries do not automatically use that cache.

The ledger and summary are mirrored into `agent_session/sheldon/analysis/` before workspace cleanup. Raw test artifacts follow `sandbox.keep_workspace`. No analysis state is shared across independent attempts.

Validation: `uv run --group dev --group sheldon pytest`. Regression coverage includes parallel isolation, stable directories, claim revisions, bounded complete JSON handoffs, opposing verdicts, automatic recording and download deduplication.

The ten-question [experiment report](shared-analysis-state-report.md) records unchanged overall accuracy and lower observed shell work/time compared with the user-labelled early-stop baseline. These historical comparisons do not isolate the summary layer's contribution. Stage measurements are in `trajectory-audit/stage-work-per-attempt.csv`; `scripts/summarize_stage_work.py` regenerates them when the referenced local run artifacts are present.

The dated configs and batch supervisors preserve experimental provenance. Use a new run name/batch directory for a changed implementation. `docker/sandbox/Dockerfile.runtime` optionally layers the new runtime over a tagged local base image to retain exact analysis dependencies.
