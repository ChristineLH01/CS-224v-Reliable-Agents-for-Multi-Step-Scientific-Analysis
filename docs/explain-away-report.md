# Explain-away search: summary

Full details: [explain-away.md](explain-away.md).

## Motivation

On the three questions SHELDON never solves (hb024, hb048, recaikavdwoimjy3b), the correct answer never enters the run: not in round 1, not in a candidate scan, and not in finalize. hb048 shows why. The question asks which drug was given to a patient, from untargeted LC-MS/MS data.

- **Attention follows magnitude.** Every run ranked ions by intensity. The answer, diphenhydramine at m/z 256.17, ranks #31 of 590 features by intensity, and no run reported it. One run looked at it and passed over it.
- **Background is identified but never subtracted.** Runs correctly named EDTA (from the collection tube), tryptophan (endogenous), piperine (diet) and a phthalate (plastic). No step concluded that the answer must lie in what remains, and they answered "not identifiable" or named an opioid.

Agents scored 0 of 9 on hb048 (baseline, shared-state and structured-scan runs, 3 attempts each).

## Method

The method treats a hypothesis as an account of the whole dataset rather than as a single answer. It has three primitives:

1. **Answer footprint.** From the question alone, state what any answer would leave in the data, and compute it as a program over the full tables.
   - On hb048: an administered drug leaves a parent plus metabolites, linked by biotransformation mass shifts and sharing a fragment ion.
2. **Explain away.** Attribute each group the footprint finds to a source (endogenous, diet, matrix or contaminant, or the answer) and remove the background.
3. **Interpretation as hypothesis.** When the data gives different answers under different conventions, the unit of hypothesis is an (interpretation, answer) pair. This primitive is not yet tested.

## Checks on hb048

All three checks use gpt-6-luna, the model SHELDON runs on, with 5 samples each. Each costs a few cents. The rules for every check were fixed before it ran.

| Check | Who writes the footprint | Who attributes sources | Result |
|---|---|---|---|
| 1 | me (biotransformation shifts + shared fragment ≥ m/z 100; intensity not used) | nobody | The drug family is found intact (5 members, all sharing the 167.085 fragment), but ranks **#6 of 41**. Endogenous homologous series rank above it. |
| 2 | me (output of check 1) | model, blind (no compound names) | **4 of 5 name diphenhydramine.** 5 of 5 attribute family #6 to the drug and families #1–#5 to endogenous or dietary sources. |
| 3 | model, from the question and the table format only | model, in a fresh call | **1 of 5** (pass criterion was ≥ 3 of 5). |

**Check 3, in detail:**

- **The footprint was derived correctly.** In 4 of 5 samples the model stated the same footprint I wrote by hand.
- **The ranking promoted background.** Each program ranked groups by size or link count. A large lipid-like series, linked by generic low-mass ions, came first, and the drug family fell outside the printed top 10 in 3 of 5 samples.
- **The answer step declined too readily.** In one sample the drug family was in the output, and the model still answered "not determinable".

## Conclusions

- **The footprint and explain-away need each other.** The footprint alone ranks the drug family #6. Explaining away without a footprint, as the agents do, ends on the strongest unexplained ion. Together they turn a 0-of-9 question into 4 of 5.
- **The model can write the footprint, but one ranking pass is not enough.** A generic footprint fits a large background family better than it fits the answer.
- **Limits:**
  - One question.
  - In checks 1–2 I wrote the predicate knowing the answer existed. My choice of fragments ≥ m/z 100 turned out to matter.

## Next step

**Check 4** makes explain-away iterative: groups attributed to background are removed and the next groups are shown, until a group is attributed to the answer. The answer step must also name its leading candidate. Its rules are fixed before it runs, with a pass criterion of ≥ 3 of 5.

If check 4 passes, test generalization with a scan-only recall run on 20 held-out questions, drawn with stratification by data type ([heldout-sample.json](explain-away/heldout-sample.json)).
