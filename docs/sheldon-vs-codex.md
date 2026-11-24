# SHELDON vs Codex with a golden hypothesis

Both agents ran on gpt-6-luna at high effort. Each was given the correct answer as one of four unmarked round-1 candidates. Codex ended on it more often: 10/15, against 6/15 for SHELDON with plan_keeps_question (pkq) and 8/15 for SHELDON without pkq. The no-pkq run had exactly the same candidates as Codex.

## Setup

| | SHELDON | Codex |
|---|---|---|
| Runs | `runs/golden_hypothesis_plan-keeps-question_5q_3attempts_20261120` (pkq), `runs/golden_hypothesis_5q_3attempts_20261116` (no pkq) | `runs/codex_golden_hypothesis_5q_3attempts` |
| Candidates | The model's own 3 round-1 hypotheses, plus a golden one written by a hidden call and inserted at a random position. The history is rewritten so it reads as the model's own. | The same 4 as the no-pkq SHELDON attempt with the same number, word for word, in the prompt |
| Tool calls, cost and time per attempt | 164, $0.117, 12 min (pkq) | 17, $0.016, 3 min |

There are 5 questions with 3 attempts each. The leakage audit is in [golden-hypothesis-pkq-vs-codex.md](golden-hypothesis-pkq-vs-codex.md).

## How the candidates are injected into Codex

The goal is to give Codex what SHELDON's model saw after round 1, and nothing more ([bmbench/golden_hints.py](../bmbench/golden_hints.py), `agent.golden_hypotheses_from` in [configs/codex_golden_hypothesis_5q.yaml](../configs/codex_golden_hypothesis_5q.yaml)).

1. **Pairing.** Attempt N of each question gets the hypotheses of SHELDON attempt N.
2. **The model's own three.** They come from the round-1 `submit_exploration` call in the SHELDON trace. The final belief is not used, because it folds duplicates: 4 of the 15 attempts would have had only 3 hypotheses.
3. **The golden one.** It goes back in at its recorded position. It has no marker, and the word "golden" never appears.
4. **Placement.** The list goes before `## Data and environment`, with this header:

   > ## Candidate hypotheses
   >
   > A preliminary exploration of this data proposed the hypotheses below. None of them has been verified: any, all or none of them may be right. Weigh them against the data as you see fit.

   The header calls them unverified, as SHELDON's model regards its own round-1 guesses. It neither endorses nor dismisses them, and it does not tell Codex how to test them.
5. **Isolation.** The prompt is written with LF line ends to a host-only `attempt-N/stdin.main.txt`. Which item is golden is recorded in `golden_hint.json`, which the sandbox never sees.

This equalizes the text, the order, the missing marker, the model and the sandbox. It does not equalize three things:
- **Ownership:** SHELDON thinks it proposed the candidates.
- **Timing:** SHELDON sees them after its own explore turn.
- **Competition:** SHELDON keeps generating hypotheses afterwards.

## Results

| Question | Gold answer | SHELDON + pkq | SHELDON, no pkq | Codex |
|---|---|---:|---:|---:|
| hb024 | 2 tissues, 3 named snake species | 0/3 | 3/3 | 2/3 |
| hb048 | diphenhydramine | 3/3 | 2/3 | 2/3 |
| recaikavdwoimjy3b | ALKBH5, FTO | 0/3 | 0/3 | 2/3 |
| hb035 | any let-7 member | 3/3 | 2/3 | 3/3 |
| recsvhviava5okg19 | SBS3 | 0/3 | 1/3 | 1/3 |
| **Total** | | **6/15** | **8/15** | **10/15** |

The no-pkq total is 7/15 after the cheating penalty.

**With and without pkq.** pkq only acts when plan has dropped the question from `situation`, which is the task text every stage before finalize sees. It did so in 5 of 15 attempts: hb024 ×3, hb048-3 and recaikavdwoimjy3b-2.

| Attempts | SHELDON + pkq | SHELDON, no pkq |
|---|---:|---:|
| The 5 that pkq changed | 1/5 | 3/5 |
| The 10 that pkq left unchanged | 5/10 | 5/10 |

The whole gap is hb024. The other differences come from resampling: each run wrote its own golden wording, position and own hypotheses.

## Trajectory differences

