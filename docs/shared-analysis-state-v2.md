# Shared analysis state v2 (runtime 0.2.0)

This revision addresses the implementation limitations found in the completed ten-question experiment. It changes no historical predictions or grades, and no paid solver run was started to validate it.

- Preserve complete submissions and observation text in the version-2 ledger. Split observations, candidate statements and notes into distinct records instead of prefix-cutting serialized JSON at 1200 characters.
- Pass complete, valid JSON records in a bounded handoff. Prioritize individual observations, balance coverage across tests, label long text as excerpts, retain both text ends, and report all omitted record/reference counts. Ledger pointers identify the full records. A bounded handoff still cannot guarantee every finding appears; it makes that limitation explicit.
- Automatically collect normal submit_* responses and shell HTTP lookups. Remove record_analysis from the offered tools, so bookkeeping spends no stage tool calls and uses no extra model completion.
- Mark model conclusions as reported/proposed and command/download interpretations as unverified. Surface opposing support/refutation verdicts as an unresolved conflict; a successful process is not a verified scientific conclusion.
- Keep a hypothesis test's working directory stable across its local iterations and revisions. Isolate other tests and rounds; use distinct filenames when preserving versions.

Validation: the full offline suite passed (116 tests), and all 15 shared-state tests passed again after the final handoff changes. Tests cover session/staged execution, early-stop composition, automatic recording, stable directories and revisions, parallel isolation, budgets, explicit excerpts/omissions, opposing verdicts and download deduplication. An offline replay of the old hb035 attempt-1 entry a161 retains its complete observations and puts the previously excluded let-7/98 (116 genes) finding in the handoff. That replay checks serialization/selection, not answer correctness or causal effect.

The existing Docker image remains the runtime 0.1.0 artifact used in the original experiment. Before another experiment, rebuild using a separate tag, for example:

```bash
uv run bmbench build --only sandbox --set sandbox.image=bmbench-sandbox:shared-state-v2
```

Use the new image tag and new run names in all v2 comparisons. The old batch supervisor intentionally refuses to resume with changed implementation hashes. Compare baseline against shared-state-only with identical other settings and the same newly built image; leave early stop off for that comparison. Begin with hb035 (preview) and hb010 (full), three attempts per condition: 12 solver attempts total. No accuracy improvement has been established yet.
