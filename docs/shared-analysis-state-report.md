# Shared Analysis State for SHELDON: Implementation and Results

We added shared analysis state to the early-stop SHELDON framework to reduce repeated work and improve coordination across stages and rounds. We compared early-stop SHELDON with and without shared analysis state on the same 10 BioMysteryBench questions, with three attempts per question (30 attempts per condition). Both conditions already include early stopping; shared analysis state is the added adaptation.

## Implementation

- **Shared resource cache:** stages and tests can reuse downloaded references. Requests through `download_analysis` are cached by URL and synchronized to prevent concurrent duplicate downloads; failed downloads are not cached as successful.
- **Structured handoffs:** complete stage submissions and individual observations are retained in an attempt-local ledger. Later stages receive a bounded selection of findings, candidates and source references. Excerpts, omissions and conflicting verdicts are explicitly marked.
- **Automatic recording:** findings are extracted from normal stage submissions, avoiding additional bookkeeping tool calls. Shell HTTP lookups are logged, although arbitrary queries are not automatically cached.
- **Separate test directories:** parallel tests have separate working directories, while each test retains its directory across local iterations and claim revisions.

The original hypothesis-generation and final-answer policies remain unchanged. SHELDON already shared hypotheses, observations and retrieval records; this extension adds resource coordination and an additional handoff mechanism.

## Results

Both conditions used GPT-6-luna at high effort, up to three rounds, a 60-minute search limit, and 4 CPUs/3.5 GB RAM per attempt. Claude Opus 5.5 graded answers and audited source-dataset lookups.

| Metric | Early-stop SHELDON* | Early-stop SHELDON + shared state |
|---|---:|---:|
| Accuracy | 17/30 (56.7%) | 17/30 (56.7%) |
| Mean time per attempt | 28.9 min | 20.0 min |
| Mean actual shell executions | 181.5 | 150.0 |
| Commands explicitly accessing prior records | 2,938 | 1,926 |

Observed mean time fell by approximately 31%, and actual shell executions by 17%. Shell counts include commands executed inside the download helper. Record-access counts use explicit path matching; they do not imply that every such read was unnecessary.

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

## Trajectory evidence

Raw run directories referenced below are local artifacts and are not included in this source snapshot. Quoted outputs and aggregate measurements are included for review.

**Case 1 — CTCF: shared downloads were actually reused.** In baseline attempt 3, round 1, test h2 downloaded `/workspace/hg38.fa.gz` (r36 (local artifact: `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts/problems/recqgsfxqqodhjens/attempt-3/agent_session/sheldon/runs/biomysterybench__sheldon__attempt__bmbench__5f0ef20438/retrievals/problem/r36.json`)). The parallel h3 test tried to read the same file and returned:

```text
gzip: /workspace/hg38.fa.gz: unexpected end of file
```

Source: baseline h3, r21 (local artifact: `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts/problems/recqgsfxqqodhjens/attempt-3/agent_session/sheldon/runs/biomysterybench__sheldon__attempt__bmbench__5f0ef20438/retrievals/problem/r21.json`). In the shared-state condition, attempt 1, round 1, h3 downloaded chromosome 1; h1 then received the same cached path:

```text
Downloaded shared asset: /workspace/analysis/downloads/d7f0121f4ce8b052d3274652d06f89afcad651fe792985f778bc976dc60a2274.gz
Reusing shared download: /workspace/analysis/downloads/d7f0121f4ce8b052d3274652d06f89afcad651fe792985f778bc976dc60a2274.gz
```

Sources: shared-state h3, r31 (local artifact: `runs/adapted_shared_state_v2_remaining_preview_3attempts_20261113/problems/recqgsfxqqodhjens/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__a120391c77/retrievals/problem/r31.json`) and shared-state h1, r33 (local artifact: `runs/adapted_shared_state_v2_remaining_preview_3attempts_20261113/problems/recqgsfxqqodhjens/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__a120391c77/retrievals/problem/r33.json`). This is direct evidence of cross-test download reuse; it does not establish that every resource collision was eliminated. CTCF remained 3/3 in both conditions.

**Case 2 — hb035: a recorded finding became an explicit next-round candidate.** In the shared-state condition, attempt 2, round 2, the comparison stage submitted this observation (excerpt):

> “found hsa-let-7f-5p covering 360 distinct input genes, hsa-let-7d-5p 356, hsa-let-7b-5p 325, hsa-let-7a-5p 315, and hsa-miR-21-5p 29.”

Source: round-2 comparison submission (local artifact: `runs/adapted_shared_state_v2_hb035_3attempts_20261113/problems/hb035/attempt-2/transcript.md:8998`). Round 3 then proposed a let-7f hypothesis, explicitly citing that earlier observation:

> “The prior arm-resolved recount reported 360 distinct input genes for hsa-let-7f-5p, versus 356 for let-7d-5p and 325 for let-7b-5p [o50].”

Source: round-3 exploration rationale (local artifact: `runs/adapted_shared_state_v2_hb035_3attempts_20261113/problems/hb035/attempt-2/transcript.md:9305`). The final answer was `hsa-let-7f-5p`. This shows an evidence-to-candidate chain. However, the baseline also passed its let-7f finding into the original observation state; this example does not isolate the added handoff's contribution. Scores changed from 1/3 to 2/3.

**Case 3 — RNA-seq: repeated gene queries remained.** In the shared-state condition, attempt 1, round 1, exploration queried Ensembl and printed:

```text
ENSG00000178498 DTX3 deltex E3 ubiquitin ligase 3 [Source:HGNC Symbol;Acc:HGNC:24457] protein_coding
```

The later h2 test queried the same gene again and printed:

```text
ENSG00000178498 DTX3 12 57604484 57609804 protein_coding
```

Sources: exploration, r4 (local artifact: `runs/adapted_shared_state_v2_remaining_full_3attempts_20261113/problems/recaikavdwoimjy3b/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__3bfd5e6876/retrievals/problem/r4.json`) and h2, r17 (local artifact: `runs/adapted_shared_state_v2_remaining_full_3attempts_20261113/problems/recaikavdwoimjy3b/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__3bfd5e6876/retrievals/problem/r17.json`). The requests used different parameters and returned different fields, so they are not necessarily redundant in purpose. Nevertheless, these direct Python requests bypassed the shared download cache: recording a lookup did not create a general gene-query cache. Both conditions scored 0/3 on this question.

## Interpretation

The strongest direct evidence supports resource reuse and file coordination. The run was faster with unchanged overall accuracy, but it does not establish an independent benefit from the additional summary layer. The shared-state condition still made many record-reading calls, and one hb002 failure was an API interruption rather than an incorrect biological conclusion.

These are historical, independently sampled runs, not a controlled ablation. The baseline's early-stop status is supplied by the user and is not explicitly marked in its logs. The shared-state condition explicitly caps shell output at 8,000 characters; the baseline configuration does not explicitly specify the cap. A focused next comparison should retain caching and directory isolation while toggling handoff injection.
