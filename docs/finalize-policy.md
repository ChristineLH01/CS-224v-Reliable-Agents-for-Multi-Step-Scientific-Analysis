# Finalize policy: why it was not built

## Proposal

Change the final stage in two ways:

- **Wider candidate pool.** Take candidate answers from test results and comparison summaries as well as from hypotheses.
- **Always commit.** Name the leading candidate with a confidence instead of declining.

## Motivation

The shared-state run (10 questions × 3 attempts, 17/30) has 13 losses. Two patterns in them suggested the final step was throwing away points.

1. **Finalize declines with high confidence (6 of 13 losses).** A decline always scores 0.
   - hb048-1 answered "Not identifiable from the supplied data" (confidence 0.98). It had tested 14 hypotheses about specific ions and attributed several of them to background.
   - hb048-2 and hb024-1/2/3 also declined.
   - recaikavdwoimjy3b-1 hedged: "The count data do not establish the direct knockouts…" (confidence 0.97).
2. **The right answer sits outside the hypotheses finalize ranks (3 of 13 losses).**
   - **hb035-1:** a comparison summary says "curated interaction records favor hsa-let-7f-5p", and finalize answered hsa-miR-4500-3p.
   - **recsvhviava5okg19-1 and -2:** every comparison summary says pooled counts rank SBS3 first, and finalize answered SBS5.

## Assessment

For each loss we dumped exactly what finalize received (hypotheses, observations and comparison summaries) and checked whether the correct answer appears in it. A finalize-only change can fix a loss only if the answer is already there.

| Losses | Attempts | Correct answer in finalize's input? | Fixable by the policy? |
|---|---|---|---|
| 9 | hb048 ×3, recaikavdwoimjy3b ×3, hb024 ×3 | No. Diphenhydramine, FTO and ALKBH5 never appear, and no run names a host species. | No. Committing swaps a decline for a different wrong answer. |
| 1 | hb002-3 | No answer: the API was interrupted. | No. This is an infrastructure failure. |
| 3 | hb035-1, recsvhviava5okg19-1, -2 | Yes. let-7 appears 81 times; SBS3 appears 129 and 103 times. | Unlikely. See below. |

**Why the motivating cases do not hold up:**

- **The declines had nothing correct to commit to.** hb048's tested ions were all background or unidentified. recaik's leaders were the near-zero genes EPDR1, HHEX and DTX3. The high confidence was an accurate reading of a run that never found the answer.
- **Where the answer is present, the same summaries also back the rival.** The data gives different answers under different conventions, and the run did not settle which convention the question means.
  - **hb035-1:** the summary goes on: "while TargetScan favors hsa-miR-4458 … the question cannot be answered uniquely without specifying the association resource."
  - **recsvhviava5okg19:** the summary also says "equal-weighting normalized sample percentages ranks SBS5 first … the question does not state the aggregation convention."
  - **Upshot:** a wider pool brings in the rubric's answer and its rivals with equal support, and "best-supported" does not choose between them.

## Conclusion

- **The expected gain is about 0 points.** The ceiling is +3 (17 → 20/30), reached only if the policy happens to pick the rubric's convention, and nothing in it gives it a reason to.
- **10 of 13 losses happen before finalize.** The correct answer never enters the run, which points to how candidates are found ([candidate-scan.md](candidate-scan.md), [explain-away.md](explain-away.md)).
- **The 3 recoverable losses need convention adjudication, not a better final rule.** Keep one candidate per convention and choose between them explicitly from the question's wording and the field's convention. Even that does not guarantee the rubric's answer, because recsvhviava5okg19 is itself ambiguous.

Evidence: 3 attempts per question, so the results are descriptive, not statistical.
