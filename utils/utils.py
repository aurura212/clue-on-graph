import sys, os, re
#os.environ["CUDA_VISIBLE_DEVICES"] = "2,5,6"
import numpy as np
import datetime
import torch
sys.path.append(os.path.dirname(os.path.realpath(__file__)) + "/..")
import json
from sentence_transformers import util, SentenceTransformer
import transformers
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from utils.freebase_func import *
from utils.llm_api import get_chat_completion_extra_kwargs, is_openai_compatible_engine
import time


def readjson(file_name):
    with open(file_name, encoding='utf-8') as f:
        data = json.load(f)
    return data

def read_jsonl(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        for line in file:
            json_obj = json.loads(line)
            data.append(json_obj)
    return data


def savejson(file_name, new_data):
    with open(file_name, mode='w',encoding='utf-8') as fp:
        json.dump(new_data, fp, indent=4, sort_keys=False,ensure_ascii=False)


def get_openai_embedding(input_message, openai_api_keys, model):
    
    ok = False
    while not ok:
        try:
            '''response = openai.Embedding.create(model="text-embedding-ada-002",
                                               input=input_message)'''
            response = model.encode(input_message)
            ok = True
        except Exception as e:
            #print(e)
            print('stuck in here get_openai_embedding')
            time.sleep(10)

    return response


def run_llm(prompt, temperature, max_tokens, llm_calls, openai_api_keys=None, llm_model=None, llm_tokenizer=None, pipe=None, engine="gpt-3.5-turbo"):
    client = None
    print("engine:", engine)
    f = 0
    result = ''
    if is_openai_compatible_engine(engine):
        import openai

        messages = []
        message_prompt = {"role":"user","content":prompt}
        messages.append(message_prompt)
        api_key = os.getenv("OPENAI_KEY", default=openai_api_keys)
        base_url = os.environ.get("OPENAI_API_BASE")
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
    elif pipe == None:
        model = llm_model
        tokenizer = llm_tokenizer
    else:
        pipeline = pipe
    while(f <= 5):
        try:
            if is_openai_compatible_engine(engine):
                completion_kwargs = {
                    "model": engine,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "frequency_penalty": 0,
                    "presence_penalty": 0,
                }
                completion_kwargs.update(get_chat_completion_extra_kwargs(engine))
                response = client.chat.completions.create(**completion_kwargs)
                result = response.choices[0].message.content.strip()
                llm_calls += 1
            elif pipe == None:
                messages = [{"role": "user", "content": prompt}]
                
                text = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=True # Switches between thinking and non-thinking modes. Default is True.
                )
                model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
                generated_ids = model.generate(
                    **model_inputs,
                    max_new_tokens=4096
                )
                output_ids = generated_ids[0][len(model_inputs.input_ids[0]):].tolist() 

                # parsing thinking content
                try:
                    # rindex finding 151668 (</think>)
                    index = len(output_ids) - output_ids[::-1].index(151668)
                except ValueError:
                    index = 0

                #thinking_content = tokenizer.decode(output_ids[:index], skip_special_tokens=True).strip("\n")
                result = tokenizer.decode(output_ids[index:], skip_special_tokens=True).strip("\n")

                
                #prompt = pipeline.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                #terminators = [pipeline.tokenizer.eos_token_id, pipeline.tokenizer.convert_tokens_to_ids("<|eot_id|>")]
                #outputs = pipeline(prompt, eos_token_id=terminators, do_sample=True)
                #result="".join(outputs[0]["generated_text"][len(prompt):])
                #llm_calls += 1
                #a_text = tokenizer.decode(outputs[0], skip_special_tokens=True)
                #result = a_text[len(messages):].strip()
            else:
                messages = [{"role": "user", "content": prompt}]
                prompt = pipeline.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                terminators = [pipeline.tokenizer.eos_token_id, pipeline.tokenizer.convert_tokens_to_ids("<|eot_id|>")]
                outputs = pipeline(prompt, eos_token_id=terminators, do_sample=True)
                result="".join(outputs[0]["generated_text"][len(prompt):])
                llm_calls += 1
            
            if len(result) == 0:
                f += 1
                continue
            break

        except Exception as e:
            print("error: ", e)
            print("LLM error, retry")
            print(prompt)
            f += 1
            # trim the input according to the model's max token limit
            if "gpt-4" in engine:
                messages[-1] = {"role":"user","content": prompt[:32767]}
                time.sleep(10)
            elif is_openai_compatible_engine(engine):
                messages[-1] = {"role":"user","content": prompt[:16384]}
                time.sleep(5)
            else:
                messages = messages[:16384]

    return result, llm_calls


