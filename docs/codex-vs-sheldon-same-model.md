# Codex vs SHELDON on the Same Model

Earlier baselines ran Codex on a different model (gpt-6.1-sol) from SHELDON (gpt-6-luna), so they compared two agents and two models at once. This run puts Codex on gpt-6-luna. On the same 10 BioMysteryBench questions with 3 attempts each, the agent is now the only difference.

**Result:** both agents get 17/30 correct. Codex costs about 1/9 as much per attempt and takes about 1/6 the time. SHELDON solves one more question at least once (pass@3 0.70 vs 0.60). Codex is right on all three attempts of a question SHELDON mostly loses (hb035).

## Setup

| Setting | SHELDON baseline | Codex |
|---|---|---|
| Run | `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts` | `runs/full_codex_gpt-6-luna_high_timeout-1h_3-attempts` |
| Config | that run's `config.yaml` | [configs/codex_luna_10q.yaml](../configs/codex_luna_10q.yaml) |
| Agent | SHELDON, no adaptations, toolloop engine, 3 rounds | Codex CLI 0.166.0 (`codex exec`), unchanged |
| Model | gpt-6-luna, high effort, through the LiteLLM proxy | the same |
| Questions | hb002, hb020, recqgsfxqqodhjens, hb024, hb035, hb048, rec5qx7nedrwk4zog, hb010, recaikavdwoimjy3b, recsvhviava5okg19 | the same |
| Attempts | 3 per question, 30 in total, a fresh container each | the same |
| Limits | 1 h, 4 CPUs, 3.5 GB RAM per attempt; network allowlist; web search off | the same |
| After the time limit | up to 300 s for SHELDON's finalize stage | up to 300 s for one resumed "best answer now" turn |
| Judge | claude-opus-5-5, medium effort, including the cheating audit | the same |

The Codex run took place on 2026-11-16. The host Mac slept for part of it, which paused the containers. Agent times are measured on a clock that stops during sleep, so the times below are unaffected.

## Overall results

| Metric | SHELDON | Codex |
|---|---:|---:|
| Accuracy | 17/30 (56.7%) | 17/30 (56.7%) |
| pass@3 (solved in at least 1 attempt) | 0.70 | 0.60 |
| pass^3 (solved in all 3 attempts) | 0.50 | 0.50 |
| Answer agreement across attempts | 0.73 | 0.80 |
| Mean cost per attempt | $0.129 | $0.015 |
| Mean agent time per attempt | 28.9 min | 4.4 min |
| Mean tool calls per attempt | 181.5 | 17.4 |
| Mean output tokens per attempt | 88.7k | 12.2k |
| Timeouts | 2 | 0 |
| Out-of-memory kills | 0 | 0 |
| Attempts flagged for cheating | 0 | 1 |

By whether humans could solve the question:

| Subset | Metric | SHELDON | Codex |
|---|---|---:|---:|
| Human-solvable (5 questions) | Accuracy | 0.80 | 0.73 |
| | pass@3 / pass^3 | 0.80 / 0.80 | 0.80 / 0.60 |
| Not human-solvable (5 questions) | Accuracy | 0.33 | 0.40 |
| | pass@3 / pass^3 | 0.60 / 0.20 | 0.40 / 0.40 |

## Per question

Correct attempts out of 3, after the cheating penalty.

| Question | Human-solvable | SHELDON | Codex | Answers |
|---|---|---:|---:|---|
| hb002 | yes | 3/3 | 2/3 | All six answers are Bacillus licheniformis. One Codex attempt scored 0 for cheating (see below). |
| hb020 | yes | 3/3 | 3/3 | Homo sapiens |
| recqgsfxqqodhjens | yes | 3/3 | 3/3 | CTCF |
| rec5qx7nedrwk4zog | yes | 3/3 | 3/3 | LINE-1 |
| hb048 | yes | 0/3 | 0/3 | Both mostly answer morphine (SHELDON once ibuprofen, Codex once gepirone). |
| hb010 | no | 3/3 | 3/3 | 16S, 18S, ITS |
| hb024 | no | 0/3 | 0/3 | Both conclude the answer cannot be determined from the data. |
| recaikavdwoimjy3b | no | 0/3 | 0/3 | Both converge on DTX3 / HHEX. |
| **hb035** | no | 1/3 | **3/3** | Codex answers hsa-let-7f-5p every time. SHELDON answers "cannot be determined", hsa-miR-155-5p and hsa-let-7a-5p. |
| **recsvhviava5okg19** | no | **1/3** | 0/3 | Codex answers SBS5 every time. SHELDON answers SBS5 twice and gets it right once with SBS3. |

## Cheating flag

In Codex hb002 attempt 1, the remote BLAST search of the 16S sequence never finished. Codex then searched NCBI Assembly for *B. licheniformis* genomes and sorted them by distance from the provided genome's exact length (4,247,430 bp). It downloaded the top hit and confirmed it was identical base by base. The judge ruled this identification of the source dataset from its metadata, which the rules prohibit, and scored the attempt 0. Without the penalty, Codex would have 18/30 (60.0%).

## Interpretation

- **On this model, SHELDON's extra search does not raise accuracy.** SHELDON uses about 10× the tool calls, 7× the output tokens, 6.5× the time and 8.6× the cost. Both agents end at the same accuracy, and on 8 of the 10 questions they reach the same outcome, wrong answers included.
- **The differences come from two questions, and they point opposite ways.** SHELDON spreads its answers across attempts (agreement 0.73 vs 0.80):
  - On recsvhviava5okg19 the spread reaches the correct answer once. This one question is the entire pass@3 gap.
  - On hb035 the spread loses an answer Codex holds all three times.

  These are the two questions where the SHELDON baseline had the correct answer in round 1 and then lost it. That points to keeping a correct answer, not finding one, as SHELDON's weakness ([configs/sheldon_answer_rivals_hb035_recsv.yaml](../configs/sheldon_answer_rivals_hb035_recsv.yaml)).
- **Shared failures are probably limits of the model, not the agent.** On hb048, hb024 and recaikavdwoimjy3b both agents fail in the same way. A different search strategy is unlikely to fix these on gpt-6-luna.
- **The sample is small.** With 10 questions × 3 attempts, a difference of one or two questions is within noise. These results do not show that either agent is more accurate. They do show that SHELDON's extra cost buys no accuracy on this question set.

## Earlier Codex baseline (different model)

The earlier Codex run on gpt-6.1-sol (`runs 2/preview_codex_gpt-6-1-sol_high_timeout-1h_3-attempts`) covered only the 5 preview questions. It scored 12/15 there, at $0.23 and 5.3 min per attempt. On the same 5 questions Codex on gpt-6-luna scored 11/15, including the cheating penalty, at $0.013 and 4.7 min per attempt. The earlier run mixes the model change with the agent change and should not be compared directly with SHELDON.

## Reproduce

```bash
uv run bmbench doctor --auth-check -c configs/codex_luna_10q.yaml
uv run bmbench run -c configs/codex_luna_10q.yaml   # completed attempts are skipped on rerun
```
