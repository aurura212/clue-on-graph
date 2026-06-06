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

    