def get_ent_one_hop_rel(entity_id, pre_relations=[], pre_head=-1, literal=False):
    if entity_id.startswith("m.") == False and entity_id.startswith("g.")==False:
        return []
    
    sparql_relations_extract_head = sparql_head_relations % (entity_id)
    head_relations = table_result_to_list(execute_sparql(sparql_relations_extract_head))

    sparql_relations_extract_tail = sparql_tail_relations % (entity_id)
    tail_relations = table_result_to_list(execute_sparql(sparql_relations_extract_tail))

    if head_relations!=[]:
      head_relations=head_relations['relation']
      # each relation starts from ns:
      head_relations = [x.replace("http://rdf.freebase.com/ns/", "") for x in head_relations if 'http://rdf.freebase.com/ns' in x]

    # tail_relations = table_result_to_list(execute_sparql(sparql_relations_extract_tail))
    if tail_relations != []:
      tail_relations = tail_relations['relation']
      tail_relations = [x.replace("http://rdf.freebase.com/ns/", "") for x in tail_relations if 'http://rdf.freebase.com/ns' in x]

    remove_unnecessary_rel = True
    if remove_unnecessary_rel:
        head_relations = [relation for relation in head_relations if not abandon_rels(relation)]
        tail_relations = [relation for relation in tail_relations if not abandon_rels(relation)]

    if len(pre_relations) != 0 and pre_head != -1:
        head_relations = [rel for rel in pre_relations if not pre_head and rel not in head_relations]
        tail_relations = [rel for rel in pre_relations if pre_head and rel not in tail_relations]

    head_relations = list(set(head_relations))
    tail_relations = list(set(tail_relations))
    total_relations = list(set(head_relations+tail_relations))
    total_relations.sort()  # make sure the orders in prompts are always the same
    total_relations = [r for r in total_relations if abandon_rels(r) == False]
    return total_relations

def etype_map(ett_name):
    pass

'''def get_ent_one_hop_etype(entity_id, pre_etypes=[], pre_head=-1, literal=False):
    if entity_id.startswith("m.") == False and entity_id.startswith("g.")==False:
        return []
    
    sparql_entity_extract_tail = sparql_tail_ett_values % (entity_id)
    head_relations = table_result_to_list(execute_sparql(sparql_entity_extract_tail))

    sparql_relations_extract_tail = sparql_tail_relations % (entity_id)
    tail_relations = table_result_to_list(execute_sparql(sparql_relations_extract_tail))

    if head_relations!=[]:
      head_relations=head_relations['relation']
      # each relation starts from ns:
      head_relations = [x.replace("http://rdf.freebase.com/ns/", "") for x in head_relations if 'http://rdf.freebase.com/ns' in x]

    # tail_relations = table_result_to_list(execute_sparql(sparql_relations_extract_tail))
    if tail_relations != []:
      tail_relations = tail_relations['relation']
      tail_relations = [x.replace("http://rdf.freebase.com/ns/", "") for x in tail_relations if 'http://rdf.freebase.com/ns' in x]

    remove_unnecessary_rel = True
    if remove_unnecessary_rel:
        head_relations = [relation for relation in head_relations if not abandon_rels(relation)]
        tail_relations = [relation for relation in tail_relations if not abandon_rels(relation)]

    if len(pre_etypes) != 0 and pre_head != -1:
        head_relations = [rel for rel in pre_etypes if not pre_head and rel not in head_relations]
        tail_relations = [rel for rel in pre_etypes if pre_head and rel not in tail_relations]

    head_relations = list(set(head_relations))
    tail_relations = list(set(tail_relations))
    total_relations = list(set(head_relations+tail_relations))
    total_relations.sort()  # make sure the orders in prompts are always the same

    return total_relations'''

