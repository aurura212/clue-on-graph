Task: Compare a blind KGQA clue reasoning process with the gold blueprint and retrieval result.

Output a JSON object only:
{
  "score": 0,
  "correct_reason": "why the blind process can retrieve the answer, if correct",
  "error_step": "the first wrong or missing step, if wrong",
  "correction": "a corrected blueprint or clue reasoning suggestion",
  "guardrail": "a reusable warning for future similar questions"
}

Scoring:
- 100 means the blind blueprint is fully aligned with the gold blueprint and retrieves the answer.
- 50 means it is partially aligned but misses an important relation, direction, or constraint.
- 0 means it is unrelated or cannot support retrieval.

Question: {question}
Topic entities: {topic_entities}
Gold blueprint: {gold_blueprint}
Gold clue reasoning: {gold_clue_reasoning}
Blind blueprint: {blind_blueprint}
Blind clue reasoning: {blind_clue_reasoning}
Retrieval result: {retrieval_result}
Hit gold answer: {hit}
