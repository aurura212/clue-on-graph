import sys
import os
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



if __name__ == "__main__":
    relations_no_q = grounding_relations('location.locations.continent', topk=20)
    print(relations_no_q)