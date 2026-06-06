from SPARQLWrapper import SPARQLWrapper, JSON
import json
from tqdm import tqdm

q_type = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?type\nWHERE {\n  ns:%s rdf:type ?type .\n}\nLIMIT 50"""
q_list = [q_type]
query = q_type

with open('mid_dict_2.json', 'r') as f:
    mid_dict_2 = json.load(f)
mid = mid_dict_2.keys()
#mid_dict = {}

sparql = SPARQLWrapper("http://localhost:3002/sparql")
for m in tqdm(mid, total=len(mid)):
    type_list = []
    domain_list = []
    sparql.setQuery(query%m)
    sparql.setReturnFormat(JSON)
    results = sparql.queryAndConvert()
    #print(len(results["results"]["bindings"]))
    for i in results["results"]["bindings"]:
        if "http://rdf.freebase.com/ns/" in i['type']['value']:
            type_list.append(i['type']['value'].split('/')[-1])
            domain_list.append(i['type']['value'].split('/')[-1].split('.')[0])
    mid_dict_2[m]['type'] = list(set(type_list))
    mid_dict_2[m]['domain'] = list(set(domain_list))

    
