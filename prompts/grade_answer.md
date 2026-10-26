You are grading one answer to a bioinformatics benchmark question against the benchmark's official answer rubric.

<question>
{question}
</question>

<rubric>
{rubric}
</rubric>

<submitted_answer>
{answer}
</submitted_answer>

<final_message>
{final_message}
</final_message>

How to grade:
- Decide whether the submitted answer is correct according to the rubric. Ignore the rubric's clause about cheating; cheating is judged separately. Judge correctness only.
- Judge substance, not formatting. Accept case differences, synonyms, standard alternative names, and abbreviations that unambiguously refer to the same entity. Accept any alternative the rubric explicitly allows. Numbers must match exactly unless the rubric gives a tolerance.
- The answer must commit to one answer. If it hedges between, or lists, several mutually exclusive candidates, it is incorrect. When the question asks for several parts (e.g. counts and names), every part must be correct.
- `<final_message>` is context only, for example to expand an abbreviation. Do not give credit for content that is not in the submitted answer. If `<submitted_answer>` is empty, grade the answer the final message clearly commits to; if it commits to none, the answer is incorrect.

Also return `normalized_answer`: the shortest canonical form of what the agent answered, whether or not it is correct. Use an official gene symbol, a binomial name, a bare number, or a short comma-separated list for multi-part answers, with no explanation. It is used to compare answers across repeated runs, so equivalent answers must normalize identically. Use "" if there is no answer.
