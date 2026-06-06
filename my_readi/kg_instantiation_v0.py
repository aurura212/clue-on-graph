import os
import re
import utils
from utils import *
from config import *
from pyserini.search import FaissSearcher, LuceneSearcher
from pyserini.search.hybrid import HybridSearcher
from pyserini.search.faiss import AutoQueryEncoder
from collections import deque
from sentence_transformers import util, SentenceTransformer
import faiss

# load hybried searcher for relation binding
query_encoder = AutoQueryEncoder(encoder_dir='./facebook/contriever', pooling='mean')
corpus = LuceneSearcher(os.path.join(CONTRIEVER_PATH, "contriever_fb_relation/index_relation_fb"))
bm25_searcher = LuceneSearcher(os.path.join(CONTRIEVER_PATH, 'contriever_fb_relation/index_relation_fb'))
contriever_searcher = FaissSearcher(os.path.join(CONTRIEVER_PATH, 'contriever_fb_relation/freebase_contriever_index'), query_encoder)
hsearcher = HybridSearcher(contriever_searcher, bm25_searcher)
sim_model = SentenceTransformer("./all-MiniLM-L6-v2")


def similar_relation_from_question(question, topk=5):
    """search similar relations according to the question. Aims for corrputed reasoning path.

    Args:
        question
        topk (Defaults to 5).

    Returns:
        retrieved relations
    """
    result = []
    hits = hsearcher.search(question, k=1000)[:topk]
    for hit in hits:
        result.append(json.loads(corpus.doc(str(hit.docid)).raw())['rel_ori'])
    return result

def grounding_relations(relation, topk=5):
    """bind a natural language relation to KG relation candidates

    Args:
        relation (_type_): _description_
        topk (int, optional): _description_. Defaults to 5.

    Returns:
        _type_: _description_
    """
    result_no_q = []
    relation_tokens = relation.replace("."," ").replace("_", " ").strip()
    hits = hsearcher.search(relation_tokens.replace("  "," ").strip(), k=1000)[:topk]
    for hit in hits:
        result_no_q.append(json.loads(corpus.doc(str(hit.docid)).raw())['rel_ori'])

    return result_no_q

def relation_binding(reasoning_path_LLM_init, topk=5):
    """bind all relations in the reasoning path to KG relation candidates

    Args:
        reasoning_path_LLM_init (dict): generated reasoning path from each topic entity
        topk (int, optional): bind a relation to topk candidates. Defaults to 5.

    Returns:
        grounded_relations (dict): grounded relations for each reasoning path
    """
    predicted_reasoning_path = []
    for keys in reasoning_path_LLM_init.keys():
        if type(reasoning_path_LLM_init[keys]) == str:
            predicted_reasoning_path.append(utils.string_to_path(reasoning_path_LLM_init[keys]))
        elif len(reasoning_path_LLM_init[keys])>0:
            predicted_reasoning_path.append(utils.string_to_path(reasoning_path_LLM_init[keys][0]))
                
    # bind all relations to KG relation candidates（faiss）
    grounded_relations = {}
    for r in predicted_reasoning_path:
        for rel in r:
            relations_no_q = grounding_relations(rel, topk=topk)
            if rel not in grounded_relations.keys():
                grounded_relations[rel] = relations_no_q
            else:
                grounded_relations[rel] = list(set(grounded_relations[rel]+relations_no_q))

    return grounded_relations

def sim_relation_detect(question, relation_list,  top_k=1):
    cand_relation = []
    for relation in relation_list:
        if relation.startswith("m.") == True or relation.startswith("g.") == True:
            continue
        cand_relation.append(relation)
    relation_encode = np.asarray([sim_model.encode(r) for r in cand_relation])
    d = len(relation_encode[0])
    #print(entity)
    q_encode = np.asarray([sim_model.encode(question)])
    index = faiss.IndexFlatL2(d)
    index.add(relation_encode)
    res = faiss.StandardGpuResources()  # 创建GPU资源
    index_gpu = faiss.index_cpu_to_gpu(res, 0, index)  # 将CPU索引转移到GPU
    distances, indices = index_gpu.search(q_encode, top_k)

    return indices[0], distances[0]


prompt_dfs = ("Given 50 2_hop relation paths, please select the relation path with the closest meaning to \"{}\" and return index of the selected relation path. "
              "Index is an integer ranging from 1 to 50. Please only return the index of the selected relation path and do not give other things.\n"
              "Here are 50 2_hop relation paths:\n{}"
              "Index of the selected relation path is:  ")

