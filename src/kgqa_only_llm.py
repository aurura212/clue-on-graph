import sys
import os
import re
#import faiss
from argparse import ArgumentParser
from tqdm import tqdm
import numpy as np
from config import LLM_BASE
import json
import random
sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/..")
from utils.utils import run_llm, get_timestamp, readjson
#from utils.freebase_func import *
import time
from tqdm import tqdm
from utils import *
from config import *
#from kg_instantiation import *
import tiktoken

os.environ['CUDA_VISIBLE_DEVICES'] = "3"
os.environ['OPENAI_API_BASE'] = "https://ai-yyds.com/v1" #"https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT_PATH = "my_readi/prompt_1"
#enc_model = SentenceTransformer("./all-MiniLM-L6-v2")


def parse_args():
    parser = ArgumentParser("KGQA for cwq or WebQSP")
    parser.add_argument("--full", action="store_true", help="full dataset.")
    parser.add_argument("--verbose", action="store_true", help="verbose or not.", default=False)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max_token", type=int, default=1024)
    parser.add_argument("--max_token_reasoning", type=int, default=2048)
    parser.add_argument("--max_que", type=int, default=200)
    parser.add_argument("--dataset", type=str, required=True, help="choose the dataset.choices={\"cwq\", \"WebQSP\"}")
    parser.add_argument("--a", type=int, default=0)
    parser.add_argument("--b", type=int, default=-1)
    parser.add_argument("--llm", type=str, choices=LLM_BASE.keys(), default="gpt35", help="base LLM model.")
    parser.add_argument("--openai_api_keys", type=str, help="openai_api_keys", default="", required=True)
    parser.add_argument("--count_token_cost", type=bool, help="count_token_cost", default=False)
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



def llm_reasoning(reasoning_paths_instances, question, llm_calls, model, tokenizer, options):
    """call llm for QA reasoning"""
    kg_instances_str = ""
    kg_triple_set = []
    response = ""
    
    
    io_prompt = open(
        os.path.join(PROMPT_PATH, "IO_reasoning.md"),
        'r', encoding='utf-8'
    ).read()
     

    
    # use internal knowledge if failed too many times
    if len(kg_instances_str) == 0 or len(response) == 0: #if "{" not in response or "}" not in response or len(kg_instances_str) == 0 or len(response) == 0:
        prompts = io_prompt + "\nQ: " + question + "\nA: "
        response, llm_calls = run_llm(prompts, options.temperature, options.max_token, llm_calls, options.openai_api_keys, model, tokenizer, options.LLM_type)
    print(response)
    return response

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



def main():    
    options.LLM_type = LLM_BASE[options.llm]
    input_file = get_dataset_file(options.dataset, hop=options.hop)
    print(input_file)
    output_file = os.path.join(OUTPUT_FILE_PATH, f"baseline_gpt/{options.dataset}_{options.llm}_{get_timestamp()}_{options.a}_{options.b}.jsonl")
    process_ana_file = os.path.join("my_readi/process_analysis", f"{options.dataset}_{options.llm}_{get_timestamp()}_wrong_{options.a}_{options.b}.jsonl")
    question_string = get_question_string(options.dataset)
    dataset = question_process(input_file)[options.a:options.b] #[14, 19, 20, 22, 23, 32, 33]
    input_token_cnt = 0
    output_token_cnt = 0
    test_edit = False
    # 924, 1658
    #model_name = "Meta-Llama-3.1-70B-Instruct"
    #model_path = os.path.join("../../../share_weight", model_name)
    if 'qwen3-8b' in options.LLM_type:
        model_path = "/data/share_weight/Qwen3-8B"
        #quantization_config = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_compute_dtype=torch.float16,bnb_4bit_use_double_quant=True,bnb_4bit_quant_type="nf4")
        model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype="auto", device_map="auto")
        tokenizer = AutoTokenizer.from_pretrained(model_path)
    else:
        model = None
        tokenizer = None
    print("save output file to: ", output_file)    
    if not options.full:
        dataset = dataset

    metrics = {
        'hit':[],
        'token_cost':[],
        'init_path_hit':[]
    }

    f_output = open(output_file, 'w+', encoding='utf-8')
    wf = open(process_ana_file, 'w+', encoding='utf-8')
    wrong_index = []

    for index, item in enumerate(tqdm(dataset, total=len(dataset), desc='dataset')):

        err_and_thought_list = []
        llm_calls = 0
        question = item[question_string] if item[question_string].endswith('?') else item[question_string] + '?'
        ground_truth = get_ground_truth(item, options.dataset)

        # llm QA reasoning
        reasoning_paths = []
        response = llm_reasoning(reasoning_paths, question, llm_calls, model, tokenizer, options)
        hit = evaluate(response, ground_truth)
        token_cost = (0.0015 * input_token_cnt + 0.002 * output_token_cnt) / 1000
        info = {
            'question':question,
            'predict': response,
            'ground_truth': ground_truth,
            'err_and_thought': err_and_thought_list,
            'hit':hit,
        }
        d = json.dumps(info)
        f_output.write(d + '\n')
        if hit == 0:
            wrong_index.append(index)
            wf.write(d + '\n')

        metrics["hit"].append(hit)
        metrics['token_cost'].append(token_cost)
        print(f"hit:{np.mean(metrics['hit'])}")
        # print(f"cost:{np.mean(metrics['token_cost'])}")

    f_output.close()
    wf.close()
    print("\n" + "*" * 20 + "\n")
    print(f"hit:{np.mean(metrics['hit'])}")
    print("wrong index: ")
    print(wrong_index)

if __name__ == '__main__':
    options = parse_args()
    main()
