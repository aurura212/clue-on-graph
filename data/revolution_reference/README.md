# Revolution Reference Base

This directory stores the new reference base for `clue_on_graph`.

## Build

From the `clue_on_graph` directory:

```bash
python reference_build/build_revolution_reference.py --dataset cwq
python reference_build/build_revolution_reference.py --dataset grailqa
python reference_build/build_revolution_reference.py --dataset WebQSP
```

Or use the wrapper script:

```bash
bash reference_build/run_build_revolution_reference.sh
```

Useful options:

```bash
--input_file   explicit training file
--output_file  explicit JSONL output file
--limit        maximum number of source samples to process
```

By default, the script reads raw training files from `data/raw_train_set/`:

```text
data/raw_train_set/ComplexWebQuestions_train.json
data/raw_train_set/WebQSP.train.json
data/raw_train_set/grailqa_v1.0_train.json
```

Use `--input_file` only when you want to override these defaults.

## Prompt Revolution Calls

The builder can call `reference_build/prompt_revolution/*.md` through the existing project
LLM interface.

```bash
python reference_build/build_revolution_reference.py \
  --dataset cwq \
  --limit 10 \
  --use_llm_gold_clue \
  --use_llm_blind \
  --openai_api_keys YOUR_KEY \
  --openai_api_base YOUR_API_BASE
```

Available switches:

```text
--use_llm_gold_clue   call gold_clue_reasoning.md
--use_llm_blind       call blind_clue_reasoning.md
--verify_blind_kg     instantiate blind blueprint with KG retrieval
--use_llm_evaluation  call evaluate_blind_reference.md
--use_llm_memory      call memory_item.md
```

Usually, start with `--use_llm_gold_clue` and `--use_llm_blind`.
Then add `--verify_blind_kg`, and finally add `--use_llm_evaluation`
and `--use_llm_memory` after retrieval results have been filled.

Full closed-loop example:

```bash
python reference_build/build_revolution_reference.py \
  --dataset cwq \
  --limit 10 \
  --use_llm_gold_clue \
  --use_llm_blind \
  --verify_blind_kg \
  --kg_verify_mode direct \
  --use_llm_evaluation \
  --use_llm_memory \
  --openai_api_keys YOUR_KEY \
  --openai_api_base YOUR_API_BASE
```

## Output Schema

Each line is one JSON object:

```json
{
  "id": "...",
  "dataset": "...",
  "question": "...",
  "masked_question": "...",
  "topic_entities": [{"id": "m.xxx", "label": "..."}],
  "answers": [{"id": "m.xxx", "label": "..."}],
  "gold": {
    "blueprint": ["relation.a", "relation.b"],
    "relation_path": "relation.a -> relation.b",
    "clue_reasoning": ["..."],
    "sparql": "...",
    "s_expression": "...",
    "source": "sparql_regex"
  },
  "blind": {
    "blueprint": [],
    "clue_reasoning": [],
    "retrieval_result": [],
    "hit": null
  },
  "evaluation": {
    "score": null,
    "correct_reason": "",
    "error_step": "",
    "correction": "",
    "guardrail": ""
  },
  "memory_item": {
    "title": "",
    "description": "",
    "content": ""
  }
}
```

## Current Stage

The current builder implements the first stage:

- Load training examples.
- Extract gold relation skeletons from SPARQL using CoG-style regex matching.
- Fall back to `graph_query.edges` when SPARQL is unavailable.
- Write the revolution-style JSONL skeleton.
- Optionally call `prompt_revolution` templates to enrich gold clue reasoning,
  blind clue reasoning, evaluation, and memory fields.
- Optionally verify blind relation blueprints through `kg_instantiation.py`
  relation binding and BFS path instantiation.

`--kg_verify_mode direct` is the default. It uses
`utils/freebase_func.py` to query Freebase directly for each predicted
relation step in both forward and backward directions. Use
`--kg_verify_mode instantiation` only when the Pyserini relation binding
indexes required by `kg_instantiation.py` are available.

The following fields are reserved for later stages:

- `blind`
- `evaluation`
- `memory_item`

Prompt templates for these optional stages are in `reference_build/prompt_revolution/`.