def dfs_for_2_hop(question, raw_relation, entity_id_0, path_0, position, options):
    #stack = deque([(entity_id, current_path, current_position)])
    # 先选取所有1-hop关系（比较相似度）
    # 再寻找每个1-hop关系中最可能的3个2-hop关系（比较相似度）
    # 
    # 最后LLM在90条triple_path中选择最可能的1个
    relation_1_hop = utils.get_ent_one_hop_rel(entity_id_0)
    #top_30_relation_1_hop_indices = sim_relation_detect(question, relation_1_hop, r, top_k=30)
    #top_30_relation_1_hop = [relation_1_hop[i] for i in top_30_relation_1_hop_indices]
    
    top_relation_path = []
    for r1 in relation_1_hop:
        neighbors_1_hop = [neighbor for neighbor in utils.entity_search(entity_id_0, r1, True) + utils.entity_search(entity_id_0, r1, False)]
        top_3_relation_2_hop = []
        for entity_id_1 in neighbors_1_hop:
            relation_2_hop = utils.get_ent_one_hop_rel(entity_id_1)
            true_relation_2_hop = []
            for r in relation_2_hop:
                neighbors_2_hop = [neighbor for neighbor in utils.entity_search(entity_id_1, r, True) + utils.entity_search(entity_id_1, r, False)]
                entity_id_2 = [i for i in neighbors_2_hop if not utils.id2entity_name_or_type_en(i).startswith("m.")]
                if len(entity_id_2) > 0:
                    true_relation_2_hop.append(r)
                else:
                    continue
            top_3_relation_2_hop += true_relation_2_hop
        top_3_relation_2_hop_indices, top_3_relation_2_hop_distances = sim_relation_detect(question, top_3_relation_2_hop, top_k=3)    
        top_3_relation_2_hop = [top_3_relation_2_hop[i] for i in top_3_relation_2_hop_indices]
        top_relation_path += [(r1, r2, d) for r2, d in zip(top_3_relation_2_hop, top_3_relation_2_hop_distances)]
            
    top_relation_path.sort(key=lambda x: x[2], reverse=True)
    top_50_relation_path = top_relation_path[:50]
    relation_path_string = ""
    for i, r in enumerate(top_50_relation_path):
        relation_path_string += "{}. {}, {}\n".format(i+1, r[0], r[1])
    prompts = prompt_dfs.format(raw_relation, relation_path_string)
    response = run_llm(prompts, temperature=options.temperature, max_tokens=options.max_token, openai_api_keys=options.openai_api_keys, engine=options.LLM_type)
    final_index = re.findall(r'\d+', response)
    if len(final_index) > 0:
        final_relation_path = top_50_relation_path[final_index[0]-1]
    else:
        final_relation_path = top_50_relation_path[0]
    
    triple_path = []
    entity_0 = utils.id2entity_name_or_type_en(entity_id_0)
    entity_id_1_0 = utils.entity_search(entity_id_0, final_relation_path[0], False)
    for e1 in entity_id_1_0:
        e1_name = utils.id2entity_name_or_type_en(e1)
        entity_id_2_00 = utils.entity_search(e1, final_relation_path[1], False)
        triple_00 = [[(e1_name, final_relation_path[0], entity_0),(utils.id2entity_name_or_type_en(e2), final_relation_path[1] ,e1_name), e2] for e2 in entity_id_2_00]
        #triple_00 = [[(e1_name, final_relation_path[0], entity_0),(e1_name, final_relation_path[1], utils.id2entity_name_or_type_en(e2)), e2] for e2 in entity_id_2_00]
        entity_id_2_10 = utils.entity_search(e1, final_relation_path[1], True)
        triple_10 = [[(e1_name, final_relation_path[0], entity_0),(e1_name, final_relation_path[1], utils.id2entity_name_or_type_en(e2)), e2] for e2 in entity_id_2_10]
        triple_path += triple_00 + triple_10
    entity_id_1_1 = utils.entity_search(entity_id_0, final_relation_path[0], True)
    for e1 in entity_id_1_1:
        e1_name = utils.id2entity_name_or_type_en(e1)
        entity_id_2_01 = utils.entity_search(e1, final_relation_path[1], False)
        triple_01 = [[(entity_0, final_relation_path[0], e1_name),(utils.id2entity_name_or_type_en(e2), final_relation_path[1] ,e1_name), e2] for e2 in entity_id_2_01]
        #triple_01 = [[(entity_0, final_relation_path[0], e1_name),(e1_name, final_relation_path[1] ,utils.id2entity_name_or_type_en(e2)), e2] for e2 in entity_id_2_01]
        entity_id_2_11 = utils.entity_search(e1, final_relation_path[1], True)
        triple_11 = [[(entity_0, final_relation_path[0], e1_name),(e1_name, final_relation_path[1] ,utils.id2entity_name_or_type_en(e2)), e2] for e2 in entity_id_2_11]
        triple_path += triple_01 + triple_11
    
    dfs_path = []
    if len(triple_path) > 0:
        for t in triple_path:
            current_entity = t[2]
            del t[2]
            current_path = path_0 + t
            current_position = position + 1
            dfs_path.append((current_entity, current_path, current_position))
    
    return dfs_path

