# Explain-away search: feasibility check

## Idea

SHELDON proposes answers and tests them one at a time. On the questions it never solves, the correct answer never reaches any stage: not round 1, not the candidate scan, not finalize. The structured scan ([candidate-scan.md](candidate-scan.md)) narrowed down why.

- **Attention goes by magnitude.** On hb048 the agent ranked features by intensity. Attempt 2 looked at the 256.1699 ion and passed over it.
- **Background is identified but never subtracted.** The identify step named the strong signals correctly: EDTA (an anticoagulant in collection tubes), tryptophan (endogenous), piperine (diet), a phthalate (plastic), carnitine (endogenous). None of them answers "what drug was administered", yet no step concluded that the answer must lie in what remains.

The proposed method makes the hypothesis an account of the whole dataset rather than an answer. It has three primitives:

1. **Source attribution (explain away).** Every salient feature or family is attributed to a source, such as instrument or matrix, endogenous, diet, contaminant, technical, or answer-relevant. Background leaves the answer search.
2. **Answer footprint.** From the question, derive what the answer would leave in the data, and write it as an executable predicate over the full tables, not as an instruction to "notice" something. Example: an administered drug leaves a parent plus metabolites, linked by biotransformation mass differences and sharing a core fragment.
3. **Interpretation as hypothesis.** The unit is an (interpretation, answer) pair, decided by domain convention (recsvhviava5okg19, hb035).

## Check 1: does a generic footprint predicate find the drug family? (no LLM)

The rules are fixed in the [`scripts/footprint_ms_check.py`](../scripts/footprint_ms_check.py) docstring before the run, and intensity is not used for ranking:

- **Features:** MS2 precursors merged within 5 ppm, each with its consensus fragments.
- **Links:** two features are linked when their mass difference is a standard phase I/II biotransformation (13 deltas) and they share at least one fragment.
- **Families:** connected components of linked features, ranked by size.

**Result:**

- **Families found:** 1,009 MS2 scans give 590 features, 148 links and 41 families ([output](explain-away/hb048-footprint-v1.txt)).
- **The diphenhydramine family is recovered intact and ranks #6 of 41.** It has 5 members: 242.1542, 256.1699, 270.1489, 272.1646 and 432.2024.
  - Its links are demethylation, N-oxidation (+O), glucuronidation and dehydrogenation.
  - Every link shares the 167.085 fragment (the benzhydryl cation).
  - By intensity, the parent is feature #31 of 590.
- **What ranks above it:**
  - **#1:** a homologous series with high hydrogen content and generic alkyl fragments.
  - **#3:** a 412–432 steroid-like series.
  - **#4:** negative-mass-defect clusters.
  - **#5:** a tryptophan-related family.
  - These are linked by steps such as ±C₂H₄, ±H₂ and +H₂O, which also connect homologous series.

**A post-hoc variant was worse.** Requiring every family member to share one core fragment ([`scripts/footprint_core_posthoc.py`](../scripts/footprint_core_posthoc.py), written after seeing the result above) pushed the drug family out of the top 6. The background series also share generic alkyl ions (109.101, 107.086). That variant is discarded.

**Conclusion:** the predicate can represent the footprint, but structural regularity alone does not isolate the answer.

## Check 2: does explaining away isolate it? (blind LLM test)

[`scripts/explain_away_check.py`](../scripts/explain_away_check.py) gives gpt-6-luna (reasoning effort high), the model SHELDON runs on, the top 10 families from check 1 and the question.

- **Blind:** the families carry no compound names ([prompt](explain-away/hb048-explain-away-v1.prompt.txt)).
- **Task:** attribute each family's source, then name the parent drug.
- **Samples:** 5 independent calls ([outputs](explain-away/hb048-explain-away-v1.jsonl)).

| Sample | Family #6 attributed to | Parent drug | Background families |
|---|---|---|---|
| 1 | administered drug or metabolites | diphenhydramine | #1 endogenous, #2 diet, #3 endogenous |
| 2 | administered drug or metabolites | "undetermined" (aglycone + glucuronide, parent unresolved) | #1 endogenous, #2 diet |
| 3 | administered drug or metabolites | diphenhydramine | #1, #3 and #5 endogenous; #2 diet |
| 4 | administered drug or metabolites | diphenhydramine | #1, #3 and #5 endogenous; #2 diet |
| 5 | administered drug or metabolites | diphenhydramine | #1, #3 and #5 endogenous; #2 diet |

