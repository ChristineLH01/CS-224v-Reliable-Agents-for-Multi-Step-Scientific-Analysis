# Keep answer rivals and neutral hypotheses

**Status: no effect. Keep both off.**

## Idea

On hb035 and recsvhviava5okg19, the baseline had the correct answer among the round-1 hypotheses and lost it later ([golden-hypothesis.md](golden-hypothesis.md)). Two ways a correct claim can leave the live set looked worth closing:

- **`keep_answer_rivals`** ([code](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/keep_answer_rivals.py)): a claim shelved because a local loop revised it or exploit refined it is compared with its replacement in one tool-less call. If the two name different answers, the shelved claim stays live as a rival.
- **`keep_neutral_hypotheses`** ([code](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/keep_neutral_hypotheses.py)): a claim exploit shelved with no supporting and no refuting observation is kept, since it was never checked.

## Experiment

- **Setup:** hb035 and recsvhviava5okg19 × 3 attempts, both adaptations on, everything else the baseline config ([config](../configs/sheldon_answer_rivals_hb035_recsv.yaml)).
- **Run:** `runs/answer_rivals_hb035_recsv_3attempts_20261116`
- **Baseline:** `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`, the same 2 questions.

| Question | Baseline | Both adaptations |
|---|---|---|
| hb035 | 1/3 | 2/3 |
| recsvhviava5okg19 | 1/3 | 0/3 (SBS5 ×3) |
| **Total** | **2/6** | **2/6** |

## Conclusion

- **No gain.** The total is unchanged. The hb035 change is within the attempt-to-attempt spread of 3 attempts.
- **recsvhviava5okg19 does not lose the answer by shelving it.** All three attempts kept SBS3 in play and still answered SBS5, the per-sample mean. The loss is in how finalize resolves an aggregation convention ([finalize-policy.md](finalize-policy.md)), not in which claims stay live.
- **Both stay in the code, off by default,** so the run can be reproduced.
