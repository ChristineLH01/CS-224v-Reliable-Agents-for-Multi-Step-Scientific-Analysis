# Quoted trajectory cases

## Trajectory evidence

**Case 1 — CTCF: shared downloads were actually reused.** In baseline attempt 3, round 1, test h2 downloaded `/workspace/hg38.fa.gz` ([r36](<runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts/problems/recqgsfxqqodhjens/attempt-3/agent_session/sheldon/runs/biomysterybench__sheldon__attempt__bmbench__5f0ef20438/retrievals/problem/r36.json>)). The parallel h3 test tried to read the same file and returned:

```text
gzip: /workspace/hg38.fa.gz: unexpected end of file
```

Source: [baseline h3, r21](<runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts/problems/recqgsfxqqodhjens/attempt-3/agent_session/sheldon/runs/biomysterybench__sheldon__attempt__bmbench__5f0ef20438/retrievals/problem/r21.json>). In v2 attempt 1, round 1, h3 downloaded chromosome 1; h1 then received the same cached path:

```text
Downloaded shared asset: /workspace/analysis/downloads/d7f0121f4ce8b052d3274652d06f89afcad651fe792985f778bc976dc60a2274.gz
Reusing shared download: /workspace/analysis/downloads/d7f0121f4ce8b052d3274652d06f89afcad651fe792985f778bc976dc60a2274.gz
```

Sources: [v2 h3, r31](<runs/adapted_shared_state_v2_remaining_preview_3attempts_20261113/problems/recqgsfxqqodhjens/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__a120391c77/retrievals/problem/r31.json>) and [v2 h1, r33](<runs/adapted_shared_state_v2_remaining_preview_3attempts_20261113/problems/recqgsfxqqodhjens/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__a120391c77/retrievals/problem/r33.json>). This is direct evidence of cross-test download reuse; it does not establish that every resource collision was eliminated. CTCF remained 3/3 in both conditions.

**Case 2 — hb035: a recorded finding became an explicit next-round candidate.** In v2 attempt 2, round 2, the comparison stage submitted this observation (excerpt):

> “found hsa-let-7f-5p covering 360 distinct input genes, hsa-let-7d-5p 356, hsa-let-7b-5p 325, hsa-let-7a-5p 315, and hsa-miR-21-5p 29.”

Source: [round-2 comparison submission](<runs/adapted_shared_state_v2_hb035_3attempts_20261113/problems/hb035/attempt-2/transcript.md:8998>). Round 3 then proposed a let-7f hypothesis, explicitly citing that earlier observation:

> “The prior arm-resolved recount reported 360 distinct input genes for hsa-let-7f-5p, versus 356 for let-7d-5p and 325 for let-7b-5p [o50].”

Source: [round-3 exploration rationale](<runs/adapted_shared_state_v2_hb035_3attempts_20261113/problems/hb035/attempt-2/transcript.md:9305>). The final answer was `hsa-let-7f-5p`. This shows an evidence-to-candidate chain. However, the baseline also passed its let-7f finding into the original observation state; this example does not isolate the added handoff's contribution. Scores changed from 1/3 to 2/3.

**Case 3 — RNA-seq: repeated gene queries remained.** In v2 attempt 1, round 1, exploration queried Ensembl and printed:

```text
ENSG00000178498 DTX3 deltex E3 ubiquitin ligase 3 [Source:HGNC Symbol;Acc:HGNC:24457] protein_coding
```

The later h2 test queried the same gene again and printed:

```text
ENSG00000178498 DTX3 12 57604484 57609804 protein_coding
```

Sources: [exploration, r4](<runs/adapted_shared_state_v2_remaining_full_3attempts_20261113/problems/recaikavdwoimjy3b/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__3bfd5e6876/retrievals/problem/r4.json>) and [h2, r17](<runs/adapted_shared_state_v2_remaining_full_3attempts_20261113/problems/recaikavdwoimjy3b/attempt-1/agent_session/sheldon/runs/biomysterybench__sheldon_adapted__attempt__bmbench__3bfd5e6876/retrievals/problem/r17.json>). The requests used different parameters and returned different fields, so they are not necessarily redundant in purpose. Nevertheless, these direct Python requests bypassed the shared download cache: recording a lookup did not create a general gene-query cache. Both conditions scored 0/3 on this question.

