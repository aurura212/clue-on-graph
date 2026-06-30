"""Evaluate PoG test run results and write metrics to run_meta.json."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
from typing import Any

EVAL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "eval"))
_eval_utils_path = os.path.join(EVAL_DIR, "utils.py")
_eval_utils_spec = importlib.util.spec_from_file_location("pog_eval_utils", _eval_utils_path)
_eval_utils = importlib.util.module_from_spec(_eval_utils_spec)
_eval_utils_spec.loader.exec_module(_eval_utils)

align = _eval_utils.align
calculate_f1 = _eval_utils.calculate_f1
dataset_align_key = _eval_utils.dataset_align_key
dataset_type_field = _eval_utils.dataset_type_field
exact_match = _eval_utils.exact_match
load_eval_aliases = _eval_utils.load_eval_aliases
resolve_dataset_path = _eval_utils.resolve_dataset_path

from jsonl_io import iter_jsonl_records  # noqa: E402


def _update_run_meta(meta_path: str, updates: dict[str, Any]) -> None:
    from datetime import datetime

    meta: dict[str, Any] = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta.update(updates)
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=4)


def _question_string_for_dataset(dataset_name: str) -> str:
    if dataset_align_key(dataset_name) == "webqsp":
        return "RawQuestion"
    return "question"


def _load_ground_truth(dataset_name: str) -> tuple[list[dict[str, Any]], str]:
    dataset_path = resolve_dataset_path(dataset_name)
    with open(dataset_path, encoding="utf-8") as f:
        datas = json.load(f)
    return datas, _question_string_for_dataset(dataset_name)


def _load_output_records(results_path: str, question_string: str) -> list[dict[str, Any]]:
    if not os.path.exists(results_path):
        return []
    return list(iter_jsonl_records(results_path))


def _extract_prediction(results_str: str) -> tuple[Any, str]:
    """Return (raw_prediction, string_for_f1) parsed from a results field."""
    if not results_str:
        return None, ""

    start_i = results_str.find("{")
    if start_i != -1:
        try:
            parsed = json.loads(results_str[start_i:])
            if "A" in parsed:
                response = parsed["A"].get("Answer")
            else:
                response = parsed.get("Answer")
            if isinstance(response, list):
                return response, ",".join(str(x) for x in response)
            return response, str(response)
        except json.JSONDecodeError:
            pattern = r'"Answer":\s*["\']([^"\']+)["\']'
            match_ = list(re.finditer(pattern, results_str[start_i:]))
            if match_:
                response = match_[-1].group(1)
                return response, str(response)

            pattern = r'"Answer":\s*(\[[^\]]+\])'
            match_ = re.search(pattern, results_str[start_i:])
            if match_:
                list_obj = ast.literal_eval(match_.group(1))
                return list_obj, ",".join(str(x) for x in list_obj)

            response = results_str
            return response, str(response)

    response = results_str
    return response, str(response)


def _is_exact_match(prediction: Any, answers: list[str]) -> bool:
    if isinstance(prediction, list):
        for item in prediction:
            if exact_match(str(item), answers):
                return True
        return False
    if prediction is None:
        return False
    return exact_match(str(prediction), answers)


def evaluate_run_results(
    dataset_name: str,
    results_path: str,
) -> dict[str, Any]:
    ground_truth_datas, question_string = _load_ground_truth(dataset_name)
    output_datas = _load_output_records(results_path, question_string)
    aname_dict, alias_dict, add_ans_alias_dict = load_eval_aliases(dataset_name)
    type_field = dataset_type_field(dataset_name)

    num_right = 0
    num_error = 0
    total_precision = 0.0
    total_recall = 0.0
    total_f1 = 0.0
    num_questions_f1 = 0
    count_q: dict[str, int] = {}
    right_q: dict[str, int] = {}
    call_num_list: list[int] = []
    time_list: list[float] = []
    token_num_list = {"input": [], "output": [], "total": []}
    error_questions: list[str] = []

    for data in output_datas:
        answers, ori_data = align(
            dataset_name,
            question_string,
            data,
            ground_truth_datas,
            aname_dict,
            alias_dict,
            add_ans_alias_dict,
        )

        if "time" in data:
            call_num_list.append(data.get("call_num", 0))
            time_list.append(data.get("time", 0.0))
            token_num_list["input"].append(data.get("input_token", 0))
            token_num_list["output"].append(data.get("output_token", 0))
            token_num_list["total"].append(data.get("total_token", 0))

        if type_field:
            q_type = ori_data.get(type_field, "unknown")
            count_q[q_type] = count_q.get(q_type, 0) + 1

        prediction, pred_str_for_f1 = _extract_prediction(data.get("results", ""))
        is_correct = _is_exact_match(prediction, answers)
        if is_correct:
            num_right += 1
            if type_field:
                q_type = ori_data.get(type_field, "unknown")
                right_q[q_type] = right_q.get(q_type, 0) + 1
        else:
            num_error += 1
            error_questions.append(data[question_string])

        if pred_str_for_f1:
            f1, precision, recall = calculate_f1(pred_str_for_f1, answers)
            total_precision += precision
            total_recall += recall
            total_f1 += f1
            num_questions_f1 += 1

    total = len(output_datas)
    by_question_type: dict[str, dict[str, Any]] = {}
    if type_field:
        for q_type, count in sorted(count_q.items()):
            correct = right_q.get(q_type, 0)
            by_question_type[q_type] = {
                "total": count,
                "correct": correct,
                "exact_match": round(correct / count, 4) if count else 0.0,
            }

    def _avg(values: list[float | int]) -> float | None:
        return sum(values) / len(values) if values else None

    metrics: dict[str, Any] = {
        "total": total,
        "correct": num_right,
        "wrong": num_error,
        "exact_match": round(num_right / total, 4) if total else 0.0,
        "f1": round(total_f1 / num_questions_f1, 4) if num_questions_f1 else 0.0,
        "precision": round(total_precision / num_questions_f1, 4) if num_questions_f1 else 0.0,
        "recall": round(total_recall / num_questions_f1, 4) if num_questions_f1 else 0.0,
        "by_question_type": by_question_type,
        "avg_call_num": round(_avg(call_num_list), 2) if _avg(call_num_list) is not None else None,
        "avg_time_sec": round(_avg(time_list), 2) if _avg(time_list) is not None else None,
        "avg_tokens": {
            "input": round(_avg(token_num_list["input"]), 1) if _avg(token_num_list["input"]) is not None else None,
            "output": round(_avg(token_num_list["output"]), 1) if _avg(token_num_list["output"]) is not None else None,
            "total": round(_avg(token_num_list["total"]), 1) if _avg(token_num_list["total"]) is not None else None,
        },
        "error_questions": error_questions,
    }
    return metrics


def run_post_test_evaluation(args, run_output: dict[str, Any]) -> dict[str, Any]:
    if not run_output:
        print("Skip evaluation: no run output configured.")
        return {}

    results_path = run_output["results_path"]
    if not os.path.exists(results_path):
        print(f"Skip evaluation: results file not found: {results_path}")
        return {}

    metrics = evaluate_run_results(args.dataset, results_path)
    meta_path = run_output.get("meta_path")
    if meta_path:
        _update_run_meta(meta_path, {"evaluation": metrics})

    print("Evaluation finished.")
    print(f"  Total: {metrics['total']}")
    print(f"  Exact Match: {metrics['exact_match']:.4f} ({metrics['correct']}/{metrics['total']})")
    print(f"  F1: {metrics['f1']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall: {metrics['recall']:.4f}")
    if metrics.get("by_question_type"):
        print("  By question type:")
        for q_type, stats in metrics["by_question_type"].items():
            print(f"    {q_type}: {stats['exact_match']:.4f} ({stats['correct']}/{stats['total']})")
    if metrics.get("avg_call_num") is not None:
        print(f"  Avg call_num: {metrics['avg_call_num']:.2f}")
    if metrics.get("avg_time_sec") is not None:
        print(f"  Avg time (sec): {metrics['avg_time_sec']:.2f}")
    avg_tokens = metrics.get("avg_tokens") or {}
    if avg_tokens.get("total") is not None:
        print(
            "  Avg tokens: "
            f"input={avg_tokens['input']:.1f}, "
            f"output={avg_tokens['output']:.1f}, "
            f"total={avg_tokens['total']:.1f}"
        )
    return metrics
