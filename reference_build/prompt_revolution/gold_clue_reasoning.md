Task: Convert a gold relation blueprint into concise clue reasoning steps for KGQA.

Input fields:
- Question
- Topic entities
- Gold relation blueprint

Output a JSON object only:
{
  "clue_reasoning": [
    "short natural-language clue step aligned with the first relation",
    "short natural-language clue step aligned with the second relation"
  ]
}

Question: {question}
Topic entities: {topic_entities}
Gold relation blueprint: {blueprint}