def entity_search(entity, relation, head=True):
    if head:
        if "http://www.w3.org/1999/02/22-rdf-syntax-ns#type" in relation:
            tail_entities_extract = sparql_tail_entities_extract_with_type% (entity)
            entities = table_result_to_list(execute_sparql(tail_entities_extract))
        else:
            tail_entities_extract = sparql_tail_entities_extract% (entity, relation)
            entities = table_result_to_list(execute_sparql(tail_entities_extract))
    else:
        head_entities_extract = sparql_head_entities_extract% (relation, entity)
        entities = table_result_to_list(execute_sparql(head_entities_extract))

    if entities != []:
        entities = entities['tailEntity']
        #entities = [x.replace("http://rdf.freebase.com/ns/", "") for x in entities if 'http://rdf.freebase.com/ns' in x]
        entities = [x.split('/')[-1] for x in entities]
    
    #new_entity = [entity for entity in entities if entity.startswith("m.")]
    new_entity = [entity for entity in entities]

    return new_entity


def path_to_string(path: list) -> str:
    result = ""
    for i, p in enumerate(path):
        if i == 0:
            h, r, t = p
            result += f"{h} -> {r} -> {t}"
        else:
            _, r, t = p
            result += f" -> {r} -> {t}"

    return result.strip()

def string_to_path(path_string):
    result = []
    if type(path_string) == list:
        path_string = path_string[0]
        
    path_array = path_string.split("->")
    path_array = path_array[1:]
    for lines in path_array:
        result.append(lines.strip())
    return result

def string_to_rel_and_etp_path(path_string):
    result_relation = []
    result_ettype = []
    if type(path_string) == list:
        path_string = path_string[0]
    
    #path_array = path_string.split(" -> ")
    result_relation = re.findall(r'\<(.*?)\>', path_string)
    result_ettype = re.findall(r'\[(.*?)\]', path_string)
    '''path_array = path_array[1:]
    for i, lines in enumerate(path_array):
        if i%2 == 0:
            result_relation.append(lines.strip()[1:-1])
        else:
            result_ettype.append(lines.strip()[1:-1])'''
    return result_relation, result_ettype


def similar_search_list(question, relation_list, options):
    """Use openai embedding to filter similar relations according to the question.
    We do this because in a large-scale KG, relation_list can be very large and confuses the LLM.

    This can be optimized using cached embeddings. 
    We recommand to used cached embedding for all relations in the knowledge graph and all questions to save token.
    We do not opensource the embedding for policy reason. You can use get_openai_embedding to get the embedding to create a cache file in data/openai_embeddings and modify this function.
    
    Args:
        question 
        relation_list 
        options : providing openai_api_keys

    Returns:
        relations similar to the question
    """
    model = SentenceTransformer("./all-MiniLM-L6-v2")
    question_embedding = get_openai_embedding(question, options.openai_api_keys, model)#[0]['embedding']
    relation_embeddings = []
    # read cache file (if applicable)
    cache_file_path = "data/openai_embeddings/fb_relation_embed.json"
    if os.path.exists(cache_file_path):
        r_embedding_map = readjson(cache_file_path)
    else:
        r_embedding_map = {}

    for rel in relation_list:
        if rel in r_embedding_map.keys():
            relation_embeddings.append(r_embedding_map[rel])
        else:
            relation_embeddings.append(get_openai_embedding(rel, options.openai_api_keys, model))
             
    # calculate similarity between the question and relations
    relation_embeddings = np.array(relation_embeddings)
    similarities = util.pytorch_cos_sim(question_embedding, relation_embeddings)

    # sort relation list by similarity 
    sorted_relations = [(relation, score) for relation, score in zip(relation_list, similarities.tolist()[0])]
    sorted_relations = sorted(sorted_relations, key=lambda x: x[1], reverse=True)

    sorted_relation_list = [relation[0] for relation in sorted_relations]
    return sorted_relation_list

def get_timestamp():
    now = datetime.datetime.now()
    return now.strftime(r"%m_%d_%H_%M_%S")


def jsonl_to_json(jsonl_file_path, json_file_path):
    data = []
    with open(jsonl_file_path, 'r') as jsonl_file:
        for line in jsonl_file:
            data.append(json.loads(line))
    with open(json_file_path, 'w') as json_file:
        json.dump(data, json_file, indent=4, sort_keys=False,ensure_ascii=False)
