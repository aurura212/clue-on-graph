import sys
import os
import re
import faiss
from argparse import ArgumentParser
from tqdm import tqdm
import numpy as np
from config import LLM_BASE
import json
import random
sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/..")
from utils.utils import run_llm, get_timestamp, readjson
from utils.freebase_func import *
import time
from tqdm import tqdm
from utils import *
from config import *
from kg_instantiation import *
import tiktoken

os.environ['CUDA_VISIBLE_DEVICES'] = '0'
os.environ['OPENAI_API_BASE'] = "https://cn2us02.opapi.win/v1"#"https://jeniya.cn/v1" "https://ai-yyds.com/v1"
PROMPT_PATH = "src/prompt_md"
enc_model = SentenceTransformer("./all-MiniLM-L6-v2")


def parse_args():
    parser = ArgumentParser("KGQA for cwq or WebQSP")
    parser.add_argument("--full", action="store_true", help="full dataset.")
    parser.add_argument("--verbose", action="store_true", help="verbose or not.", default=False)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max_token", type=int, default=1024)
    parser.add_argument("--max_token_reasoning", type=int, default=2048)
    parser.add_argument("--max_que", type=int, default=200)
    parser.add_argument("--dataset", type=str, required=True, help="choose the dataset.choices={\"cwq\", \"WebQSP\", \"grailqa\"}")
    parser.add_argument("--a", type=int, default=0)
    parser.add_argument("--b", type=int, default=-1)
    parser.add_argument("--relation_check", type=int, default=1)
    parser.add_argument("--use_prune", type=int, default=1)
    parser.add_argument("--use_edit", type=int, default=1)
    parser.add_argument("--external_knowledge", type=int, default=1)
    parser.add_argument("--random_knowledge", type=int, default=0)
    parser.add_argument("--reference_mode", type=str, choices=["legacy", "revolution", "none"], default="legacy")
    parser.add_argument("--reference_base_path", type=str, default="")
    parser.add_argument("--reference_limit", type=int, default=-1)
    parser.add_argument("--reference_top_k", type=int, default=4)
    parser.add_argument("--llm", type=str, choices=LLM_BASE.keys(), default="gpt35", help="base LLM model.")
    parser.add_argument("--openai_api_keys", type=str, help="opeani_api_keys", default="", required=True)
    parser.add_argument("--count_token_cost", type=bool, help="count_token_cost", default=True)
    parser.add_argument("--initial_path_eval", type=bool, help="evaluate initial reasoning path (ablation study)", default=False)
    parser.add_argument("--hop", type=int, default=0)
    args = parser.parse_args()
    args.LLM_type = LLM_BASE[args.llm]
    return args


def question_process(fpath):
    if fpath.endswith('jsonl'):
        data = read_jsonl(fpath)
    else:
        data = readjson(fpath)

    return data


