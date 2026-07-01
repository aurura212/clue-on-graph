#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

YOUR_KEY="${OPENAI_API_KEY:-}"
OPENAI_API_BASE="${OPENAI_API_BASE:-https://cn2us02.opapi.win/v1}"
GPU_IDS="${GPU_IDS:-2}"
DEVICE="${LIGHTMEM_DEVICE:-cuda}"
DATASET="${DATASET:-webqsp}"
LLM_TYPE="${LLM_TYPE:-gpt-3.5-turbo-0125}"
RUN_MODE="${RUN_MODE:-test}"
SPLIT="${SPLIT:-test}"
LIMIT="${LIMIT:-300}"
START="${START:-0}"
QUESTION="${QUESTION:-}"
RUN_DIR="${RUN_DIR:-}"

REFERENCE_MODE="${REFERENCE_MODE:-none}"
REFERENCE_BASE_PATH="${REFERENCE_BASE_PATH:-../data/revolution_reference/webqsp_reference_limit100.jsonl}"
REFERENCE_LIMIT="${REFERENCE_LIMIT:--1}"
REFERENCE_TOP_K="${REFERENCE_TOP_K:-4}"
REFERENCE_STAGES="${REFERENCE_STAGES:-relation}"
RANDOM_KNOWLEDGE="${RANDOM_KNOWLEDGE:-0}"

RELATION_MEMORY_MODE="${RELATION_MEMORY_MODE:-none}"
RELATION_MEMORY_STAGES="${RELATION_MEMORY_STAGES:-relation}"
RELATION_MEMORY_PATH="${RELATION_MEMORY_PATH:-relation_memory/webqsp_gpt-3.5-turbo-0125_train_n1800_20260630_004723.jsonl}"
RELATION_MEMORY_TOP_K="${RELATION_MEMORY_TOP_K:-4}"
MEMORY_RETRIEVAL_STRATEGY="${MEMORY_RETRIEVAL_STRATEGY:-hybrid}"
MEMORY_STATE_WEIGHT="${MEMORY_STATE_WEIGHT:-0}"
MEMORY_LABELS="${MEMORY_LABELS:-positive,missed_positive,negative}"
MEMORY_PROMPT_TOKEN_BUDGET="${MEMORY_PROMPT_TOKEN_BUDGET:-600}"
MEMORY_CANDIDATE_RELATION_LIMIT="${MEMORY_CANDIDATE_RELATION_LIMIT:-10}"
RELATION_SEMANTIC_TOP_K="${RELATION_SEMANTIC_TOP_K:-40}"
EVIDENCE_STATE_MEMORY_PATH="${EVIDENCE_STATE_MEMORY_PATH:-}"
FAILURE_REFLECTION_MEMORY_PATH="${FAILURE_REFLECTION_MEMORY_PATH:-}"
CORRECTION_ACTION_MEMORY_PATH="${CORRECTION_ACTION_MEMORY_PATH:-}"

export CUDA_VISIBLE_DEVICES="$GPU_IDS"
export OPENAI_API_BASE="$OPENAI_API_BASE"
export LIGHTMEM_DEVICE="$DEVICE"

if [ -z "$YOUR_KEY" ]; then
  echo "Please set OPENAI_API_KEY before running this script."
  exit 1
fi

echo "Running PoG test mode on ${DATASET} (${SPLIT})"
args=(
  --dataset "$DATASET"
  --run_mode "$RUN_MODE"
  --split "$SPLIT"
  --max_length 4096
  --temperature_exploration 0.3
  --temperature_reasoning 0.3
  --depth 4
  --remove_unnecessary_rel True
  --LLM_type "$LLM_TYPE"
  --opeani_api_keys "$YOUR_KEY"
  --openai_api_base "$OPENAI_API_BASE"
  --reference_mode "$REFERENCE_MODE"
  --reference_base_path "$REFERENCE_BASE_PATH"
  --reference_limit "$REFERENCE_LIMIT"
  --reference_top_k "$REFERENCE_TOP_K"
  --reference_stages "$REFERENCE_STAGES"
  --random_knowledge "$RANDOM_KNOWLEDGE"
  --relation_memory_mode "$RELATION_MEMORY_MODE"
  --relation_memory_stages "$RELATION_MEMORY_STAGES"
  --relation_memory_path "$RELATION_MEMORY_PATH"
  --relation_memory_top_k "$RELATION_MEMORY_TOP_K"
  --memory_retrieval_strategy "$MEMORY_RETRIEVAL_STRATEGY"
  --memory_state_weight "$MEMORY_STATE_WEIGHT"
  --memory_labels "$MEMORY_LABELS"
  --memory_prompt_token_budget "$MEMORY_PROMPT_TOKEN_BUDGET"
  --memory_candidate_relation_limit "$MEMORY_CANDIDATE_RELATION_LIMIT"
  --relation_semantic_top_k "$RELATION_SEMANTIC_TOP_K"
  --start "$START"
  --limit "$LIMIT"
)

if [ -n "$QUESTION" ]; then
  args+=(--question "$QUESTION")
fi
if [ -n "$RUN_DIR" ]; then
  args+=(--run_dir "$RUN_DIR")
fi
if [ -n "$EVIDENCE_STATE_MEMORY_PATH" ]; then
  args+=(--evidence_state_memory_path "$EVIDENCE_STATE_MEMORY_PATH")
fi
if [ -n "$FAILURE_REFLECTION_MEMORY_PATH" ]; then
  args+=(--failure_reflection_memory_path "$FAILURE_REFLECTION_MEMORY_PATH")
fi
if [ -n "$CORRECTION_ACTION_MEMORY_PATH" ]; then
  args+=(--correction_action_memory_path "$CORRECTION_ACTION_MEMORY_PATH")
fi

python main_freebase.py "${args[@]}"
