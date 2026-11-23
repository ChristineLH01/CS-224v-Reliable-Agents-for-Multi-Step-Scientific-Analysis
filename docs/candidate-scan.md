# Candidate scan

## Idea

In the baseline run, 36 of 90 round-1 hypotheses (40%) named no specific answer: they described how to find one ("the miRNA with the most validated targets"). The proposed fix has three parts:

1. Before round 1, scan the data and list every candidate answer it supports.
2. Make each hypothesis name a candidate.
3. Treat method and data-quality questions as tests, not hypotheses.

This only helps if the scan lists the correct answer, so we first measured recall offline.

## Implementation

The `candidate_scan` adaptation ([code](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/candidate_scan.py), [prompt](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/prompts/candidate_scan.prompt)) adds one tool-using stage after plan:

- **What it does:** inspects the data with up to 20 shell calls.
- **What it returns:** a ranked list of candidates (3 to 10, each with evidence and cited retrievals), plus method and data-quality tests.
- **Recall mode only:** with `candidate_scan_only: true`, the run stops after the scan. Feeding candidates into explore is not implemented.
- **Not part of `[all]`:** enable it by name.

## Experiment

- **Questions:** the ten 10q questions.
- **Scan runs:**
  - **v1:** 12 completed attempts. It was stopped early because it gave 1 to 2 candidates and gave up on questions that need a database lookup.
  - **v2:** 1 attempt per question. The prompt requires at least 3 candidates, forbids "cannot be determined" as a candidate, and asks for lookups when the data alone cannot settle the answer.
- **Baseline:** round-1 hypotheses of the unadapted run in `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`, 3 attempts per question.
- **Grading:** [`scripts/eval_candidate_recall.py`](../scripts/eval_candidate_recall.py) grades each candidate on its own with the official grader prompt and judge.
- **Provenance:** configs, prompts, image ids and graded outputs are under `runs/candidate_scan_recall_*` and `runs/candidate_recall_eval_*`.
- **Cost:** about $0.006 per scan, plus $2.88 of judging in total.

## Results

| Question | Baseline round-1 hit | Baseline final correct | v2 scan hit |
|---|---|---|---|
| hb002, hb020, recqgsfxqqodhjens, rec5qx7nedrwk4zog | 3/3 | 3/3 | yes, rank 1 |
| hb010 | 2/3 | 3/3 | yes, rank 1 |
| recsvhviava5okg19 | 2/3 | 1/3 | yes, rank 1 |
| hb035 | 2/3 | 1/3 | no |
| hb024, hb048, recaikavdwoimjy3b | 0/3 | 0/3 | no |

- **The scan adds no new correct answers.** It hits only questions the baseline already hits in round 1.
- **It is worse on hb035.** The scan gave up on the database lookup and listed common miRNAs from memory.
- **It does not help on the three questions SHELDON never solves.** Its candidates there are wrong in kind: cattle instead of snakes, opioids instead of diphenhydramine.

**Conclusion:** candidate generation is not the bottleneck, so we did not build the full change. On hb035 and recsvhviava5okg19 the correct answer was already present in round 1 and lost later.

## Structured mode

The v2 scan did not add a correct answer on the three questions SHELDON never solves (hb024, hb048, recaikavdwoimjy3b), and no stage of any run ever proposed one. Those failures are about where candidates come from: the scan, like round-1 explore, names answers from memory after a short look at the data. On hb048 that gave opioids, while Claude Code answered correctly by tabulating every precursor with its fragments, grouping the precursors that share a product ion, and only then naming the group.

`candidate_scan_mode: structured` applies that order to the scan. It makes two isolated tool-using calls:

1. **`structure`** ([prompt](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/prompts/structure_scan.prompt))
   - **What it does:** parses every file and writes full tables under `/workspace/scan/`. Tool output is cut at about 8,000 characters, so it reads the tables through filtering and summaries.
   - **What it returns:** up to 12 features: strong signals, families of units sharing a sub-signal, groupings of samples, contrasts and anomalies. Each feature carries the values a lookup could query with.
   - **What it may not do:** propose answers, or name a feature from memory.
2. **`identify`** ([prompt](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/prompts/structure_identify.prompt))
   - **What it does:** names what lies behind each feature by querying reference resources reachable behind the allowlist, such as PubChem by formula or mass, the BLAST URL API, Ensembl, UniProt and Bioconductor data packages.
   - **What it returns:** an identification per feature, with the query used, and candidate answers. Each candidate lists the features it `explains`.

