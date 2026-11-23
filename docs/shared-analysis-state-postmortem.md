# Shared analysis state: diagnosis after the ten-question run

The run does not identify a causal effect of shared state: early_stop and shared_analysis_state were enabled together, there is no contemporaneous baseline or one-adaptation ablation, and each question has only three attempts. The reference is the user's historical table. Repository stage defaults were retained where the document did not specify a value; this is not a verified one-factor reproduction of every original setting.

## Where the three lost scores came from

| Question | Historical formal correct | Current formal correct | Observed difference |
|---|---:|---:|---|
| hb020 | 3/3 | 2/3 | All answers correct; one attempt failed the source-identification audit |
| hb035 | 1/3 | 0/3 | One fewer answer-correct attempt |
| hb010 | 3/3 | 2/3 | One attempt omitted 18S, answering only 16S and ITS |

Every other question has the same number of formal correct attempts as the historical table. Current answer correctness before audit is 15/30, versus 14/30 formal scores. An audit flag is distinct from an incorrect biological answer; it is not evidence the implementation caused the violation.

## Confirmed implementation limitations

1. `AnalysisState.add()` slices every summary at 1200 characters. `SharedLLM.record_submission()` serializes selected structured fields to JSON and sends that string through the slice; this is not semantic summarization. Of 444 captured submissions, 439 were longer than 1200 characters and were truncated. Handoffs can therefore include partial JSON and omit later observations. Complete submissions remain in the on-disk ledger, and SHELDON's original observation state still exists: this is not proof that an omitted handoff item was absent from every prompt.
2. Concrete example: preview hb035 attempt-1, ledger entry a161 (candidate_analysis.h7.1), retains a full observation that the older TargetScan table puts let-7/98 first for 116 genes. The short summary excludes that finding and ends in the middle of a verdict object. hb035 attempt-3 entry a290 similarly excludes a let-7-family result from its short summary. These observations concern families, not a uniquely established correct mature miRNA; omitting them is not proof that preserving them would solve the question.
3. New `record_analysis` calls consume the existing stage tool allowance. There were 286 such calls over 30 attempts. In a 12-call candidate iteration, manual publishing competes with analysis commands. Automatic bookkeeping would avoid this overhead.
4. Only explicitly reported lookups get semantic `lookup` entries (11 total); generic shell lookups remain generic command records. There was just one explicit supersession/correction across the run. There is no general query cache or automatic conflict-resolution system. Shared claims can encourage anchoring, but the present evidence does not establish that mechanism as the cause of any specific wrong answer.
5. Every local iteration gets a new directory, not just each independent test. That increases path-management work between iterations. There were 21 command summaries containing missing-file errors. Some are failed guesses/globs rather than consequences of directory isolation; no causal count has been established.
6. This feature does not change candidate generation or the final-answer decision rule. Consequently it does not address the original failure to propose a concrete correct candidate or the tendency to decline on difficult questions.

## What improved and what did not

The ledger records 4065 actual shell executions (135.5 per attempt), including shell executions inside the download tool. The historical report gives 182 per attempt: a descriptive reduction of 25.5%. The transcript tool counters separately contain 3824 Bash, 249 download_analysis and 286 record_analysis calls; cached downloads do not execute another shell command. Mean solver time was 25.6 minutes versus the reported historical 28.9. These historical comparisons do not isolate causality and do not establish that the same amount of useful analysis was preserved.

Early stop fired only once, on rec5qx7nedrwk4zog attempt-3, whose answer was judged correct. Thus the two answer-correctness losses did not arise from an actual early termination of those attempts; other effects of the extra checks are not isolated.

## Next changes and validation

Replace prefix slicing with a structured, length-bounded handoff that retains explicit findings, contrary evidence, candidates, uncertainty and resolvable artifact references. Keep full records separately. Make routine publication automatic so it does not spend the candidate's analysis budget. Keep a stable directory for a hypothesis test across local iterations, with iteration-specific filenames/subfolders. Add automatic lookup indexing and duplicate/conflicting-result handling without promoting reported claims to verified facts.

Then compare no adaptations, shared state alone and early stop alone under one frozen set of other settings. Start with hb035 and hb010 before spending on another complete batch. This diagnosis did not modify the runtime, rerun solvers or regrade attempts.
