# Golden hypothesis: SHELDON (plan-keeps-question) vs Codex

## Question

The [golden hypothesis ablation](golden-hypothesis.md) puts the correct answer among the round-1 hypotheses without saying which one it is. In that run, plan dropped the question from `situation` in 4 of 15 attempts, so it was repeated with `plan_keeps_question` on. This report compares that run with Codex given the same four hypotheses.

When both agents are handed the same four candidates, one of them correct and none marked, which one ends on the correct answer more often?

**Result:** Codex 10/15, SHELDON 6/15. SHELDON wins only hb048 (3/3 vs 2/3). It loses hb024, recaikavdwoimjy3b and recsvhviava5okg19 because it rejects or reframes the correct candidate after testing it. Codex accepts the candidate on weaker evidence, or with none.

## Setup

| | SHELDON golden + plan-keeps-question | Codex golden |
|---|---|---|
| Run | `runs/golden_hypothesis_plan-keeps-question_5q_3attempts_20261120` | `runs/codex_golden_hypothesis_5q_3attempts` |
| Config | [sheldon_golden_hypothesis_pkq_5q.yaml](../configs/sheldon_golden_hypothesis_pkq_5q.yaml) | [codex_golden_hypothesis_5q.yaml](../configs/codex_golden_hypothesis_5q.yaml) |
| Agent | SHELDON, adaptations `[plan_keeps_question]`, 3 rounds, session mode | Codex CLI, unchanged |
| Model | gpt-6-luna, high effort | the same |
| How the candidates arrive | After the round-1 explore turn, a hidden call writes a 4th hypothesis stating the correct answer. It is inserted at a random position and reads as one the model proposed ([golden_hypothesis.py](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/golden_hypothesis.py)). | A `## Candidate hypotheses` section in the prompt holds the same 4 hypotheses, in the same order, framed as unverified output of "a preliminary exploration" ([golden_hints.py](../bmbench/golden_hints.py)). |
| Which 4 hypotheses | SHELDON's own 3 plus the golden one | Those of the matching attempt of `runs/golden_hypothesis_5q_3attempts_20261116`, the earlier golden run without plan-keeps-question |
| Questions | hb024, hb048, recaikavdwoimjy3b, hb035, recsvhviava5okg19 (the 5 the baseline did not solve 3/3), 3 attempts each | the same |
| Sandbox, grader | 4 CPU, 3.5 GB, 1 h; claude-opus-5-5 grader | the same |

The two arms do not see identical hypothesis text. The Codex attempts reuse the four hypotheses from the earlier golden run, because the plan-keeps-question run did not exist when the Codex run started. In both arms, the golden hypothesis's wording comes from the same writer prompt, and the other three are the model's own proposals for the same question.

## Results

The main comparison is the first two columns. The others are for reference.

| Question | **SHELDON golden + pkq** | **Codex golden** | SHELDON golden, no pkq | SHELDON baseline | Codex baseline |
|---|---:|---:|---:|---:|---:|
| hb024 | 0/3 | **2/3** | 3/3 | 0/3 | 0/3 |
| hb048 | **3/3** | 2/3 | 2/3 | 0/3 | 0/3 |
| recaikavdwoimjy3b | 0/3 | **2/3** | 0/3 | 0/3 | 0/3 |
| hb035 | 3/3 | 3/3 | 2/3 | 1/3 | 3/3 |
| recsvhviava5okg19 | 0/3 | **1/3** | 1/3 | 1/3 | 0/3 |
| **Total** | **6/15** | **10/15** | 8/15 (7/15 judge score) | 2/15 | 3/15 |
| Cost per attempt | $0.117 | $0.016 | $0.134 | $0.136 | $0.014 |
| Minutes per attempt | 12.1 | 3.2 | 21.2 | 25.3 | 3.0 |

The rubric accepts any let-7 family member for hb035. The grader flagged no attempt in either main arm as cheating. In the "no pkq" column, two hb024 attempts were flagged for literature lookup (see [golden-hypothesis.md](golden-hypothesis.md)), which is why its judge score is 7/15.

