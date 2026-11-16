## Work by stage

Both conditions include early stopping. Values are means over all 30 attempts; shares use total actual shell executions. Hypotheses count accepted additions: proposed by exploration, revised during testing, or refined during revision. Observations count native accepted observations, not shared-ledger copies.

**Early-stop SHELDON**

| Stage | Hypotheses | Shell commands (share) | Observations |
|---|---:|---:|---:|
| Propose and select | 9.0 proposed | 12.0 (6.6%) | 0.0 |
| Test | 1.7 revised | 150.5 (82.9%) | 38.7 |
| Compare and revise | 4.4 refined | 18.7 (10.3%) | 7.6 |
| Finalize | — | 0.3 (0.2%) | 0.0 |
| Total | 15.1 | 181.5 (100%) | 46.3 |

**Early-stop SHELDON + shared state**

| Stage | Hypotheses | Shell commands (share) | Observations |
|---|---:|---:|---:|
| Propose and select | 8.4 proposed | 11.3 (7.5%) | 0.0 |
| Test | 2.1 revised | 124.6 (83.1%) | 36.5 |
| Compare and revise | 4.7 refined | 13.2 (8.8%) | 10.2 |
| Finalize | — | 0.8 (0.6%) | 0.0 |
| Total | 15.2 | 150.0 (100%) | 46.6 |

Finalize is shown separately because it executed 9 shell commands in the baseline and 25 in the shared-state condition; omitting it would make the totals inconsistent. Means and percentages are rounded independently. Partial work from the shared-state attempt interrupted by an API error is included; final snapshots alone would omit its 12 already accepted observations.

Testing remains approximately 83% of shell work. Shared state reduced absolute shell executions across all three main stages, while comparison-stage observations rose from 7.6 to 10.2 per attempt. Total native observations stayed close (46.3 vs 46.6); this does not establish equal or improved evidence quality. Lower hypothesis/round totals also reflect different stopping and the interrupted attempt, rather than a changed per-round proposal setting.