- **The family was picked out in 5 of 5 samples, and the drug was named in 4 of 5.**
- **For comparison, the agents had 0 of 9 on this question:** SHELDON runs 2 (0/3), shared-state v2 (0/3) and the structured scan (0/3). Claude Code is 3/3.
- **Cost:** about 7k input and 11–15k output tokens per call.

## What this does and does not show

- **Shows:** on hb048, footprint plus explain-away turns a question the agent never solves into one the same model answers 4 of 5 times. Neither part works alone. The footprint ranks the drug family only #6, and the agent, which explains features away without a footprint, answers with the strongest unexplained isomer (morphine).
- **Does not show that it generalizes.** It is one question.
  - I wrote the predicate knowing the answer existed. Its rules were fixed before the run, and the biotransformations are a standard list, but designer bias cannot be ruled out.
  - The method's claim is that the model writes the footprint predicate itself, for any data type. That is untested.
- **Next:** the model writes the predicate (check 3, below). Then run on the held-out sample.

## Check 3: does the model write the footprint itself?

[`scripts/explain_away_selfwritten.py`](../scripts/explain_away_selfwritten.py). Its docstring fixes the rules before the run.

- **Input:** `features.json`, 589 MS2 precursor features, each with its consensus fragments (≥ 2% of base peak, m/z ≥ 40). The preprocessing is answer-neutral: no biotransformation list, no fragment filtering and no families.
- **Write:** gpt-6-luna gets the question, the table format and 3 example rows. The system prompt is data-type neutral ([prompt](explain-away/hb048-selfwritten-v1/write.prompt.txt)). It asks for three things:
  - the answer's footprint, stated as relations among data units;
  - the background, and how the footprint separates the answer from it;
  - a stdlib program that ranks groups by how fully they satisfy the footprint, not by magnitude, and names no answer.
  - One repair call is allowed if the program fails.
- **Run:** in docker with no network, then a fresh call attributes each printed group and names the drug.
- **Pass criterion:** diphenhydramine in at least 3 of 5 samples.
- **Cost:** about $0.05 in total.

| Sample | Footprint the model stated | Program ranks by | Drug family in output | Answer |
|---|---|---|---|---|
| 1 | biotransformation shift + shared fragment | shift classes, then links | no | declined |
| 2 | biotransformation shift + ≥2 shared fragments + RT separation | criteria met | group 2 | **diphenhydramine** |
| 3 | biotransformation shift + ≥2 shared ions incl. a rare one (repaired once) | relations, then shared ions | no | declined |
| 4 | biotransformation shift + shared rare ions | members, then shift types | group 6 | declined |
| 5 | ≥2 shared product ions (no shift) (repaired once) | shared ions | no | declined |

**It fails the pass criterion: 1 of 5.** [Outputs](explain-away/hb048-selfwritten-v1/samples.jsonl).

- **The footprint is derived correctly.** 4 of 5 samples arrived at the same footprint I wrote by hand (biotransformation mass shift plus a shared fragment), from the question alone. Sample 5 kept only the shared fragments.
- **Ranking by footprint strength promotes background.** Every program ranked by size or link count. With fragments down to m/z 40, a lipid-like homologous series (281.25, 297.24, …) links through generic alkyl ions (55, 67, 69, 81). It becomes group 1, with 35 members and 50 links. The drug family has 5 members, and in 3 of 5 samples it falls below the printed top 10. My v1 avoided this only through choices made for this data (fragments ≥ m/z 100, generic fragments dropped).
- **The answer step declines even when the family is shown.** In sample 4 it was group 6, with the 167.0858 fragment on its links, and the step answered "not determinable". It also declined in samples 1, 3 and 5. The answer prompt does not ask it to commit.

**What this says about the method:**

- Footprint (primitive 2) is something the model can write.
- A single pass of "rank by footprint, then explain away" is not enough, because a large background family satisfies a generic footprint better than the answer does.
- The fix the method itself suggests is to iterate: groups attributed to background are removed and the program's next groups are shown, until a group is attributed to the answer or the groups run out. Check 2 is effectively one round of this with a list long enough to reach #6. This change and an answer step that names its leading candidate are not yet tested. They would be check 4, with its rules fixed before it runs.

## Held-out sample

[`explain-away/heldout-sample.json`](explain-away/heldout-sample.json): 20 of the 80 full-set questions not studied so far, stratified by data type (inferred from the question text) and drawn with seed 20261116.

| Stratum | Questions | Drawn |
|---|---|---|
| expression | 24 | 6 |
| reads / variants | 26 | 7 |
| microbial | 12 | 3 |
| epigenomic | 14 | 3 |
| other | 4 | 1 |

16 of the 20 are marked human-solvable. Six questions have an uncertain stratum; none was drawn.