Baselines:
- SHELDON: `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`
- Codex: `runs/full_codex_gpt-6-luna_high_timeout-1h_3-attempts`

Both baseline columns are restricted to these 5 questions.

## What happened to the correct candidate, by question

### hb024: SHELDON refuses an answer the data cannot support

The answer (2 tissue groups from 3 named snake species) cannot be derived from the 18 anonymous FASTA files.

- **SHELDON (0/3):** all three attempts shelved the golden hypothesis or narrowed it to "the file counts are compatible with, but do not establish, 3 species × 2 tissues". Typical reason: "No observation supports or refutes the proposed two tissue labels or the three scientific species names". All three final answers say the data do not determine the groups.
- **Codex (2/3):** two attempts counted the files and then stated the candidate as the answer. The third said it was not determinable.
- **Earlier golden run (3/3), and why it won:**
  - In attempts 1 and 3 plan dropped the question, so the stages explored unrelated features: 16S composition, evenness, a signal shared by samples 8–12. The golden hypothesis was then the only one in the belief that answered the question, and finalize, which sees the question, used it with a hedge. Attempt 2 kept the question and was correct after a literature lookup that the grader flagged.
  - Here plan-keeps-question restored the question in all 3 attempts. The stages proposed rival designs (3×6, 9×2, 6×3, 18 groups) and tested claims such as "compatible with, but do not establish, 3 species × 2 tissues". These ranked first at finalize.
  - The earlier 3/3 came from the defect that plan-keeps-question fixes.

The gain on this question measures willingness to repeat an unverifiable hint, not analysis. Codex's 2/3 should not count as a win in ability.

### hb048: SHELDON verifies the candidate best

- **SHELDON (3/3):** two attempts tested the golden hypothesis directly. They parsed the mzML spectra and found the protonated diphenhydramine precursor near m/z 256.17 and its product ions. The third refined it into a child that kept the answer.
- **Codex (2/3):** one attempt answered ceftezole instead.

This is the only question where a candidate can be checked by a direct measurement, and SHELDON's per-candidate testing pays off.

### recaikavdwoimjy3b: different evidence standards

The candidate is that ALKBH5 and FTO are knocked out in samples 1–3. In the count table both genes are significantly but only partly reduced in samples 1–3: log2FC −0.41 and −0.91, padj 5e-17 and 2e-30.

- **SHELDON (0/3):** all three attempts read a knockout as loss of expression. They rejected the candidate because the genes "are robustly expressed in every sample, with no knockout-like absence", and replaced it with genes whose expression drops to zero (DTX3, HHEX, EPDR1).
- **Codex (2/3):** two attempts looked up the two Ensembl IDs, saw the consistent reduction and answered "ALKBH5, FTO". The third answered EPDR1, DTX3, HHEX, like SHELDON.

The data support the candidate only under a looser reading of "knockout" (a knockout can leave a truncated transcript). SHELDON's stricter test rules out the correct answer.

### hb035: both solve it

All six answers are let-7a-5p or let-7f-5p. SHELDON shelved or refined the golden hypothesis every time, because no curated interaction table could be fetched. Its finalize still chose a let-7 member.

### recsvhviava5okg19: SHELDON's finalize picks the other aggregation

The answer depends on how signature contributions are aggregated:
- pooling the 300 tumors' mutation counts before fitting puts SBS3 first, at about 35%;
- the equal-weight mean of per-sample fractions puts SBS5 first.

- **SHELDON (0/3):** all three attempts measured both numbers.
  - In attempt 2 a child of the golden hypothesis survived as `tested`, with the note "Answer to the task: SBS3".
  - Finalize still answered SBS5 in all three.
- **Codex (1/3):** one attempt pooled the counts, fit them with NNLS and answered SBS3. The other two answered SBS5.

This is the finalize failure that [golden-hypothesis.md](golden-hypothesis.md) and [answer-rivals.md](answer-rivals.md) already describe. plan-keeps-question does not touch it.

## Findings

