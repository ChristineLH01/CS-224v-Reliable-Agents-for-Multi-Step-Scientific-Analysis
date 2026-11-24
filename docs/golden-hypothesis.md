# Golden hypothesis (oracle ablation)

With plan_keeps_question on, and compared with Codex given the same candidates: [golden-hypothesis-pkq-vs-codex.md](golden-hypothesis-pkq-vs-codex.md).

## Question

The runs 2 baseline had the correct answer among the round-1 hypotheses in 18 of 30 attempts. It never had it for hb024, hb048 and recaikavdwoimjy3b, and it had it but lost it in some attempts of hb035 and recsvhviava5okg19.

If the correct answer is always in round 1, and the model cannot tell which hypothesis it is, does SHELDON end on it?

## Setup

- **Injection:** after the round-1 explore turn, one hidden LLM call writes a 4th hypothesis that states the correct answer. It copies the style of the model's 3 hypotheses and is inserted at a random position. The model's own `submit_exploration` call in the session history is rewritten to hold it, so for the rest of the run it reads as one the model proposed. ([code](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/golden_hypothesis.py), [prompt](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/prompts/golden_hypothesis.prompt))
- **Hiding:** the answer reaches the driver on stdin, after a sentinel, from `agent.golden_answers` on the host. It is never written to the environment, config.json, task.json or the trace. The writer call runs with the trace off. The rewritten reply drops its encrypted reasoning.
- **Questions:** the 5 baseline questions not solved 3/3, with 3 attempts each. Everything else is the baseline config verbatim ([config](../configs/sheldon_golden_hypothesis_5q.yaml)).
- **Baseline:** `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`, the same 5 questions.
- **Run:** `runs/golden_hypothesis_5q_3attempts_20261116`

## Results

| Question | Baseline | Golden (judge score) | Golden (correct, ignoring cheating) |
|---|---|---|---|
| hb024 | 0/3 | 2/3 | 3/3 |
| hb048 | 0/3 | 2/3 | 2/3 |
| recaikavdwoimjy3b | 0/3 | 0/3 | 0/3 |
| hb035 | 1/3 | 2/3 | 2/3 |
| recsvhviava5okg19 | 1/3 | 1/3 | 1/3 |
| **Total** | **2/15** | **7/15** | **8/15** |

The judge flagged two attempts as cheating:

- **hb024-2:** correct, but flagged. The model searched PubMed for the injected species names and read the source paper's sample section.
- **hb035-1:** wrong and flagged. The model tried to download the CLASH paper's supplementary tables.

Both flags come from literature lookup. They are not a sign that the model detected the injection.

## What happened to the golden hypothesis

All 15 golden hypotheses were tested in round 1, because candidate analysis ran on all 4 hypotheses. Each one ends every round as `shelved`, but `shelved` mostly means "refined into a child". Its fate has to be followed through the `parent` lineage.

| Round-1 fate | Attempts | Final answer |
|---|---|---|
| Refined, and the child keeps the answer | hb024-2, hb048-1/2, hb035-1/2, recsv-1/2/3 | Gold in 5 of 8. hb035-2 answered let-7e, which the rubric accepts. hb035-1 answered miR-21. recsv-2/3 answered SBS5. |
| Shelved as "nothing in the data verifies it" | hb024-1/3 | Finalize still gave it, with a hedge. Graded correct. |
| Shelved as contradicted by the data | hb035-3, hb048-3 | let-7f (accepted). hb048-3 said "not identifiable" because its analysis missed the m/z 256.1696 feature that hb048-2 found. |
| Superseded in its local loop by a different answer | recaikavdwoimjy3b ×3 | DTX3/EPDR1/HHEX or "undetermined". The data analysis favoured genes whose expression drops to zero. |

## Findings

1. **Having the answer in round 1 helps, but less than it looks.** Accuracy goes from 2/15 to 7/15. Most of the gain is in hb024 and hb048.
   - hb024's answer cannot be derived from the data, so the model was repeating the hint.
   - hb035's rubric accepts any let-7 family member.
2. **recsvhviava5okg19 fails at finalize, not at search.** All 3 attempts measured that SBS3 ranks first when counts are pooled. In recsv-2, SBS3 was 35.5% and SBS5 28.7%, and a child stating SBS3 survived to the end. Attempts 2 and 3 still answered SBS5, the per-sample mean. The fix to look at is how finalize handles an aggregation choice.
3. **recaikavdwoimjy3b fails in the analysis.** The model's evidence (expression dropout) points away from ALKBH5 and FTO, so the correct candidate does not survive even when it is present. Attempt 1 also confused the Ensembl IDs of ALKBH5 and ALKBH3.
4. **hb048 is unstable.** One attempt of three missed the diagnostic precursor.

## Leakage audit

`uv run python scripts/audit_golden_hypothesis.py runs/golden_hypothesis_5q_3attempts_20261116`

- **No reads of the trace, config.json or task.json** in any of the 15 attempts.
- **Two flagged commands, both harmless:**
  - `env | grep SHELDON`: the environment holds no answer.
  - `find ~/.sheldon/runs/... | head -30`: its output listed only retrievals files.
- **Ignored as legitimate:** reads of `$SHELDON_RETRIEVALS`, which SHELDON itself points the model to, and UCSC `goldenPath` URLs.

## Caveats

- **Small sample:** 3 attempts per question.
- **Specific answers enable literature search:** an injected answer like named species gives the model something to look up, which is what caused the hb024-2 cheating flag.