def num_tokens_from_string(string: str, model_name: str = "gpt-3.5-turbo") -> int:
    """Returns the number of tokens in a text string.  For calculating token cost."""
    encoding = tiktoken.encoding_for_model(model_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def relation_search(ett_list):
    rel_list = {}
    for i, mid_list in ett_list.items():
        rel = []
        mid_list = list(set(mid_list))
        for mid in mid_list:
            edge_set = utils.get_ent_one_hop_rel(mid)
            rel.extend(edge_set)
        rel_list[f"entity_{i}"] = list(set(rel))
    return rel_list

def similar_question_select(cand_questions, init_text, top_k=2):
    if len(cand_questions) == 0:
        return []
    top_k = min(top_k, len(cand_questions))
    cand_encode_list = np.asarray([enc_model.encode(r) for r in cand_questions])
    init_encode = np.asarray([enc_model.encode(init_text)])
    d = len(cand_encode_list[0])
    index = faiss.IndexFlatL2(d)
    index.add(cand_encode_list)
    D, I = index.search(init_encode, top_k)
    index = I
    selected_questions = [cand_questions[i] for i in I[0]]
    return selected_questions

def read_jsonl_file(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read().strip()
    if not text:
        return data

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        pass

    try:
        for line in text.splitlines():
            line = line.strip()
            if line:
                data.append(json.loads(line))
        return data
    except json.JSONDecodeError:
        data = []

    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        obj, index = decoder.raw_decode(text, index)
        data.append(obj)
    return data

def get_dataset_prefix(dataset):
    if 'WebQSP' in dataset:
        return 'WebQSP'
    if 'cwq' in dataset:
        return 'cwq'
    if 'grailqa' in dataset:
        return 'grailqa'
    return dataset.split('_')[0]

def get_reference_limit_suffix(limit):
    return "all" if limit < 0 else f"limit{limit}"

def get_default_revolution_reference_path(dataset, limit=-1):
    dataset_prefix = get_dataset_prefix(dataset)
    limit_suffix = get_reference_limit_suffix(limit)
    candidates = [
        f"data/revolution_reference/{dataset_prefix}_reference_{limit_suffix}.jsonl",
        f"data/revolution_reference/{dataset_prefix.lower()}_reference_{limit_suffix}.jsonl",
        f"data/revolution_reference/{dataset_prefix}_reference.jsonl",
        f"data/revolution_reference/{dataset_prefix.lower()}_reference.jsonl",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]

def load_reference_bank(options):
    if options.reference_mode == "none" or options.external_knowledge != 1:
        return {}
    if options.reference_mode == "legacy":
        dataset_prefix = get_dataset_prefix(options.dataset)
        with open(f"data/{dataset_prefix}_demos_kmeans_100_with_answers.json", encoding='utf-8') as f_demo:
            return json.load(f_demo)
    if options.reference_mode == "revolution":
        reference_path = options.reference_base_path or get_default_revolution_reference_path(
            options.dataset,
            options.reference_limit,
        )
        if not os.path.exists(reference_path):
            raise FileNotFoundError(
                f"Revolution reference base not found: {reference_path}. "
                f"Use --reference_limit to select a generated limit-specific file, "
                f"or --reference_base_path to provide an explicit path."
            )
        return read_jsonl_file(reference_path)
    raise ValueError(f"Unknown reference_mode: {options.reference_mode}")

def mask_question_with_entities(question, topic_ent_list):
    masked_question = question
    for ent in topic_ent_list:
        masked_question = masked_question.replace(ent, "*")
    return masked_question

def select_reference_items(reference_bank, question, topic_ent_list, options):
    if options.reference_mode == "none" or options.external_knowledge != 1:
        return []
    if options.reference_mode == "legacy":
        demo_question = [i for i, _ in reference_bank.items()]
        if len(demo_question) == 0:
            return []
        if options.random_knowledge == 1:
            return random.sample(demo_question, min(options.reference_top_k, len(demo_question)))
        masked_question = mask_question_with_entities(question, topic_ent_list)
        return similar_question_select(demo_question, masked_question, top_k=options.reference_top_k)
    if options.reference_mode == "revolution":
        if len(reference_bank) == 0:
            return []
        if options.random_knowledge == 1:
            return random.sample(reference_bank, min(options.reference_top_k, len(reference_bank)))
        masked_question = mask_question_with_entities(question, topic_ent_list)
        cand_questions = [ref.get("masked_question") or ref.get("question", "") for ref in reference_bank]
        selected_questions = similar_question_select(cand_questions, masked_question, top_k=options.reference_top_k)
        selected_set = set(selected_questions)
        selected_refs = []
        for ref in reference_bank:
            ref_question = ref.get("masked_question") or ref.get("question", "")
            if ref_question in selected_set:
                selected_refs.append(ref)
            if len(selected_refs) >= options.reference_top_k:
                break
        return selected_refs
    return []

def format_revolution_relation_paths(ref):
    paths = {}
    relation_path = ref.get("gold", {}).get("relation_path", "")
    topic_entities = ref.get("topic_entities", [])
    if topic_entities:
        for ent in topic_entities:
            label = ent.get("label") or ent.get("id")
            if label and relation_path:
                paths[label] = f"{label} -> {relation_path}"
    elif relation_path:
        paths["Topic Entity"] = relation_path
    return paths

def compact_path_string(path):
    if not path:
        return ""
    triples = path[0] if isinstance(path, list) and path and isinstance(path[0], list) else path
    if not triples:
        return ""
    parts = []
    for i, triple in enumerate(triples):
        if not isinstance(triple, list) or len(triple) != 3:
            continue
        h, r, t = triple
        if i == 0:
            parts.append(str(h))
        parts.extend([str(r), str(t)])
    return " -> ".join(parts)

def first_non_empty(values):
    if not values:
        return ""
    return str(values[0])

def format_revolution_retrieval_context(ref):
    retrieval_result = ref.get("blind", {}).get("retrieval_result", [])
    if not retrieval_result:
        return ""
    result = retrieval_result[0]
    lines = []
    failure_reasons = ref.get("blind", {}).get("verification", {}).get("failure_reasons", [])
    partial_path = compact_path_string(result.get("partial_paths", []))
    if result.get("reasoning_path"):
        lines.append("Blind Path: " + str(result.get("reasoning_path")))
    if failure_reasons:
        lines.append("Failure: " + first_non_empty(failure_reasons))
    if partial_path:
        lines.append("Partial Path: " + partial_path)
    return "\n".join(lines)

def format_revolution_reference_item(ref, include_answer=True):
    q = ref.get("question", "")
    gold_relation_path = ref.get("gold", {}).get("relation_path", "")
    evaluation = ref.get("evaluation", {})
    answers = [ans.get("label", "") for ans in ref.get("answers", []) if ans.get("label")]

    lines = ["Reference Question: " + q]
    retrieval_context = format_revolution_retrieval_context(ref)
    if retrieval_context:
        lines.append(retrieval_context)
    topic_entities = ref.get("topic_entities", [])
    if topic_entities:
        label = topic_entities[0].get("label") or topic_entities[0].get("id") or "Topic Entity"
        lines.append("Correct Path: " + str(label) + " -> " + gold_relation_path)
    else:
        lines.append("Correct Path: " + gold_relation_path)
    if evaluation.get("correction"):
        lines.append("Rule: " + str(evaluation.get("correction", "")))
    if include_answer:
        lines.append("Answer: " + "; ".join(answers[:3]))
    return "\n".join(lines) + "\n"

def build_reference_demo_strings(reference_bank, question, topic_ent_list, options):
    if options.reference_mode == "none" or options.external_knowledge != 1:
        return "", ""
    selected_items = select_reference_items(reference_bank, question, topic_ent_list, options)
    demo_str_check = ''
    demo_str_prune = ''
    if options.reference_mode == "legacy":
        for q in selected_items:
            r_path = list(reference_bank[q]['Paths'].values())[0]
            demo_str_check += "Question: " + q + '\n' + 'Relation_path: ' + str(reference_bank[q]['Paths']) + '\n'
            demo_str_prune += "Question: " + q + '\n' + 'Relation_path: ' + r_path + '\n' + 'Answer: ' + "; ".join(reference_bank[q]['Answers'][:3]) + '\n'
    elif options.reference_mode == "revolution":
        for ref in selected_items:
            demo_str_check += format_revolution_reference_item(ref, include_answer=False) + "\n"
            demo_str_prune += format_revolution_reference_item(ref, include_answer=True) + "\n"
    return demo_str_check, demo_str_prune

def LLM_edit(reasoning_path_LLM_init, demo_str, entity_label, feedback, question, count_of_error_edit, options, pipeline=None, ett_id_dict=None, candidate_rel_dict=None, input_token_cnt=0, output_token_cnt=0, llm_calls=0):
    """
    reasoning path editing

    Args:
        reasoning_path_LLM_init : previous reasoning path
        entity_label : topic entity
        feedback : prepared error message for editing
        question 
        options 
        input_token_cnt 
        output_token_cnt

    Returns:
        reasoning_path_LLM_init : edited reasoning path
        thought : LLM CoT
        input_token_cnt, output_token_cnt : to calculate token cost
    """
    init_path = reasoning_path_LLM_init[entity_label]
    #print("init_path: ", init_path)
    err_msg, grounded_know_string, candidate_rel = feedback

    prompt_edit_1 = open(
        os.path.join(PROMPT_PATH, f"{options.dataset.split('_')[0]}_edit_with_relation.md"),
        'r', encoding='utf-8'
    ).read()
    
    print("candidate relation:", candidate_rel_dict)
    print("demo:", demo_str)
    '''if len(demo_str) > 0:
        prompts_1 = prompt_edit_1%(demo_str) + "Question: " + question + "\nInitial Path: " + str(init_path) + "\n>>>> Analysis Message\n" + err_msg + ">>>> Instantiation Context\nInstantiate Paths:" + grounded_know_string +"\nCandidate Relations:" + str(candidate_rel_dict)  + "\n>>>> Corrected Path\nGoal: "
    else:
        prompts_1 = prompt_edit_1%(demo_str) + "Question: " + question + "\nInitial Path: " + str(init_path) + "\n>>>> Analysis Message\n" + err_msg + ">>>> Instantiation Context\nInstantiate Paths:" + grounded_know_string +"\nCandidate Relations:" + str(candidate_rel_dict)  + "\n>>>> Corrected Path\nGoal: "'''
    
    prompts_1 = prompt_edit_1%(demo_str) + "Question: " + question + "\nInitial Path: " + str(init_path) + "\n>>>> Analysis Message\n" + err_msg + ">>>> Instantiation Context\nInstantiate Paths:" + grounded_know_string +"\nCandidate Relations:" + str(candidate_rel_dict)  + "\n>>>> Corrected Path\nGoal: "
    # test
    # no_change_count = 0
    for _ in range(MAX_LLM_RETRY_TIME):
        try:
            response, llm_calls = run_llm(prompts_1, temperature=options.temperature, max_tokens=options.max_token, llm_calls=llm_calls, openai_api_keys=options.openai_api_keys, pipe=pipeline, engine=options.LLM_type)
            print(response)
            new_path_0 = response.split("Final Path:")[-1].strip().strip("\"").strip()
            path_split = utils.string_to_path(new_path_0)
            new_path = entity_label
            for i in path_split:
                if len(i.split('.')) >= 2:
                    new_path += ' -> ' + i
            print("Edited Path:", new_path)
            thought = response
            if entity_label not in new_path or "->" not in new_path:
                count_of_error_edit[0] += 1
                raise ValueError("entity_label or -> is not in path")
            
            if new_path == init_path:
                count_of_error_edit[1] += 1
                raise ValueError("no changing origin plan")

            if "->" not in new_path or entity_label not in new_path:
                count_of_error_edit[2] += 1
                raise ValueError("output empty plan or without the starting point")

            elements = new_path.split(" -> ")
            if len(list(set(elements))) < len(elements):
                count_of_error_edit[3] += 1
                raise ValueError("same relation in path")
            
            if len(elements) > 5:
                count_of_error_edit[4] += 1
                raise ValueError("path too long!!!!")
            
            print()
            reasoning_path_LLM_init[entity_label] = new_path
            break
        except Exception as e:
            if options.verbose:
                error_line = "*" * 40
                print(error_line)
                print(e)
                print("---------- new path -----------:", new_path)
                print(error_line)
                print()
            print(e)
            time.sleep(0.5)
    
    ''' if no_change_count == MAX_LLM_RETRY_TIME:
        if init_path in count_of_same_path:
            count_of_same_path[init_path] += 1
        else:
            count_of_same_path[init_path] = 1'''

    if options.count_token_cost:
        input_token_cnt += num_tokens_from_string(prompt_edit_1)
        output_token_cnt += num_tokens_from_string(response)
    
    return reasoning_path_LLM_init, thought, count_of_error_edit, input_token_cnt, output_token_cnt, llm_calls

def get_init_reasoning_path(question, topic_ent, options, pipeline=None, input_token_cnt=0, output_token_cnt=0, llm_calls=0, cand_relation=None):
    """generate initial reasoning path

    Args:
        question
        topic_ent : topic entities
        options : parsed arguments
        input_token_cnt : to calculate token cost
        output_token_cnt : to calculate token cost

    Returns:
        init_reasoning_path
        input_token_cnt
        output_token_cnt
    """
    '''prompt_filter = open(
        os.path.join(PROMPT_PATH, f"ett_type_filter.md"),
        'r', encoding='utf-8'
    ).read()'''


    if options.relation_check == 1:
        prompt_init_path = open(
        os.path.join(PROMPT_PATH, f"{options.dataset.split('_')[0]}_init_with_relation.md"),
        'r', encoding='utf-8'
        ).read()
        prompt_init_path += "Question: " + question + "\nTopic Entities:" + ', '.join(topic_ent) + "\nValuable Relations:" + str(cand_relation)+ "\nThought:"
    else:
        prompt_init_path = open(
        os.path.join(PROMPT_PATH, f"{options.dataset.split('_')[0]}_init.md"),
        'r', encoding='utf-8'
        ).read()
        prompt_init_path += "Question: " + question + "\nTopic Entities:" + ', '.join(topic_ent)+ "\nThought:"
    
    # default empty path
    default_relation_path = {
        k: k
        for k in topic_ent
    } 
    
    init_reasoning_path = default_relation_path
    
    for _ in range(MAX_LLM_RETRY_TIME):
        try:
            response, llm_calls = run_llm(prompt_init_path, options.temperature, options.max_token_reasoning, llm_calls, openai_api_keys=options.openai_api_keys, pipe=None, engine=options.LLM_type)
            reponse_dict = eval(response.split("Path:")[-1].strip())
            for k, v in reponse_dict.items():
                if type(v) == list:
                    if type(v[0]) == str:
                        init_reasoning_path[k] = v[0]    
                    else:
                        init_reasoning_path[k] = v[0][0]   
            assert type(init_reasoning_path) == dict   
            break
        
        except Exception as e:
            init_reasoning_path = default_relation_path
            print(e)
            error_line = "*" * 40
            print(response)
            time.sleep(0.5)
            print(error_line)
    
    if options.count_token_cost:
        input_token_cnt += num_tokens_from_string(prompt_init_path)
        output_token_cnt += num_tokens_from_string(response)
    
    return init_reasoning_path, input_token_cnt, output_token_cnt, llm_calls


def llm_reasoning(reasoning_paths_instances, question, pipeline, options, input_token_cnt, output_token_cnt, llm_calls):
    """call llm for QA reasoning"""
    kg_instances_str = ""
    kg_triple_set = []
    
    
    prompt = open(
        os.path.join(PROMPT_PATH, f"kgqa_reasoning.md"),
        'r', encoding='utf-8'
    ).read()
    
    cot_prompt = open(
        os.path.join(PROMPT_PATH, "cot_reasoning.md"),
        'r', encoding='utf-8'
    ).read()
    
    for lines in reasoning_paths_instances:
        triple_sq = ''
        for l in lines:
            triple_sq += "(" + l[0] + ", " + l[1] + ", " + l[2] + ")"
        if triple_sq not in kg_triple_set:
            kg_triple_set.append(triple_sq)

    for l in kg_triple_set:
        kg_instances_str += l + "\n"
    kg_instances_str = kg_instances_str.strip("\n")   
    print("Knowledge Triplets: ", kg_instances_str)
    if len(kg_instances_str) > 0:
        prompts = prompt + "Q: " + question + "\nKnowledge Triplets: " + kg_instances_str + "\nA: "
        for _ in range(MAX_LLM_RETRY_TIME):
            try:
                response, llm_calls = run_llm(prompts, options.temperature, options.max_token, llm_calls, openai_api_keys=options.openai_api_keys, pipe=pipeline, engine=options.LLM_type)
                
                if len(response) == 0:
                    print(f"\n{'*'*10} Empty Results {'*'*10}")
                    print("Q: " + question)
                    print("*"*30 + '\n')
                    continue

                '''if "{" not in response or "}" not in response:
                    print(f"\n{'*'*10} Invalid Results {'*'*10}")
                    print(response)
                    print()
                    continue
                else:
                    break'''
            except Exception as e:
                continue
    
    # use internal knowledge if failed too many times
    if len(kg_instances_str) == 0 or len(response) == 0: #if "{" not in response or "}" not in response or len(kg_instances_str) == 0 or len(response) == 0:
        prompts = cot_prompt + "Q: " + question + "\nA: "
        try:
            response, llm_calls = run_llm(prompts, options.temperature, options.max_token, llm_calls, openai_api_keys=options.openai_api_keys, pipe=pipeline, engine=options.LLM_type)
        except Exception as e:
            response = ""
    print(response)

    if options.count_token_cost:
        input_token_cnt += num_tokens_from_string(prompts)
        output_token_cnt += num_tokens_from_string(response)

    return response, input_token_cnt, output_token_cnt, llm_calls

def check_string(string):
    return "{" in string

def clean_results(string):
    """
    Extract result from LLM output.

    Args:
        string : LLM output

    Returns:
        extracted result
    """
    if "{" in string:
        start = string.find("{") + 1
        end = string.find("}")
        content = string[start:end]
        return content
    else:
        return "NULL"
   
def hit1(response, answers):
    clean_result = response.strip().replace(" ","").lower()
    for answer in answers:
        clean_answer = answer.strip().replace(" ","").lower()
        # the line below is used by ToG
        # if clean_result == clean_answer or clean_result in clean_answer or clean_answer in clean_result:
        if clean_result == clean_answer or clean_answer in clean_result:
            return True
    return False 

def evaluate(results, ground_truth):
    """return hit"""
    hit = 0
    if check_string(results):
        response = clean_results(results)
        if type(response) != str:
            response=""
        if response=="NULL":
            response = results
        else:
            if response != "" and hit1(response, ground_truth):
                hit=1
    else:
        response = results
        if type(response) != str:
            response = ""
        if response != "" and hit1(response, ground_truth):
            hit = 1
    return hit

def check_ending(result_paths, grounded_knowledge_current_0, ungrounded_neighbor_relation_dict, reasoning_path_LLM_init, entity_label, question, demo_str, pipeline, options, input_token_cnt, output_token_cnt, llm_calls):
    """
    Check if we need to edit the reasoning path.
    If so, prepare the feedback from instantiation information.

    Args:
        result_paths : KG instances
        grounded_knowledge_current : stores all instances during BFS (length starting from 0)
        ungrounded_neighbor_relation_dict : if instantiation fails, this store some relations as candidates for editing
        reasoning_path_LLM_init : previous reasoning path from each topic entity
        entity_label : topic entity
        question 
        options

    Returns:
        max_path_len : length for the longest instance
        End_loop_cur_path: whether we need to edit the reasoning path
        (err_msg, grounded_know_string, candidate_rel) : prepared feedback for editing
    """
    max_path_len = grounded_knowledge_current_0[-1][-1]
    init_path = reasoning_path_LLM_init[entity_label]
    grounded_know = []
    ungrounded_know = []
    err_msg_list = []
    ungrounded_cand_rel = {}
    max_grounded_len = 0
    cvt_ending = False
    path_can_be_evaluated = {}
    path_have_been_evaluated = []
    judge_message = ''

    if options.verbose:
        print("max len of grounded knowledge current: ", max_path_len)
    End_loop_cur_path = True
    Answer_is_retrived = False

    # check if anything goes wrong and get the reasoning
    if len(result_paths) > 0:
        if max_path_len == 0:
            End_loop_cur_path = False
        
        # evaluate instanced path
        if options.use_prune == 1:
            prompt_prune = open(
                os.path.join(PROMPT_PATH, f"path_prune.md"),
                'r', encoding='utf-8'
            ).read()
            
            #relation_elements = init_path.split(" -> ")[1:]
            
            for path in result_paths:
                if not path[-1][-1].startswith("m.") and not path[-1][-1].startswith("g."):
                    path_str = entity_label
                    for t in path:
                        path_str += " -> " + t[1]
                    if path_str in path_can_be_evaluated:
                        path_can_be_evaluated[path_str].append(path)
                    else:
                        path_can_be_evaluated[path_str] = [path]
            #print("path_can_be_evaluated:", path_can_be_evaluated)
            if len(path_can_be_evaluated) > 0:
                cand_list = []
                tp_seq_str = ''
                for i, j in path_can_be_evaluated.items():
                    if len(j) > 15:
                        sample_path = random.sample(j, 15)
                        cand_list.extend(sample_path)
                    else:
                        cand_list.extend(j)
                for i, p in enumerate(cand_list):
                    tp_seq_str += "\n" + f"{i+1}." + str(p) 
                if len(demo_str) > 0:
                    if options.reference_mode == "revolution":
                        prompt_prefix = (
                            "Here are reference cases showing blind KGQA failures, partial instantiated paths, "
                            "gold correction paths, and answers. Use them to judge whether the current triplet "
                            "sequences already reach the answer or only reach an intermediate/failing node.\n"
                            + demo_str + '\n'
                        )
                    else:
                        prompt_prefix = "Here are 4 examples of some questions, associated relation and answer of question.\n" + demo_str + '\n'
                    print("prune_prefix:\n" + prompt_prefix)
                    #prompt_prune = prompt_prune % (prompt_prefix)
                    prompt_1 = prompt_prefix + prompt_prune + "\nQuestion: " + question + "\nTriplet sequences: " + tp_seq_str + "\nThinking Process:"
                else:
                    prompt_1 = prompt_prune + "\nQuestion: " + question + "\nTriplet sequences: " + tp_seq_str + "\nThinking Process:"
                response_1, llm_calls = run_llm(prompt_1, options.temperature, options.max_token_reasoning, llm_calls, openai_api_keys=options.openai_api_keys, pipe=pipeline, engine=options.LLM_type)
                print("Judge Message:" , response_1)
                judge_message = response_1
                pruned_path_raw = re.findall(r"\[[\s\S]*?\]", response_1, re.DOTALL)
                pruned_path = []
                for p in pruned_path_raw:
                    try:
                        know = eval(p)
                        if type(know) == list and len(know) > 0:
                            pruned_path.append(know)
                    except Exception as e:
                        continue
                ungrounded_know.extend(pruned_path)
                if "<HAVE_ANSWER>" in response_1 and "<NO_ANSWER>" not in response_1:
                    End_loop_cur_path = True
                    Answer_is_retrived = True
                else:
                    End_loop_cur_path = False
                    Answer_is_retrived = False
                    if response_1.find("<NO_ANSWER>") != -1:
                        think_process = response_1[:response_1.find("<NO_ANSWER>")]
                        think_process = think_process.replace('Thinking Process: ', '')
                    elif response_1.find("Retained sequences:") != -1:
                        think_process = response_1[:response_1.find("Retained sequences:")]
                        think_process = think_process.replace('Thinking Process: ', '')
                    else:
                        think_process = response_1
                        for i, p in enumerate(pruned_path_raw):
                            cut_str = f"{i}. " + p
                            if cut_str in think_process:
                                think_process = think_process.replace(cut_str, '')
                            else:
                                think_process = think_process.replace(p, '')
                        think_process = think_process.replace('Thinking Process:', '')
                        think_process = think_process.replace('Retained sequences:', '')
                    think_process = think_process.strip()
                    err_msg_list.append(think_process)
                path_have_been_evaluated = cand_list
                if options.count_token_cost:
                    input_token_cnt += num_tokens_from_string(prompt_1)
                    output_token_cnt += num_tokens_from_string(response_1)

            else:
                End_loop_cur_path = False
    else:
        End_loop_cur_path = False

    if len(grounded_knowledge_current_0) > 0:
        max_grounded_len = grounded_knowledge_current_0[-1][-1]

    grounded_knowledge_current = []
    #evaluated_path_str = [" -> ".join([i[1] for i in knowledge]) for knowledge in path_have_been_evaluated]
    if options.use_prune == 1:
        for i in grounded_knowledge_current_0:
            if i[1] in path_have_been_evaluated:
                continue
            else:
                grounded_knowledge_current.append(i)
    else:
        grounded_knowledge_current = grounded_knowledge_current_0
    # process grounded knowledge (some relations might be instantiated successfully)  - code below can be optional if we do not need to edit the path
    for know in grounded_knowledge_current:
        node_label = utils.id2entity_name_or_type_en(know[0])
        
        if not node_label.startswith("m.") and not node_label.startswith("g.") and know[2] != 0 and know[2] == max_grounded_len:
            if node_label in ungrounded_neighbor_relation_dict.keys():
                ungrounded_cand_rel[node_label] = ungrounded_neighbor_relation_dict[node_label]
            grounded_know.append(know[1])
            
        if know[2] == max_grounded_len and (node_label.startswith("m.") or node_label.startswith("g.")) :
            cvt_ending = True
    
    # 5 is a hyper parameter to prevent too much knowledge in LLM context
    grounded_know_0 = []
    for triplets in grounded_know:
        if_triplets = 1
        for t in triplets:
            if type(t) != tuple or len(t) != 3:
                if_triplets = 0
                break
        if if_triplets == 1:
            grounded_know_0.append(triplets)
    grounded_know = grounded_know_0 if len(grounded_know_0) <= 5 else random.sample(grounded_know_0, 5)

    # process all cvt nodes into <cvt></cvt>
    cvt_know = [(i[0], i[1]) for i in grounded_knowledge_current if (utils.id2entity_name_or_type_en(i[0]).startswith("m.") or utils.id2entity_name_or_type_en(i[0]).startswith("g.")) and len(i[1])>0 and i[2]>=max_grounded_len - 1]
    if len(cvt_know) > 0:
        cvt_ending = True

    # cvt_know = list(set(cvt_know))
    # 10 is a hyper parameter to prevent too much knowledge in LLM context
    #cvt_know = cvt_know if len(cvt_know) <= 10 else random.sample(cvt_know, 10)

    for cvt in cvt_know:
        if cvt[0] in ungrounded_neighbor_relation_dict.keys():
            # the label of a cvt node is the original mid
            ungrounded_cand_rel[cvt[0]] = ungrounded_neighbor_relation_dict[cvt[0]]
        ungrounded_know.append(cvt[1])
    ungrounded_know_0 = []
    for triplets in ungrounded_know:
        if_triplets = 1
        for t in triplets:
            if type(t) != tuple or len(t) != 3:
                if_triplets = 0
                break
        if if_triplets == 1:
            ungrounded_know_0.append(triplets)
    ungrounded_know = ungrounded_know_0 if len(ungrounded_know_0) <= 15 else random.sample(ungrounded_know_0, 15)

    # candidate relation for path edit
    candidate_rel_dict = {}
    for know in grounded_know + ungrounded_know:
        if type(know[-1]) == tuple:
            last_ett = know[-1][-1]
        else:
            continue
        relation_path = entity_label
        for i, m in enumerate(know):
            relation_path += " -> " + str(m[1])
        if last_ett in ungrounded_cand_rel:
            if relation_path not in candidate_rel_dict:
                candidate_rel_dict[relation_path] = ungrounded_cand_rel[last_ett]
            elif len(candidate_rel_dict[relation_path]) < len(ungrounded_cand_rel[last_ett]):
                candidate_rel_dict[relation_path] = ungrounded_cand_rel[last_ett]
        '''elif last_ett.startswith("m.") or last_ett.startswith("g."):
            nei_relation = utils.get_ent_one_hop_rel(last_ett)
            candidate_rel_dict[relation_path] = nei_relation'''
    for i, j in candidate_rel_dict.items():
        j = j[:15]
    print("cand_relation_dict: ", candidate_rel_dict)
    grounded_know = [" -> ".join([i if not i.startswith("m.") and not i.startswith("g.") else "<cvt></cvt>" for i in utils.path_to_string(knowledge).split(" -> ")]) for knowledge in grounded_know]
    ungrounded_know = [" -> ".join([i if not i.startswith("m.") and not i.startswith("g.") else "<cvt></cvt>" for i in utils.path_to_string(knowledge).split(" -> ")]) for knowledge in ungrounded_know]
    
    ungrounded_know = list(set(ungrounded_know))
    grounded_know += ungrounded_know
    grounded_know = list(set(grounded_know))
    grounded_know_string = "\n".join(grounded_know)
    # ungrounded_know_string = "\n".join(grounded_know)
    #print("grounded_know_string: ", grounded_know_string)
    if len(grounded_know) == 0 and len(ungrounded_neighbor_relation_dict) > 0:
        ungrounded_cand_rel = ungrounded_neighbor_relation_dict

    # prepare candidate relations
    candidate_rel = []
    for k, v in ungrounded_cand_rel.items():
        candidate_rel.extend(v)
    candidate_rel = list(set(candidate_rel))

    # filter similar relations as candidates. 35 is a hyper parameter to prevent too much knowledge in LLM context
    candidate_rel = candidate_rel if len(candidate_rel) <= 35 else utils.similar_search_list(question, candidate_rel, options)[:35]
    candidate_rel.sort()

    # prepare error message for editing
    
    if cvt_ending:
        err_msg_list.append("<cvt></cvt> in the end.")
    if "->" not in init_path:
        err_msg_list.append("Empty Initial Path.")
    else:
        relation_elements = init_path.split(" -> ")[1:]
        if max_grounded_len < len(relation_elements):
            ungrounded_relation = relation_elements[max_grounded_len]
            err_msg_list.append(f"relation \"{ungrounded_relation}\" not instantiated.")
            
    err_msg = ""
    for index, msg in enumerate(err_msg_list):
        err_msg += str(index+1)+". "+ msg +"\n"

    print("Error message")
    print(err_msg)
    
    return max_path_len, End_loop_cur_path, Answer_is_retrived, (err_msg, grounded_know_string, candidate_rel), result_paths, candidate_rel_dict, input_token_cnt, output_token_cnt, llm_calls, judge_message

def merge_different_path(grounded_revised_knowledge, reasoning_paths, options):
    """
    Merge different paths instances from different topic entities.
    For instances from each topic entity, we first take all entities in these instances and calculate the intersection of these entities.
    If the intersection is not empty, we retain all instances containing these intersected entities for instances from each topic entities.
    For example, for the question "What country bordering France contains an airport that serves Nijmegen?", we have instances from "France" and "Nijmegen".
    We take all entities from "France" and "Nijmegen" and calculate that the intersection is "German".
    Then, we retain all path instances for "France" and "Nijmegen" containing "German".

    Moreover, if one path instances contains too much instances (more than 50), we remove some these instances, because they might not be useful for LLM's QA reasoning.

    Args:
        grounded_revised_knowledge : knowledge instances from each topic entity
        reasoning_paths : all knowledge instances (cumulated)
        options

    Returns:
        merged path instances
    """
    if options.verbose:
        print("**********************merge*****************************")
    
    # get related entities and knowledge length for each topic entity
    entity_sets={}
    knowledeg_len_dict={}
    for topic_entity, grounded_knowledge in grounded_revised_knowledge.items():
        if not topic_entity in entity_sets.keys():
            entity_sets[topic_entity]=set()

        knowledge_len = 0
        for paths in grounded_knowledge:
            knowledge_len += len(paths)
            for triples in paths:
                entity_sets[topic_entity].add(triples[0])
                entity_sets[topic_entity].add(triples[2])
        knowledeg_len_dict[topic_entity] = knowledge_len

    intersec_set = ""
    for topic_entity, entities_in_knowledge in entity_sets.items():
        if type(intersec_set) == str:
            intersec_set = entities_in_knowledge
        else:
            intersec_set = intersec_set.intersection(entities_in_knowledge)
            # take intersection according to intersected entities
            if len(intersec_set) > 0:
                new_reasoning_paths = []
                lists_of_paths = []
                for path in reasoning_paths:
                    for i in intersec_set:
                        if i in str(path):
                            new_reasoning_paths.append(path)
                            lists_of_paths.append(utils.path_to_string(path))
                reasoning_paths = new_reasoning_paths
            else:
                # no intersection, and the current path contains too much instances (which might not be useful)
                if len(reasoning_paths) > 50:
                    cand_path_dict = {}
                    new_reasoning_paths = []
                    for i in reasoning_paths:
                        if i[0][0] in cand_path_dict:
                            cand_path_dict[i[0][0]].append(i)
                        else:
                            cand_path_dict[i[0][0]] = [i]
                    for t, p in cand_path_dict.items():
                        if len(p) > 30:
                            new_reasoning_paths += random.sample(p, 30)
                        else:
                            new_reasoning_paths += p
                    reasoning_paths = new_reasoning_paths

    return reasoning_paths

def nei_triple_extract(mid):
    rel_list = get_ent_one_hop_rel(mid)
    triple_list = []
    for relation in rel_list:
        nei_list_tail = [neighbor for neighbor in utils.entity_search(mid, relation, True)]
        nei_list_head = [neighbor for neighbor in utils.entity_search(mid, relation, False)]
        nei_list_tail = list(set(nei_list_tail))
        nei_list_head = list(set(nei_list_head))
        triple_list.extend([(utils.id2entity_name_or_type_en(mid), relation, utils.id2entity_name_or_type_en(nei)) for nei in nei_list_tail]) # 可能有重复关系
        triple_list.extend([(utils.id2entity_name_or_type_en(nei), relation, utils.id2entity_name_or_type_en(mid)) for nei in nei_list_head])
    return triple_list

def triple_summary(question, triples, options):
    prompt = open(
        os.path.join(PROMPT_PATH, f"triple_summary.md"),
        'r', encoding='utf-8'
    ).read()
    
    if len(triples) > 256:
        cand_triples = []
        #triples = random.sample(triples, 350)
        triple_cls = {}
        triple_filter = {}
        for t in triples:
            if t[1] not in triple_cls:
                triple_cls[t[1]] = [t]
                triple_filter[t[1]] = 0
            else:
                triple_cls[t[1]].append(t)
        triple_cls_2 = [(r, t) for r, t in triple_cls.items()]
        triple_cls_2.sort(key=lambda x: len(x[1]), reverse=True)
        i = 0
        c = 0
        while c < 256:
            for t in triple_cls_2:
                if c == 256:
                    break
                if len(t[1]) > i:
                    triple_filter[t[0]] += 1
                    c += 1
            i += 1
        for r, c in triple_filter.items():
            cand_triples += triple_cls[r][:c]
    else:
        cand_triples = triples

    triples_str = ', '.join(["({}, {}, {})".format(t[0], t[1], t[2]) for t in cand_triples])
    prompt_summary = prompt.format(question, triples_str)
    response = run_llm(prompt_summary, options.temperature, options.max_token, options.openai_api_keys, pipe=None, engine=options.LLM_type)
    return response

def relation_extract(question, topic_entity, topic_ent_list, cand_relation, demo_str, input_token_cnt, output_token_cnt, llm_calls, options):
    prompt = open(
        os.path.join(PROMPT_PATH, f"extract_relation.md"),
        'r', encoding='utf-8'
    ).read()
    if len(demo_str) > 0:
        print("demo_str:\n", demo_str)
        if options.reference_mode == "revolution":
            insert_text = (
                "Here are reference cases from training questions. Each case shows a blind initial prediction, "
                "the KG retrieval failure or partial path, and the corrected gold relation path. "
                "Use similar cases to prioritize candidate relations that avoid the same failure pattern:\n"
                + demo_str
            )
        else:
            insert_text = "Here are reference cases of questions and associated relation paths which connect to correct answer of question:\n" + demo_str
    else:
        insert_text = demo_str
    
    relation_str = "; ".join(cand_relation)
    prompt_1 = prompt % (insert_text) + "\nQuestion:" + question + "\nTopic Entity:" + topic_entity + "\nRelations" + relation_str + "\nAnswer:"
    response, llm_calls = run_llm(prompt_1, options.temperature, options.max_token, llm_calls, options.openai_api_keys, pipe=None, engine=options.LLM_type)
    rel_with_score = re.findall(r"\([\s\S]*?\)", response, re.DOTALL)
    #print(rel_with_score)
    rel_score_tuple = []
    if len(rel_with_score) > 0:
        for p in rel_with_score:
            try:
                rel_score_tuple.append(eval(p))  
            except Exception as e:
                continue
    
    if options.count_token_cost:
        input_token_cnt += num_tokens_from_string(prompt_1)
        output_token_cnt += num_tokens_from_string(response)

    return rel_score_tuple, input_token_cnt, output_token_cnt, llm_calls

def main():    
    options.LLM_type = LLM_BASE[options.llm]
    input_file = get_dataset_file(options.dataset, hop=options.hop)
    print(input_file)
    print(f"relation_check: {options.relation_check}; use_prune: {options.use_prune}; use_edit: {options.use_edit}; external_knowledge: {options.external_knowledge}; random_knowledge: {options.random_knowledge}")
    output_file = os.path.join(OUTPUT_FILE_PATH, 
                               f"KGQA/{options.dataset}_{options.llm}_{get_timestamp()}_{options.relation_check}{options.use_prune}{options.use_edit}{options.external_knowledge}{options.random_knowledge}_{options.a}_{options.b}.jsonl")
    output_metrics = os.path.join(OUTPUT_FILE_PATH, f"KGQA/{options.dataset}_{options.llm}_{get_timestamp()}_{options.relation_check}{options.use_prune}{options.use_edit}{options.external_knowledge}{options.random_knowledge}_{options.a}_{options.b}_metrics.json")
    process_ana_file = os.path.join("src/process_analysis", f"{options.dataset}_{options.llm}_{get_timestamp()}_wrong_{options.a}_{options.b}.jsonl")
    question_string = get_question_string(options.dataset)
    dataset = question_process(input_file)[options.a:options.b] #[14, 19, 20, 22, 23, 32, 33]
    
    #model_name = "Meta-Llama-3.1-70B-Instruct"
    #model_path = os.path.join("../../../share_weight", model_name)
    if 'Llama' in options.LLM_type:
        model_path = "/data/share_weight/Meta-Llama-3.1-70B-Instruct"
        quantization_config = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_compute_dtype=torch.float16,bnb_4bit_use_double_quant=True,bnb_4bit_quant_type="nf4")
        quantized_model = AutoModelForCausalLM.from_pretrained(
        model_path, device_map="auto", quantization_config=quantization_config)
        tokenizer = AutoTokenizer.from_pretrained(model_path)
        pipeline = transformers.pipeline("text-generation", model=quantized_model, tokenizer=tokenizer,max_new_tokens=512,pad_token_id=128001)
    else:
        pipeline = None
    print("save output file to: ", output_file)    
    if not options.full:
        dataset = dataset

    metrics = {
        'hit':[],
        'input_token':[],
        'output_token':[],
        'total_token':[],
        'LLM_call':[],
        'time':[],
    }

    f_output = open(output_file, 'w+', encoding='utf-8')
    wf = open(process_ana_file, 'w+', encoding='utf-8')
    wrong_index = []

    reference_bank = load_reference_bank(options)

    for index, item in enumerate(tqdm(dataset, total=len(dataset), desc='dataset')):
        topic_ent_list = get_topic_entity_list(item, input_file)
        topic_ent_dict = get_topic_entity_dict(item, input_file)
        err_and_thought_list = []
        input_token_cnt = 0
        output_token_cnt = 0
        llm_calls = 0
        process_time = time.time()
        first_relation = []
        # skip for items without a topic entity

        question = item[question_string] if item[question_string].endswith('?') else item[question_string] + '?'
        ground_truth = get_ground_truth(item, options.dataset)

        if topic_ent_list == []:
            print(f"topic entity is empty")
            # this is critical. Please check if you need to continue
            reasoning_paths = []
            response, input_token_cnt, output_token_cnt, llm_calls = llm_reasoning(reasoning_paths, question, pipeline, options, input_token_cnt, output_token_cnt, llm_calls)
            process_time = time.time() - process_time
            hit = evaluate(response, ground_truth)
            #token_cost = (0.0015 * input_token_cnt + 0.002 * output_token_cnt) / 1000
            info = {
                'question':question,
                'first_init_path': [],
                'predict': response,
                'ground_truth': ground_truth,
                'kg_instances': reasoning_paths,
                'err_and_thought': err_and_thought_list,
                'hit':hit,
            }
            d = json.dumps(info)
            f_output.write(d + '\n')
            if hit == 0:
                wrong_index.append(index)
                wf.write(d + '\n')

            metrics["hit"].append(hit)
            # metrics['token_cost'].append(token_cost)
            metrics["input_token"].append(input_token_cnt)
            metrics["output_token"].append(output_token_cnt)
            metrics["total_token"].append(input_token_cnt + output_token_cnt)
            metrics["LLM_call"].append(llm_calls)
            metrics["time"].append(process_time)
            print(f"hit:{np.mean(metrics['hit'])}")
            continue
        
        
        # reasoning path generation
        topic_ent_relation = {j: [] for i, j in topic_ent_dict.items()}

        demo_str_check, demo_str_prune = build_reference_demo_strings(
            reference_bank,
            question,
            topic_ent_list,
            options,
        )
        
        if options.relation_check == 1:
            for entity_id, entity_label in topic_ent_dict.items():
                nei_relation = get_ent_one_hop_rel(entity_id)
                cand_relation, input_token_cnt, output_token_cnt, llm_calls = relation_extract(question, entity_label, topic_ent_list, nei_relation, demo_str_check, input_token_cnt, output_token_cnt, llm_calls, options)
                print("First Relations:")
                print(cand_relation)
                first_relation = cand_relation
                cand_relation = [i for i in cand_relation if type(i) == tuple]
                if len(cand_relation) > 0:
                    cand_relation = [i[0] for i in cand_relation]
                topic_ent_relation[entity_label] = cand_relation
            
        
        init_reasoning_path, input_token_cnt, output_token_cnt, llm_calls = get_init_reasoning_path(question, topic_ent_list, options, pipeline, input_token_cnt, output_token_cnt, llm_calls, topic_ent_relation)
        
        first_init_reasoning_path = init_reasoning_path.copy()
        #if options.verbose:
        print(f"Question:{question}")
        print(f"init reasoning path: {init_reasoning_path}")
        refine = 0

        # kg instance for each path from the topic entity
        knowledge_instance_final = dict.fromkeys(topic_ent_list, [])
        len_of_grounded_knowledge = dict.fromkeys(topic_ent_list, [])
        len_of_predict_knowledge = dict.fromkeys(topic_ent_list, [])
        reasoning_paths = []
        thought = ""
        lists_of_paths = []
        predict_path = dict.fromkeys(topic_ent_list, [])
        
        # instantitation for each path
        for entity_id, entity_label in tqdm(topic_ent_dict.items(), total=len(topic_ent_dict)):
            if entity_label not in init_reasoning_path.keys():
                continue
            
            if options.verbose:
                print("Topic entity: ", entity_label)
            
            retrived_path = {}
            count_of_error_edit = [0, 0, 0, 0, 0]
            while refine < MAX_REFINE_TIME:
                # relation binding
                binded_relations = relation_binding(init_reasoning_path, topk=5)
                relation_path_array = utils.string_to_path(init_reasoning_path[entity_label])
                sequential_relation_candidates = [binded_relations[relation] for relation in relation_path_array]
                if init_reasoning_path[entity_label] not in retrived_path:
                    # path connecting for each path
                    result_paths_0, grounded_knowledge_current, ungrounded_neighbor_relation_dict, result_ett_id = bfs_for_each_path(entity_id, relation_path_array, sequential_relation_candidates, options, options.max_que)
                    retrived_path[init_reasoning_path[entity_label]] = (result_paths_0, grounded_knowledge_current, ungrounded_neighbor_relation_dict, result_ett_id)
                else:  
                    result_paths_0, grounded_knowledge_current, ungrounded_neighbor_relation_dict, result_ett_id = retrived_path[init_reasoning_path[entity_label]]  
                # check if we need to edit the reasoning path
                max_path_len, end_refine, answer_is_retrived, feedback, result_paths, candidate_relation, input_token_cnt, output_token_cnt, llm_calls, judge_message = check_ending(result_paths_0, grounded_knowledge_current, ungrounded_neighbor_relation_dict, init_reasoning_path, entity_label, question, demo_str_prune, pipeline, options, input_token_cnt, output_token_cnt, llm_calls)
                    
                len_of_predict_knowledge[entity_label].append(len(init_reasoning_path[entity_label].split("->"))-1)
                len_of_grounded_knowledge[entity_label].append(max_path_len)    
                predict_path[entity_label].append(init_reasoning_path[entity_label])
                

                if options.verbose:
                    print("len of predict path", len(init_reasoning_path[entity_label].split("->"))-1)    

                # test init reasoning path
                if options.initial_path_eval and refine == 0:
                    reasoning_paths_init = []
                    reasoning_paths_init.extend(result_paths)
                    # llm QA reasoning
                    response_init = llm_reasoning(reasoning_paths_init, question, pipeline, options)
                    hit_init = evaluate(response_init, ground_truth)
                    metrics['init_path_hit'].append(hit_init)
                    print(f"hit_init:{np.mean(metrics['init_path_hit'])}")               
                    
                # invoke editing
                if not end_refine and options.use_edit == 1:   
                    refine += 1
                    init_reasoning_path, thought, count_of_error_edit, input_token_cnt, output_token_cnt, llm_calls = LLM_edit(init_reasoning_path, demo_str_check, entity_label, feedback, question, count_of_error_edit, options, pipeline, result_ett_id, candidate_relation, input_token_cnt, output_token_cnt, llm_calls)
                    err_and_thought_list.append([feedback[0], judge_message, thought, init_reasoning_path[entity_label]])
                    #reasoning_paths.extend(result_paths)
                    if options.verbose:
                        print(f"{f'feedback: {feedback}' if not end_refine else ''}")
                        print(f"Refine time:{refine}")
                
                retry_too_much = False
                for c in count_of_error_edit:
                    if c >= 2*MAX_LLM_RETRY_TIME:
                        retry_too_much = True
                        break

                # no more editing, post process of this path
                if end_refine or refine >= MAX_REFINE_TIME-1 or retry_too_much or options.use_edit == 0:
                    if answer_is_retrived == True:
                        reasoning_paths.extend(result_paths)
                    #str_paths = [str(p) for p in reasoning_paths]
                    #reasoning_paths = list(set(str_paths))
                    #reasoning_paths = [eval(p) for p in reasoning_paths]
                    knowledge_instance_final[entity_label] = result_paths
                    lists_of_paths = [utils.path_to_string(p) for p in reasoning_paths]
                    if max_path_len > 0:
                        for grounded_path in grounded_knowledge_current:
                            retrived_path = grounded_path[1].copy()
                            while len(retrived_path) > 0:
                                if retrived_path[-1][-1].startswith('m.') or retrived_path[-1][-1].startswith("g."):
                                    retrived_path.pop()
                                else:
                                    break
                            '''if grounded_path[-1] < max_path_len:
                                continue'''
                            if len(retrived_path) == 0:
                                continue
                            string_path = utils.path_to_string(retrived_path)
                            if len(string_path) > 0:
                                if string_path not in lists_of_paths:
                                    lists_of_paths.append(string_path)

                                    if len(reasoning_paths) == 0:
                                        reasoning_paths = [retrived_path]
                                    else:
                                        reasoning_paths.extend([retrived_path])
                        lists_of_paths = list(set(lists_of_paths))
                    print("reasoning_paths:", reasoning_paths)
                    break

  
        # merge kg instances for each path
        if len(topic_ent_list) > 1:
            reasoning_paths = merge_different_path(knowledge_instance_final, reasoning_paths, options)

        # llm QA reasoning
        print("reasoning_paths after merge:", reasoning_paths)
        response, input_token_cnt, output_token_cnt, llm_calls = llm_reasoning(reasoning_paths, question, pipeline, options, input_token_cnt, output_token_cnt, llm_calls)
        process_time = time.time() - process_time
        hit = evaluate(response, ground_truth)
        #token_cost = (0.00075 * input_token_cnt + 0.002 * output_token_cnt) / 1000
        info = {
            'question':question,
            'First_relation': first_relation,
            'first_init_path': first_init_reasoning_path,
            'predict': response,
            'ground_truth': ground_truth,
            'kg_instances': reasoning_paths,
            'err_and_thought': err_and_thought_list,
            'hit':hit,
        }
        d = json.dumps(info)
        f_output.write(d + '\n')
        if hit == 0:
            wrong_index.append(index)
            wf.write(d + '\n')

        metrics["hit"].append(hit)
        # metrics['token_cost'].append(token_cost)
        metrics["input_token"].append(input_token_cnt)
        metrics["output_token"].append(output_token_cnt)
        metrics["total_token"].append(input_token_cnt + output_token_cnt)
        metrics["LLM_call"].append(llm_calls)
        metrics["time"].append(process_time)
        print(f"hit:{np.mean(metrics['hit'])}")
        # print(f"cost:{np.mean(metrics['token_cost'])}")

    f_output.close()
    wf.close()
    print("\n" + "*" * 20 + "\n")
    print(f"hit:{np.mean(metrics['hit'])}")
    print("wrong index: ")
    print(wrong_index)
    with open(output_metrics, 'w') as f_abs:
        final_metrics = {
            'hit':np.mean(metrics['hit']),
            'input_token':np.mean(metrics['input_token']),
            'output_token':np.mean(metrics['output_token']),
            'total_token':np.mean(metrics['total_token']),
            'LLM_call':np.mean(metrics['LLM_call']),
            'time':np.mean(metrics['time']),
            'relation_check': options.relation_check,
            'use_edit': options.use_edit,
            'external_knowledge': options.external_knowledge
        }
        json.dump(final_metrics, f_abs)

if __name__ == '__main__':
    options = parse_args()
    main()
