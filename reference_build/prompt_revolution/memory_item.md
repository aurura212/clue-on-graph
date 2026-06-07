Task: Extract one reusable ReasoningBank-style memory item from a KGQA reference example.

Output a JSON object only:
{
  "title": "short strategy or guardrail name",
  "description": "one-sentence description",
  "content": "generalizable rule, strategy, or failure-prevention note"
}

Question: {question}
Gold blueprint: {gold_blueprint}
Blind blueprint: {blind_blueprint}
Evaluation: {evaluation}
