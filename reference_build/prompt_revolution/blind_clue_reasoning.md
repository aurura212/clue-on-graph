Task: Predict a clue reasoning process and relation blueprint for KGQA without using any reference examples.

Use only the question and topic entities. Do not use gold answers, gold SPARQL, or retrieved references.

Output a JSON object only:
{
  "blueprint": ["relation.or.semantic_step_1", "relation.or.semantic_step_2"],
  "clue_reasoning": ["short clue step 1", "short clue step 2"],
  "answer_type": "entity | value | date | count | unknown",
  "constraints": ["short constraint if any"]
}

Question: {question}
Topic entities: {topic_entities}
