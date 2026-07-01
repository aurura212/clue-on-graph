#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Required: export OPENAI_API_KEY=...
YOUR_KEY="${OPENAI_API_KEY:-}"
OPENAI_API_BASE="${OPENAI_API_BASE:-https://cn2us02.opapi.win/v1}"

GPU_IDS="${GPU_IDS:-3}"
DEVICE="${LIGHTMEM_DEVICE:-cuda}"
DATASET="${DATASET:-webqsp}"
LLM_TYPE="${LLM_TYPE:-gpt-3.5-turbo-0125}"

RUN_MODE="${RUN_MODE:-train}"
SPLIT="${SPLIT:-train}"
START="${START:-0}"
LIMIT="${LIMIT:-1800}"
QUESTION="${QUESTION:-}"

MAX_LENGTH="${MAX_LENGTH:-4096}"
TEMPERATURE_EXPLORATION="${TEMPERATURE_EXPLORATION:-0.3}"
TEMPERATURE_REASONING="${TEMPERATURE_REASONING:-0.3}"
DEPTH="${DEPTH:-4}"
REMOVE_UNNECESSARY_REL="${REMOVE_UNNECESSARY_REL:-True}"

REFERENCE_MODE="${REFERENCE_MODE:-none}"
REFERENCE_BASE_PATH="${REFERENCE_BASE_PATH:-}"
REFERENCE_LIMIT="${REFERENCE_LIMIT:--1}"
REFERENCE_TOP_K="${REFERENCE_TOP_K:-4}"
REFERENCE_STAGES="${REFERENCE_STAGES:-relation}"
RANDOM_KNOWLEDGE="${RANDOM_KNOWLEDGE:-0}"

# relation_choice | experience | all
TRAIN_MEMORY_FAMILY="${TRAIN_MEMORY_FAMILY:-all}"

# relation_choice memory options
RELATION_MEMORY_TYPE="${RELATION_MEMORY_TYPE:-relation_choice}"
RELATION_MEMORY_OUTPUT_PATH="${RELATION_MEMORY_OUTPUT_PATH:-}"
TRAIN_FOLLOWUP_POLICY="${TRAIN_FOLLOWUP_POLICY:-stop_if_correct}"
WRITE_MISSED_POSITIVE="${WRITE_MISSED_POSITIVE:-1}"

# experience memory output paths. Leave empty to use default separated directories:
# PoG/relation_memory/evidence_state/
# PoG/relation_memory/failure_reflection/
# PoG/relation_memory/correction_action/
EVIDENCE_STATE_MEMORY_OUTPUT_PATH="${EVIDENCE_STATE_MEMORY_OUTPUT_PATH:-}"
FAILURE_REFLECTION_MEMORY_OUTPUT_PATH="${FAILURE_REFLECTION_MEMORY_OUTPUT_PATH:-}"
CORRECTION_ACTION_MEMORY_OUTPUT_PATH="${CORRECTION_ACTION_MEMORY_OUTPUT_PATH:-}"

# Retrieval / prompt options retained for train-time relation pruning.
RELATION_MEMORY_MODE="${RELATION_MEMORY_MODE:-none}"
RELATION_MEMORY_STAGES="${RELATION_MEMORY_STAGES:-relation}"
RELATION_MEMORY_PATH="${RELATION_MEMORY_PATH:-}"
RELATION_MEMORY_TOP_K="${RELATION_MEMORY_TOP_K:-4}"
MEMORY_RETRIEVAL_STRATEGY="${MEMORY_RETRIEVAL_STRATEGY:-hybrid}"
MEMORY_STATE_WEIGHT="${MEMORY_STATE_WEIGHT:-0.5}"
MEMORY_LABELS="${MEMORY_LABELS:-positive,missed_positive,negative}"
MEMORY_PROMPT_TOKEN_BUDGET="${MEMORY_PROMPT_TOKEN_BUDGET:-600}"
MEMORY_CANDIDATE_RELATION_LIMIT="${MEMORY_CANDIDATE_RELATION_LIMIT:-8}"

GOLD_FRONTIER_LIMIT="${GOLD_FRONTIER_LIMIT:-50}"
RELATION_SEMANTIC_TOP_K="${RELATION_SEMANTIC_TOP_K:-100}"
RUN_DIR="${RUN_DIR:-}"