def bfs_for_each_path(entity_id, target_path, grounded_reasoning_set, mode, question, options, max_que = 300):
    """
    Path connecting for each reasoning path, according to the reasoning path.
    This is essentially a BFS search for each relation in the reasoning path. Each layer of BFS search consists of candidate relations.
    In each layer, we check if neighbors of the current node have intersection with the candidate relation. If so, current relation is successfully instantiated.

    We return useful structured information (including currently instantiated instances and possible candidate relations in the failed points) for editing if instantiation fails.
    Args:
        entity_id : topic entity id for current reasoning path
        target_path : current reasoning path
        grounded_reasoning_set (list): list of grounded relation candidates for each position in the reasoning path
        options : parsed arguments
        max_que (int, optional): maximum queue size for each layer. Defaults to 300.

    Returns:
        result_paths : instantiated reasoning path (empty if instantiation fails)
        grounded_knowledge_current : stores all instances during BFS (length starting from 0)
        ungrounded_neighbor_relation_dict : if instantiation fails, store some relations as candidates for editing
    """
    result_paths = []
    current_position = 0
    queue = deque([(entity_id, [], current_position)])  # BFS queue. Container for entities, path instances and current position on path
    grounded_knowledge_current = []
    ungrounded_neighbor_relation_dict = {}

    while queue:
        size = len(queue)
        ungrounded_neighbor_relation_dict.clear()

        if options.verbose:
            print("current layer size when BFS", size)

        while size > 0:
            size -= 1
            current_node, current_path, current_position = queue.popleft()

            # push current grounded path to grounded_knowledge_current.
            # Note that grounded_knowledge_current stores all instantiated path (including length from 0 to current path length
            grounded_knowledge_current.append((current_node, current_path, current_position))

            if current_position == len(target_path) and current_path not in result_paths and len(current_path) > 0:
                result_paths.append(current_path)

            # continue instantiation if current path is shorter than predicted target_path
            if current_position < len(target_path):
                # get edges (relations) around current node (except previous relations)
                pre_relations = [rel[1] for rel in current_path]
                edge_set = utils.get_ent_one_hop_rel(current_node, pre_relations=pre_relations)
                if len(edge_set) == 0:
                    continue

                # take intersection
                list1 = grounded_reasoning_set[current_position]  # grounded relations for current position
                list2 = edge_set                                  # relations around current node
                list3 = pre_relations

                intersection_grounded = list(set(list1) & set(list2))    
                intersection_have_grounded = list(set(intersection_grounded) & set(list3))    
                intersection = list(set(intersection_grounded) - set(intersection_have_grounded))
                print("intersection: {}".format(intersection))
                # no intersection for current node (something goes wrong), store some relations as candidates for editing
                if len(intersection) <= 0:
                    if mode < 2:
                        ungrounded_neighbor_relation_dict[utils.id2entity_name_or_type_en(current_node)] = edge_set
                        continue
                    else:
                        dfs_path = dfs_for_2_hop(question, target_path[current_position], current_node, current_path, current_position, options)
                        for p in dfs_path:
                            queue.append(p)
                # grounded success. add to queue for next loop
                else:
                    for relation in intersection:
                        if len(queue) >= max_que:
                            break
                        # forward and backward relations
                        neighbors_with_relation = [neighbor for neighbor in utils.entity_search(current_node, relation, True) + utils.entity_search(current_node, relation, False)]
                        for neighbor in neighbors_with_relation:
                            if len(queue) < max_que:
                                queue.append((neighbor, current_path + [(utils.id2entity_name_or_type_en(current_node), relation, utils.id2entity_name_or_type_en(neighbor))], current_position + 1))
                            else:
                                break
                            
        if len(ungrounded_neighbor_relation_dict.keys()) == 0 and len(grounded_knowledge_current) == 1 and grounded_knowledge_current[-1][-1] == 0:
            ungrounded_neighbor_relation_dict[utils.id2entity_name_or_type_en(grounded_knowledge_current[-1][0])] = utils.get_ent_one_hop_rel(grounded_knowledge_current[-1][0])
    print("result_paths")
    print(result_paths)
    return result_paths, grounded_knowledge_current, ungrounded_neighbor_relation_dict