- **Record:** the same shape as the single-call scan, so `scripts/eval_candidate_recall.py` grades it unchanged, plus `features`, `tables` and `identifications`. These show whether a miss happened in mapping or in naming.
- **Experiment:** hb024, hb048 and recaikavdwoimjy3b, 3 attempts each.
  - **Configs:** `configs/sheldon_structure_scan_recall_{preview,full}.yaml`.
  - **Image:** `bmbench-sandbox:structure-scan`, which is `tenq-v1` plus this tree's `bmb_sheldon`.
  - **Batch:** `scripts/run_sheldon_structure_scan.py`, writing to `runs/structure_scan_recall_batch_20261116`.
- **Grading:** `runs/structure_scan_recall_eval_20261116`, with the official judge.

### Results

**Recall is 0/9.** No attempt listed a correct candidate on any of the three questions. The single-call scan was also 0/3 on each. Every attempt completed, at a mean cost of $0.016 per scan.

| Question | Structure step | Identify step | Where it failed |
|---|---|---|---|
| hb048 | Parsed the mzML in all attempts and listed 6 to 9 features. They were ranked by intensity: EDTA (about 1.3e8), piperine at 286.14, tryptophan, a halogenated cluster and a phthalate. **The 256.17 → 167.09 family was never reported as a feature.** Attempt 2 looked up 256.1699 in its own spectra table (r14), found one MS2 scan and passed over it. No attempt grouped precursors by shared product ion. | PubChem formula lookups worked. They named EDTA (and its sodium adduct), tryptophan and the C17H19NO3 isomers (morphine, piperine) correctly. | Structure |
| hb024 | Found real structure. Attempts 1 and 3 ran BLAST (the URL API, or a local 16S database) and grouped the files by dominant taxa: E. coli-like in files 1–7 and 14, Suttonella in 8–12, Providencia in 18. Attempt 3 clustered them into 4 groups; the answer has 2. | Turned bacterial names into hosts. Escherichia marmotae became the host Marmota himalayana, and Suttonella ornithocola became Parus major. The 16S reference database holds no host or isolation-source fields, so host and tissue had nothing to rest on. | Both, mainly identify |
| recaik | Correct and thorough: PCA groups, DESeq2 (5,976 genes at adjusted p < 0.05), and the genes at zero in samples 1–3. | Named the on/off genes (DTX3, HHEX, EPDR1), as every earlier run did. FTO and ALKBH5 are not extreme on any ranking. | The question (the data barely supports the answer) |

**Conclusion.** Ordering the steps as structure first, lookup second does not help on its own:

- **Naming from lookups works:** when a feature is found, PubChem and BLAST name it correctly.
- **On hb048 the gap is how features are ranked.** The structure step ranks by intensity, and the answer is a weak family (about 7.6e5) that shares a product ion. The step is told to look for such families but does not compute them.
- **On hb024 the gap is the resource.** A 16S database names bacteria, not hosts.

The agreed go criteria for the explanatory-coverage objective were: a correct candidate on any of the three questions, or the hb048 family found. Neither was met, so that objective has not been built.

## How the answer was lost

**hb035 (miRNA most associated with a gene list).** No attempt found a usable miRNA-target database, although the UCSC TargetScan tables are on the allowlist.

- **Attempt 2:** all evidence on the let-7a hypothesis was neutral ("no data to check"), so exploit shelved it in round 1 for lack of support. SHELDON then ranked miRNAs by PubMed co-mentions, a proxy that favours well-studied miRNAs, and answered miR-155.
- **Attempt 3:** let-7a happened to survive and was answered correctly.
- **Attempt 1:** declined to answer.

**recsvhviava5okg19 (top COSMIC signature in breast samples).** The data gives two answers depending on aggregation: pooled mutation counts rank SBS3 first (the rubric's answer), and the mean of per-sample percentages ranks SBS5 first. Attempts 1 and 2 computed both in round 1.

- **Attempt 2:** the local loop refined "SBS3 is highest" into "SBS5 has the highest mean", and the SBS3 hypothesis was shelved as superseded. Later rounds only confirmed SBS5 under the mean.
- **Attempt 1:** its notes said the pooled result supports SBS3, but finalize still chose SBS5.
- **Attempt 3:** never met the ambiguity and answered SBS3.

## Next steps

1. **A refinement that changes the answer should create a rival, not replace its parent.** This would have kept SBS3 in recsvhviava5okg19 attempt 2.
2. **Do not shelve a hypothesis whose evidence is all neutral.** Treat it as untested. This would have kept let-7a in hb035 attempt 2.
3. **When one dataset gives different answers under different methods, keep one candidate per method.** Finalize then chooses between them explicitly, citing the question's wording. This is the original "method questions as tests" idea, triggered when a split is found rather than before round 1. It does not guarantee the right choice, because the recsvhviava5okg19 question is itself ambiguous.

These findings come from one or three attempts per question, so they are descriptive, not statistical.
