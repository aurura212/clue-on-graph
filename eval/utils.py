import json
import os
import sys

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(EVAL_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
COPE_ALIAS_DIR = os.path.join(PROJECT_ROOT, "cope_alias")

POG_DIR = os.path.join(PROJECT_ROOT, "PoG")
if POG_DIR not in sys.path:
    sys.path.insert(0, POG_DIR)

from jsonl_io import iter_jsonl_records

DATASET_FILES = {
    "cwq": os.path.join(DATA_DIR, "cwq.json"),
    "webqsp": os.path.join(DATA_DIR, "WebQSP.json"),
    "webqsp_split": os.path.join(DATA_DIR, "WebQSP_split.json"),
    "grailqa": os.path.join(DATA_DIR, "grailqa.json"),
    "grailqa_split": os.path.join(DATA_DIR, "grailqa_split.json"),
}

DATASET_ALTERNATE_FILES = {
    "grailqa_split": os.path.join(DATA_DIR, "datasets", "grailqa_split.json"),
}


def normalize_dataset_name(dataset_name: str) -> str:
    lowered = dataset_name.lower().strip()
    if lowered in DATASET_FILES:
        return lowered
    if lowered.startswith("cwq"):
        return "cwq"
    if lowered.startswith("grailqa"):
        return "grailqa_split" if "split" in lowered else "grailqa"
    if lowered.startswith("webqsp"):
        return "webqsp_split" if "split" in lowered else "webqsp"
    return lowered


def dataset_align_key(dataset_name: str) -> str:
    name = normalize_dataset_name(dataset_name)
    if name.startswith("grailqa"):
        return "grailqa"
    if name.startswith("webqsp"):
        return "webqsp"
    if name.startswith("cwq"):
        return "cwq"
    return name


def dataset_type_field(dataset_name: str) -> str:
    key = dataset_align_key(dataset_name)
    if key == "cwq":
        return "compositionality_type"
    if key == "grailqa":
        return "level"
    return ""


def resolve_dataset_path(dataset_name: str) -> str:
    name = normalize_dataset_name(dataset_name)
    path = DATASET_FILES.get(name)
    if path and os.path.isfile(path):
        return path
    alt = DATASET_ALTERNATE_FILES.get(name)
    if alt and os.path.isfile(alt):
        return alt
    tried = [p for p in (path, alt) if p]
    raise FileNotFoundError(f"Dataset file not found for {dataset_name}. Tried: {tried}")


def load_eval_aliases(dataset_name: str) -> tuple[dict, dict, dict]:
    """Load alias maps used by align(). GrailQA has no alias files."""
    alias_dict: dict = {}
    add_ans_alias_dict: dict = {}
    aname_dict: dict = {}
    key = dataset_align_key(dataset_name)

    if key == "cwq":
        with open(os.path.join(COPE_ALIAS_DIR, "cwq_aname_dict.json"), encoding="utf-8") as f:
            aname_dict = json.load(f)
        with open(os.path.join(COPE_ALIAS_DIR, "CWQ_aliase_data31158.json"), encoding="utf-8") as f:
            alias_dict = json.load(f)
        with open(os.path.join(COPE_ALIAS_DIR, "ComplexWebQuestions_test_wans.json"), encoding="utf-8") as f:
            for q_item in json.load(f):
                ans_list = []
                for ans_item in q_item["answers"]:
                    if ans_item["answer"]:
                        ans_list.append(ans_item["answer"])
                    else:
                        ans_list.append(ans_item["answer_id"])
                    if "aliases" in ans_item:
                        ans_list += ans_item["aliases"]
                add_ans_alias_dict[q_item["question"]] = ans_list
    elif key == "webqsp":
        with open(os.path.join(COPE_ALIAS_DIR, "WQSP_aliase_data.json"), encoding="utf-8") as f:
            alias_dict = json.load(f)

    return aname_dict, alias_dict, add_ans_alias_dict

def read_output(file_path, question_string):
    answered_dict = {}
    if not file_path.endswith('.jsonl'):
        if os.path.isdir(file_path):
            file_path = os.path.join(file_path, "results.jsonl")
        elif not os.path.exists(file_path):
            under_result = os.path.join(POG_DIR, "result", file_path, "results.jsonl")
            if os.path.exists(under_result):
                file_path = under_result
            else:
                file_path = file_path + '.jsonl'
    trace_path = file_path.replace("results.jsonl", "pog_trace.jsonl")
    if os.path.exists(file_path):
        for data in iter_jsonl_records(file_path):
            answered_dict[data[question_string]] = data

    if os.path.exists(trace_path):
        for tdata in iter_jsonl_records(trace_path):
            q = tdata.get(question_string) or tdata.get("question")
            if q in answered_dict:
                answered_dict[q]["pog_trace"] = tdata.get("pog_trace")

    answered_list = list(answered_dict.values())
    return answered_list

def prepare_dataset_for_eval(dataset_name, output_file):
    dataset_path = resolve_dataset_path(dataset_name)
    with open(dataset_path, encoding="utf-8") as f:
        datas = json.load(f)

    key = dataset_align_key(dataset_name)
    if key == "webqsp":
        question_string = "RawQuestion"
    else:
        question_string = "question"

    output_datas = read_output(output_file, question_string)
    return datas, question_string, output_datas


def _find_origin(ground_truth_datas, question_string, question):
    matches = [j for j in ground_truth_datas if j[question_string] == question]
    if not matches:
        raise KeyError(f"Question not found in ground truth: {question!r}")
    return matches[0]


def align(dataset_name, question_string, data, ground_truth_datas, aname_dict, alias_dict, add_ans_alias_dict):
    answer_list = []
    align_key = dataset_align_key(dataset_name)
    origin_data = _find_origin(ground_truth_datas, question_string, data[question_string])

    if align_key == "cwq":
        add_data = list(aname_dict.get(data[question_string], []))
        add_ans_alias_data = list(add_ans_alias_dict.get(data[question_string], []))
        add_data += add_ans_alias_data
        if "answers" in origin_data:
            answers = origin_data["answers"]
        else:
            answers = origin_data["answer"]
        if isinstance(answers, list):
            for ans in answers:
                if ans not in add_data:
                    add_data.append(ans)
        elif answers not in add_data:
            add_data.append(answers)

        answer_list = add_data
        alias_list = []
        for x in answer_list:
            if x in alias_dict:
                alias_list += alias_dict[x]

        answer_list = list(set(answer_list) | set(alias_list))

    elif align_key == "webqsp":
        answers = origin_data["Parses"]
        for answer in answers:
            for name in answer["Answers"]:
                if name["EntityName"] is None:
                    answer_list.append(name["AnswerArgument"])
                else:
                    answer_list.append(name["EntityName"])

        alias_list = []
        for x in answer_list:
            if x in alias_dict:
                alias_list += alias_dict[x]

        answer_list = list(set(answer_list) | set(alias_list))

    elif align_key == "grailqa":
        for answer in origin_data.get("answer", []):
            entity_name = answer.get("entity_name")
            if entity_name:
                answer_list.append(entity_name)
            arg = answer.get("answer_argument")
            if arg:
                answer_list.append(arg)

    return list(set(answer_list)), origin_data
    

def exact_match(response, answers):
    clean_result = response.strip().replace(" ","").lower()
    for answer in answers:
        clean_answer = answer.strip().replace(" ","").lower()
        if clean_result == clean_answer or clean_result in clean_answer or clean_answer in clean_result:
            return True
    return False



def calculate_f1(prediction, answers):

    if len(prediction) == 0:
        return 0, 0, 0
    matched = 0
    p_matched=0
    prediction_str = ' '.join(prediction)
    for a in answers:
        if exact_match(prediction_str, a):
            matched += 1
    prediction_parts = [p.strip() for p in prediction.split(',') if p.strip()]
    if not prediction_parts:
        return 0, 0, 0
    for part in prediction_parts:
        if exact_match(part,answers):
            p_matched+=1
    precision = p_matched / len(prediction_parts) if len(prediction_parts)>0 else 0
    recall = matched / len(answers) if len(answers)>0 else 0
    if precision + recall == 0:
        return 0, precision, recall
    else:
        return 2 * precision * recall / (precision + recall), precision, recall
