# Plan keeps question

## Problem

SHELDON's plan stage splits the statement into `situation` and `deliverable`. Only `situation` reaches the stages before finalize (`run.py:134`). The plan prompt never says where the question goes, and a split is accepted if `situation` is at least 120 characters long (`stages.py:54-58`).

In 7 of the 30 runs 2 attempts, the question went to `deliverable`, so three rounds ran without knowing what was asked. Four of those attempts were wrong: hb024 ×3 and recaikavdwoimjy3b-3. Evidence: [loss analysis](trajectory-audit/runs2-sheldon-loss-analysis.md) §3.1.

## Fix

The `plan_keeps_question` adaptation ([code](../bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/plan_keeps_question.py)) runs after plan:

- **Check:** every `?` sentence of `## Question` must appear in `situation`. If there is none, the first sentence must.
- **If missing:** `situation` becomes the whole statement (SHELDON's own fallback when it rejects a plan), and `requirements` are kept.
- **Record:** the result goes in the trace and in `result.json` (`sheldon.plan_keeps_question`).
- **Enable by name:** it is not part of `[all]`.

## Validation

- **Replay:** run on the 30 real runs 2 splits, the check flags exactly the 7 bad ones.
- **Tests:** [8 tests](../tests/test_plan_keeps_question.py) pass, including an offline full loop where explore sees the question only with the fix on.

## Experiment

**Setup:** hb024 and recaikavdwoimjy3b × 3 attempts, with only this adaptation on ([config](../configs/sheldon_plan_keeps_question_2q.yaml)).

| Attempt | Check | Answer | Runs 2 answer |
|---|---|---|---|
| hb024-1/2/3 | restored / kept / restored | abstained | abstained |
| recaikavdwoimjy3b-1 | restored | DTX3, HHEX | DTX3 |
| recaikavdwoimjy3b-2 | restored | HHEX, DTX3 | HHEX, DTX3 |
| recaikavdwoimjy3b-3 | kept | HHEX, DTX3 | EPDR1 |

- **The fix works:** the question was restored in 4 of 6 attempts, and in all 6 the analysis now works on the question.
- **Score still 0/6:**
  - hb024 still abstains.
  - recaikavdwoimjy3b still picks the near-zero genes and never proposes FTO or ALKBH5.
- **Next:** keep the fix. These problems need downstream changes, in identifying hosts from sequence and in ranking knockouts.
