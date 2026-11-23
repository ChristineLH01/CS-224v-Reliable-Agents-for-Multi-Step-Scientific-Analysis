# Adapted SHELDON: ten-question experiment

This experiment reproduces the supplied ten-question setup with the two implemented adaptations: `early_stop` and `shared_analysis_state`. It does not implement the proposed pre-round data scan or the proposed final-answer policy. It uses the existing hypothesis/verdict-based early-stop gate, not a new exhaustive candidate scan.

| Setting | Value |
|---|---|
| Preview questions | hb002, hb020, recqgsfxqqodhjens, hb035, hb024 |
| Full questions | hb048, rec5qx7nedrwk4zog, hb010, recaikavdwoimjy3b, recsvhviava5okg19 |
| Attempts | 3 per question, 30 total; independent fresh containers |
| Solver | gpt-6-luna, high effort, all stages; Responses API |
| SHELDON | 7fbf027; toolloop engine, session mode |
| Adaptations | early_stop + shared_analysis_state, explicitly named |
| Search limits | 3 rounds or 3600 seconds; no total call/cost cap |
| Finalization | Up to 300 additional seconds after the time limit |
| Hypotheses | 3 initial, 3 explored each later round, select at most 3 |
| Candidate testing | Concurrent, up to 3 iterations each, 12 tool calls per iteration |
| Other stage tool budgets | Explore 20, select 2, compare 10, exploit 4, finalize 20 |
| Model output budget | 16000 tokens per call, reasoning included |
| Shell output cap | 8000 characters of command stdout/stderr; wrapper truncation notices/metadata may add text |
| Shared handoff | 10000-character history budget; no extra summarization model call |
| CPU / RAM | 4 CPUs / 3.5 GB per attempt |
| Attempt concurrency | 2; preview and full groups run sequentially |
| Network | Domain allowlist; built-in web search disabled |
| Judge | Deferred by user request; intended claude-opus-5-5, medium effort |
| Input retention | Keep prepared problem data, avoiding repeated downloads |
| Workspace retention | Default cleanup; shared ledger and summary retained in agent_session |

The supplied document specifies the candidate-test budget but not every other stage budget; explore/finalize retain the repository's existing 20-call settings. The document's explicit 8000-character shell cap is set in these experiment configs rather than the image default of 40000. Input retention affects disk use only. The dedicated run names prevent mixing these runs with the earlier single-question smoke test.

Configs: `configs/sheldon_adapted_10q_preview.yaml`, `configs/sheldon_adapted_10q_full.yaml`.

Start/resume the batch with `.venv/bin/python scripts/run_sheldon_10q.py`. The supervisor waits for any existing bmbench attempts before launching the two groups, never kills those existing attempts, and prevents duplicate supervisors with a file lock. Completed attempts are skipped on resume. Configuration snapshots, image IDs and implementation SHA256 hashes are saved in the batch directory, without credentials or answer keys.

Batch status: `runs/adapted_10q_batch_20261112/status.json`.
Logs: `runs/adapted_10q_batch_20261112/{supervisor,preview,full}.log`.
Run results: `runs/adapted_10q_{preview,full}_3attempts_shell8000_20261112/`.

Grading can be added later using `bmbench grade -r <run-directory>` once Claude authentication is configured. Ungraded runs do not establish accuracy or pass@3/pass^3. The published answer keys and reference results in the supplied document are not included in solver prompts, configs or inputs.