**Codex never mentions the candidates, but follows them.** None of its 15 trajectories has "candidate" or "hypothesis" in its own messages. Yet the candidates raise it from 3/15 to 10/15, and they steer both its analysis and its answer:
- **hb024:** after 4–5 commands and no biological analysis, it answered with the golden text. It even wrote "the FASTA headers … do not independently verify the host and tissue labels" first.
- **hb048-2:** the golden cited an ion cluster at m/z 182.98 and 154.97. Codex analysed that cluster, answered ceftezole, and never computed diphenhydramine's ion at m/z 256.17.
- **recaikavdwoimjy3b-2:** this was the only prompt naming concrete rival genes with counts, and Codex answered with those genes.

**SHELDON tests the golden claim, and four parts of its process work against it:**

1. **Finalize:** its prompt says "Do not state as established what was only not refuted." For unverifiable answers this beats "give one specific answer".
2. **Refine:** it rewrites unverifiable claims as "X is not established". Such claims are trivially true, so they are tested, kept and ranked first.
3. **Local loop:** after one weak check, a revision replaces the golden claim with a rival, and the claim never comes back.
4. **Ranking:** `ranked()` sorts by evidence count ([state.py](../.venv/lib/python3.14/site-packages/sheldon/methods/sheldon/state.py), line 260), and finalize takes the top claim without deliberating. Repeated robustness checks of one reading therefore outvote a single test of another.

### Per question

| Question | SHELDON | Codex |
|---|---|---|
| **hb024**: the answer is not in the 18 anonymous FASTA files | **+pkq 0/3:** 130–146 tool calls (clustering, 16S, host-marker screens) found only "compatible" evidence. Exploit refined the golden claim into "not established", and finalize answered "cannot be determined", which is the correct reading of the data. **No pkq 3/3:** plan had dropped the question, so the stages explored unrelated features. The golden claim was the only one that answered the question and won by default. | **2/3:** repeated the hint |
| **recaikavdwoimjy3b**: ALKBH5 and FTO are significantly but only partly reduced | **0/3:** it checked with raw counts and CPM only ("Neither transcript is close to absent"), and once with the wrong gene ID. Its own explore had found genes that fall to zero (HHEX, DTX3), and the local loop swapped them in. | **2/3:** DESeq2 every time (log2FC −0.41 and −0.91, padj ≤ 3e-17). It accepted the candidate unless the prompt named a rival gene with counts. |
| **recsvhviava5okg19**: pooled spectrum gives SBS3, per-sample mean gives SBS5 | **0/3:** bootstrap, EM, median and winner counts each added a supported SBS5 claim. A tested SBS3 child ranked 4th, and finalize took the top SBS5 claim with confidence 0.9–0.99. | **1/3:** the win came from a bug in the per-sample code that left only the pooled result. The other two attempts picked the mean without saying why. |
| **hb048**: a measurable ion (m/z 256.1699 and its fragments) | **+pkq 3/3:** it tested the named drug directly. Two golden statements named m/z 256.17, which helped. | **2/3:** one attempt followed the golden's cited ion cluster instead |
| **hb035**: let-7 tops every database | **3/3:** shelved the golden claim, but reached let-7 through miRTarBase and CLASH tables | **3/3** |

## Why SHELDON does worse

SHELDON is built to resist unverified claims. It tests every claim, generates rivals, keeps only what was established, and ranks by evidence count. An injected correct answer is an unverified claim, so SHELDON loses it whenever its own evidence leaves the claim unverifiable, partly supported or outvoted.

Codex has none of this. It adopts a candidate that its short analysis does not contradict. That helps when the candidate is right, and hurts when the candidate's wording points at the wrong evidence.

SHELDON matches or beats Codex only where its own analysis reaches the answer without the hint (hb048, hb035).

**Possible fixes:**
- When nothing is established, answer with the best-supported claim instead of "cannot be determined".
- Do not let "not established" rewrites replace the claim they came from.
- Keep a replaced claim as a rival when the evidence against it is weak.
- Count repeated checks of one reading as one piece of evidence.
- At finalize, choose between readings by the question's wording.

**Caveats:**
- There are 3 attempts per question.
- The pkq run's candidate text differs from Codex's.
- The explanations of why an agent chose an answer are inferred from the trajectories.
