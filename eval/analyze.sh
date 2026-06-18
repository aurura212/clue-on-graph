#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"

YOUR_KEY="sk-a767b29113b84128bfe4b876756f1894"
DATASET="cwq"
# Run folder under PoG/result/, or path to results.jsonl
RUN_FOLDER="cwq_gpt-3.5-turbo-0125_cog_top2_limit100_stages-relation_n450_20260618_005346"
USE_LLM="1"
LLM_TYPE="deepseek-v4-pro"
FILTER="all"   # all | wrong | correct
QUESTION_LIMIT=-1

# 注意：必须是完整 URL，不要写成 "$https://..."
export OPENAI_API_BASE="https://api.deepseek.com"
# 若直连 DeepSeek 不通，改用代理：
# export OPENAI_API_BASE="https://cn2us02.opapi.win/v1"

RUN_TAG="${RUN_FOLDER##*/}"
RUN_TAG="${RUN_TAG%.jsonl}"
RUN_TAG="${RUN_TAG%/}"
DIAG_DIR="${PROJECT_ROOT}/pog_diagnosis/${RUN_TAG}"
OUT_REPORT="${DIAG_DIR}/${RUN_TAG}.jsonl"
OUT_SUMMARY="${DIAG_DIR}/${RUN_TAG}_summary.json"

mkdir -p "${DIAG_DIR}"

ARGS=(
  --dataset "$DATASET"
  --output_file "${PROJECT_ROOT}/PoG/result/${RUN_FOLDER}"
  --out_report "$OUT_REPORT"
  --out_summary "$OUT_SUMMARY"
  --filter "$FILTER"
)

if [ "$QUESTION_LIMIT" -ge 0 ] 2>/dev/null; then
  ARGS+=(--limit "$QUESTION_LIMIT")
fi

if [ "$USE_LLM" = "1" ]; then
  if [ "$YOUR_KEY" = "YOUR_KEY" ]; then
    echo "Please set YOUR_KEY before running LLM attribution (--use_llm)."
    exit 1
  fi
  echo "LLM attribution: ON (model=${LLM_TYPE})"
  ARGS+=(
    --use_llm
    --LLM_type "$LLM_TYPE"
    --openai_api_keys "$YOUR_KEY"
  )
else
  echo "LLM attribution: OFF (set USE_LLM=1 to enable)"
fi

python analyze_pog.py "${ARGS[@]}"