export CUDA_VISIBLE_DEVICES="$GPU_IDS"
export OPENAI_API_BASE="$OPENAI_API_BASE"
export LIGHTMEM_DEVICE="$DEVICE"

if [ -z "$YOUR_KEY" ]; then
  echo "Please set OPENAI_API_KEY before running this script."
  exit 1
fi

if [[ "$TRAIN_MEMORY_FAMILY" != "relation_choice" && "$TRAIN_MEMORY_FAMILY" != "experience" && "$TRAIN_MEMORY_FAMILY" != "all" ]]; then
  echo "TRAIN_MEMORY_FAMILY must be one of: relation_choice, experience, all"
  exit 1
fi

args=(
  --dataset "$DATASET"
  --run_mode "$RUN_MODE"
  --split "$SPLIT"
  --max_length "$MAX_LENGTH"
  --temperature_exploration "$TEMPERATURE_EXPLORATION"
  --temperature_reasoning "$TEMPERATURE_REASONING"
  --depth "$DEPTH"
  --remove_unnecessary_rel "$REMOVE_UNNECESSARY_REL"
  --LLM_type "$LLM_TYPE"
  --opeani_api_keys "$YOUR_KEY"
  --openai_api_base "$OPENAI_API_BASE"
  --reference_mode "$REFERENCE_MODE"
  --reference_limit "$REFERENCE_LIMIT"
  --reference_top_k "$REFERENCE_TOP_K"
  --reference_stages "$REFERENCE_STAGES"
  --random_knowledge "$RANDOM_KNOWLEDGE"
  --relation_memory_mode "$RELATION_MEMORY_MODE"
  --relation_memory_stages "$RELATION_MEMORY_STAGES"
  --relation_memory_top_k "$RELATION_MEMORY_TOP_K"
  --memory_retrieval_strategy "$MEMORY_RETRIEVAL_STRATEGY"
  --memory_state_weight "$MEMORY_STATE_WEIGHT"
  --memory_labels "$MEMORY_LABELS"
  --memory_prompt_token_budget "$MEMORY_PROMPT_TOKEN_BUDGET"
  --memory_candidate_relation_limit "$MEMORY_CANDIDATE_RELATION_LIMIT"
  --train_memory_family "$TRAIN_MEMORY_FAMILY"
  --relation_memory_type "$RELATION_MEMORY_TYPE"
  --train_followup_policy "$TRAIN_FOLLOWUP_POLICY"
  --gold_frontier_limit "$GOLD_FRONTIER_LIMIT"
  --write_missed_positive "$WRITE_MISSED_POSITIVE"
  --relation_semantic_top_k "$RELATION_SEMANTIC_TOP_K"
  --start "$START"
  --limit "$LIMIT"
)

if [ -n "$QUESTION" ]; then
  args+=(--question "$QUESTION")
fi
if [ -n "$REFERENCE_BASE_PATH" ]; then
  args+=(--reference_base_path "$REFERENCE_BASE_PATH")
fi
if [ -n "$RELATION_MEMORY_PATH" ]; then
  args+=(--relation_memory_path "$RELATION_MEMORY_PATH")
fi
if [ -n "$RUN_DIR" ]; then
  args+=(--run_dir "$RUN_DIR")
fi
if [ -n "$RELATION_MEMORY_OUTPUT_PATH" ]; then
  args+=(--relation_memory_output_path "$RELATION_MEMORY_OUTPUT_PATH")
fi
if [ -n "$EVIDENCE_STATE_MEMORY_OUTPUT_PATH" ]; then
  args+=(--evidence_state_memory_output_path "$EVIDENCE_STATE_MEMORY_OUTPUT_PATH")
fi
if [ -n "$FAILURE_REFLECTION_MEMORY_OUTPUT_PATH" ]; then
  args+=(--failure_reflection_memory_output_path "$FAILURE_REFLECTION_MEMORY_OUTPUT_PATH")
fi
if [ -n "$CORRECTION_ACTION_MEMORY_OUTPUT_PATH" ]; then
  args+=(--correction_action_memory_output_path "$CORRECTION_ACTION_MEMORY_OUTPUT_PATH")
fi

echo "Running PoG train mode: $TRAIN_MEMORY_FAMILY"
python main_freebase.py "${args[@]}"
