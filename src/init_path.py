import os
from argparse import ArgumentParser
from tqdm import tqdm
import numpy as np
from config import LLM_BASE
import json
import random
import sys
sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/..")
from utils.utils import run_llm, get_timestamp, readjson
from utils.freebase_func import *
import time
from tqdm import tqdm
from utils import *
from config import *
from kg_instantiation import *
import tiktoken

PROMPT_PATH = "./prompt"

def parse_args():
    parser = ArgumentParser("KGQA for cwq or WebQSP")
    parser.add_argument("--full", action="store_true", help="full dataset.")
    parser.add_argument("--verbose", action="store_true", help="verbose or not.", default=False)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max_token", type=int, default=1024)
    parser.add_argument("--max_token_reasoning", type=int, default=2048)
    parser.add_argument("--max_que", type=int, default=200)
    #parser.add_argument("--dataset", type=str, required=True, help="choose the dataset.choices={\"cwq\", \"WebQSP\"}")
    parser.add_argument("--llm", type=str, choices=LLM_BASE.keys(), default="gpt35", help="base LLM model.")
    #parser.add_argument("--openai_api_keys", type=str, help="opeani_api_keys", default="", required=True)
    parser.add_argument("--count_token_cost", type=bool, help="count_token_cost", default=False)
    parser.add_argument("--initial_path_eval", type=bool, help="evaluate initial reasoning path (ablation study)", default=False)
    parser.add_argument("--hop", type=int, default=0)
    args = parser.parse_args()
    args.LLM_type = LLM_BASE[args.llm]
    return args


def num_tokens_from_string(string: str, model_name: str = "gpt-3.5-turbo") -> int:
    """Returns the number of tokens in a text string.  For calculating token cost."""
    encoding = tiktoken.encoding_for_model(model_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def get_init_reasoning_path(question, topic_ent, options, input_token_cnt=0, output_token_cnt=0, pipeline=None):
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
    prompt = open(
        os.path.join(PROMPT_PATH, f"property_init.md"),
        'r', encoding='utf-8'
    ).read()
    
    # default empty path
    default_relation_path = {
        k: k
        for k in topic_ent
    } 
    
    prompt += "Question: " + question + "\nTopic Entities:" + str(topic_ent)+ "\nThought:"
    init_reasoning_path = default_relation_path
    
    for _ in range(MAX_LLM_RETRY_TIME):
        try:
            response = run_llm(prompt, options.temperature, options.max_token_reasoning, openai_api_keys="", pipe=pipeline, engine="Llama")

            reponse_dict = eval(response.split("Chain:")[-1].strip())
            for k, v in reponse_dict.items():
                if type(v) == list:
                    relation_path, etype_path = utils.string_to_rel_and_etp_path(v[0])
                    relation_path_str = k + ' -> ' + ' -> '.join(relation_path)
                    etype_path_str = k + ' -> ' + ' -> '.join(etype_path)
                    init_reasoning_path[k]=[relation_path_str, etype_path_str, v[0]]         
            assert type(init_reasoning_path) == dict   
            break
        
        except Exception as e:
            init_reasoning_path = default_relation_path
            print(e)
            error_line = "*" * 40
            print(response)
            time.sleep(1)
            print(error_line)
    
    if options.count_token_cost:
        input_token_cnt += num_tokens_from_string(prompt)
        output_token_cnt += num_tokens_from_string(response)
    
    return init_reasoning_path, input_token_cnt, output_token_cnt


if __name__ == "__main__":
    options = parse_args()
    question = "who will play mr gray in the film?"
    topic_entity = ["Christian Grey"]

    model_id = "/data/share_weight/Meta-Llama-3.1-70B-Instruct"
    quantization_config = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_compute_dtype=torch.float16,bnb_4bit_use_double_quant=True,bnb_4bit_quant_type="nf4")
    quantized_model = AutoModelForCausalLM.from_pretrained(
    model_id, device_map="auto", quantization_config=quantization_config)
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    pipeline = transformers.pipeline("text-generation", model=quantized_model, tokenizer=tokenizer,max_new_tokens=1024,pad_token_id=128001)

    path, icnt, ocnt = get_init_reasoning_path(question, topic_entity, options, pipeline=pipeline)
    print(path)