1. **Given the same four candidates, Codex ends on the correct one more often: 10/15 vs 6/15.** On this model, SHELDON's per-candidate testing works against the hint more often than for it.
   - **Where SHELDON loses** (hb024, recaikavdwoimjy3b, recsvhviava5okg19), the correct answer is unverifiable, contradicted by a strict reading of the data, or disagrees with SHELDON's choice of aggregation.
   - **Where it wins** (hb048), the correct answer can be checked against a measurement.
2. **Part of Codex's lead is accepting the hint, not analysis.** Its hb024 wins repeat an answer the data cannot support. Without hb024, the score is 8/12 vs 6/12.
3. **plan-keeps-question lowers the oracle total (6/15 vs 8/15) only through hb024 (3/3 → 0/3), and that drop is a correction.** Without it, the golden hypothesis won hb024 by default, because the stages never addressed the question. Without hb024, golden + pkq scores 6/12 against 5/12 without pkq. In the 10-question run without golden it also changed nothing (16/30 vs 17/30 with early_stop + shared_analysis_state alone, see `runs/pkq_es_ss_10q_3attempts_20261122`). It does what it is meant to do (the question stays visible), but the recall and finalize failures are elsewhere.
4. **SHELDON costs about 7× more and takes about 4× longer per attempt** ($0.117 vs $0.016, 12 vs 3 minutes).

Each cell has 3 attempts, so a difference of one attempt per question is within noise. The 4-point gap in the totals comes from three questions that each go the same way in 2–3 attempts.

## Validity: the Windows run

The first version of this run (13 of its 15 attempts, run on Windows) is **invalid** and is not used above.

- **The answer stayed in the prompt.** The host wrote `stdin.main.txt` with CRLF line ends. The driver's sentinel `\n<<<bmb-golden-hypothesis>>>\n` then did not match, so the golden answer stayed in the prompt as plain text, after a visible sentinel.
- **Nothing was injected.** Those attempts had only the model's own 3 round-1 hypotheses.
- **plan-keeps-question did nothing.** Its `## Question` pattern did not match either, so all 13 checks logged `no_question`.
- **It scored 11/15.**
  - In the 10 attempts where plan copied the leaked text into `situation`, every later call saw the answer, and all 10 were correct.
  - In the 3 where plan dropped it, the answer appeared once, and all 3 were wrong.
- **The grader did not notice.** It flagged none of the 13 as cheating, because the leak was in the task text itself.

These attempts are kept in `runs/golden_hypothesis_plan-keeps-question_5q_3attempts_20261120_invalid-crlf/`. All 13 were rerun on macOS. The 2 attempts originally run on macOS (hb035-3, recsvhviava5okg19-3) were valid and are kept.

**Fixes:**
- The host now writes stdin and `prompt.md` with LF on every platform ([sheldon.py](../bmbench/agents/sheldon.py), [golden_hints.py](../bmbench/golden_hints.py), [runner.py](../bmbench/runner.py)).
- `split_stdin` and plan-keeps-question normalize CRLF.
- `split_stdin` now raises if the sentinel survives in the prompt, so the attempt fails instead of leaking.
- [audit_golden_hypothesis.py](../scripts/audit_golden_hypothesis.py) counts sentinel occurrences in each trace.
- Tests cover all three cases.
- The driver-side checks take effect in images built after this change.

**Audit of the runs used here:**
- **SHELDON golden + pkq:** 15/15 injected, 0 traces with the sentinel. plan-keeps-question logged `kept` 10 times and `restored` 5 times. Two commands were flagged, and both are harmless: a read of SHELDON's own retrievals directory, and a write of a notes file.
- **Codex golden:** all 15 stdin files hold exactly 4 candidates, no carriage returns, and no mention of "golden". The only "golden" strings in its logs are UCSC `goldenPath` URLs.
- **The earlier SHELDON golden run:** 15/15 injected, 0 sentinels.

## Reproduce

```bash
uv run bmbench run -c configs/sheldon_golden_hypothesis_pkq_5q.yaml   # needs data/golden_answers.yaml
uv run bmbench run -c configs/codex_golden_hypothesis_5q.yaml         # needs runs/golden_hypothesis_5q_3attempts_20261116
uv run python scripts/audit_golden_hypothesis.py runs/golden_hypothesis_plan-keeps-question_5q_3attempts_20261120
```
