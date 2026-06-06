from SPARQLWrapper import SPARQLWrapper, JSON

#query = "PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?x\nWHERE {\nFILTER (?x != ?c)\nFILTER (!isLiteral(?x) OR lang(?x) = '' OR langMatches(lang(?x), 'en'))\n?c ns:location.country.administrative_divisions ns:m.010vz . \n?c ns:government.governmental_jurisdiction.governing_officials ?y .\n?y ns:government.government_position_held.office_holder ?x .\n?y ns:government.government_position_held.basic_title ns:m.060c4 .\nFILTER(NOT EXISTS {?y ns:government.government_position_held.from ?sk0} || \nEXISTS {?y ns:government.government_position_held.from ?sk1 . \nFILTER(xsd:datetime(?sk1) <= \"1980-12-31\"^^xsd:dateTime) })\nFILTER(NOT EXISTS {?y ns:government.government_position_held.to ?sk2} || \nEXISTS {?y ns:government.government_position_held.to ?sk3 . \nFILTER(xsd:datetime(?sk3) >= \"1980-01-01\"^^xsd:dateTime) })\n}\n"
#sparql_head_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ns:%s ?relation ?x .\n}"""
#sparql_head_ett_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation ?label\nWHERE {\n  ?head ?relation ns:%s .\n ?head ns:type.object.name ?label .\nFILTER(LANGMATCHES(LANG(?label), "en"))}"""
#sparql_tail_ett_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation ?label\nWHERE {\n  ns:%s ?relation ?tail .\n ?tail ns:type.object.name ?label .\nFILTER(LANGMATCHES(LANG(?label), "en"))}"""

#sparql_head_ett_values_2_hop = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?label\nWHERE {\n ?head ?x ?head1 .\n?head1 ?x1 ns:%s .\n?head ns:type.object.name ?label .\nFILTER(LANGMATCHES(LANG(?label), "en"))}\n"""
#sparql_tail_ett_values_2_hop = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?label\nWHERE {\n ns:%s ?x1 ?tail1 .\n?tail1 ?x ?tail .\n?tail ns:type.object.name ?label .\nFILTER(LANGMATCHES(LANG(?label), "en"))}\n"""

sparql_head_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ns:%s ?relation ?x .\n}"""
sparql_tail_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ?x ?relation ns:%s .\n}"""

sparql_head_ett_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?head\nWHERE {\n  ?head ?x ns:%s .\n}"""
sparql_tail_ett_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tail ?x\nWHERE {\n  ns:%s ?x ?tail .\n}"""

sparql_head_ett_values_2_hop = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?head\nWHERE {\n  ?head ?x ?head1 .\n  ?head1 ?x1 ns:%s .\n}"""
sparql_tail_ett_values_2_hop = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tail\nWHERE {\n  ns:%s ?x1 ?tail1 .\n  ?tail1 ?x ?tail .\n}"""

sparql_tail_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?Entity\nWHERE {\nns:%s ns:%s ?Entity .\n}""" 
sparql_tail_entities_extract_with_type = """PREFIX ns: <http://rdf.freebase.com/ns/>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nSELECT ?tailEntity\nWHERE {\nns:%s rdf:type ?tailEntity .\n}""" 
sparql_head_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?Entity\nWHERE {\n?Entity ns:%s ns:%s  .\n}"""
sparql_id = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?tailEntity\nWHERE {\n  {\n    ?entity ns:type.object.name ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\n  UNION\n  {\n    ?entity <http://www.w3.org/2002/07/owl#sameAs> ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\nFILTER (LANG(?tailEntity) = "en")\n}"""
desc = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tail\nWHERE {\n  ns:%s ns:common.topic.description ?tail .\nFILTER (LANG(?tail) = "en")\n}""" 
sparql_head_entities_extract_values = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n%s %s \n ?tailEntity  %s  %s  .\n}"""
sparql_tail_entities_extract_values = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n%s %s \n %s %s ?tailEntity .\n}""" 
a = '''PREFIX ns: <http://rdf.freebase.com/ns/>
SELECT DISTINCT?label ?relation
WHERE {
    # 获取邻居
    ns:%s ?relation ?neighbor .
    FILTER (?relation != rdf:name && ?relation != ns:type.object.type && ?relation != <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>)
    ?neighbor ?relation2 ?neighbor2 .
    FILTER (?relation2 != rdf:name && ?relation2 != ns:type.object.type)
    ?neighbor2 rdfs:label ?label .
    FILTER (LANG(?label) = "en")
    FILTER (isURI(?neighbor)&&isURI(?neighbor2))
}
LIMIT 100'''

def get_entity_name(entity_id):
    # 设置 SPARQL 端点
    sparql = SPARQLWrapper("http://localhost:3002/sparql")  # 替换为您的 SPARQL 端点
    sparql.setReturnFormat(JSON)

    # 构造 SPARQL 查询
    query = f"""
    SELECT ?name WHERE {{
        <{entity_id}> <http://www.w3.org/2000/01/rdf-schema#label> ?name .
        FILTER (lang(?name) = "en")
    }}
    """

    # 设置查询
    sparql.setQuery(query)

    # 执行查询并解析结果
    try:
        results = sparql.query().convert()
        names = [result for result in results['results']['bindings']]
        return names
    except Exception as e:
        print(f"Error querying SPARQL endpoint: {e}")
        return []
entity_id = "http://rdf.freebase.com/ns/m.010vz"
#names = get_entity_name(entity_id)

#print("Entity Names:", names)

rel1 = sparql_head_relations % 'm.0944j8_'# 'm.01_2n' 'm.010vz' 'm.1h3m1x5' m.042f1 m.03_r3 m.01dw03
rel2 = sparql_tail_relations % 'm.042f1'
ett1 = sparql_tail_entities_extract % ("m.0944j8_", "government.government_position_held.office_position_or_title")
ett2 = sparql_tail_entities_extract % ("m.042f1", "government.politician.government_positions_held")
type_name = sparql_id % ("m.0cgqx", "m.0cgqx")
q = "PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?x ?c ?y ?k\nWHERE {\nFILTER (?x != ?c)\nFILTER (!isLiteral(?x) OR lang(?x) = '' OR langMatches(lang(?x), 'en'))\n?c ns:location.country.national_anthem ?k .\n?k ns:government.national_anthem_of_a_country.anthem ns:m.02r0hl7 . \n?c ns:location.statistical_region.religions ?y .\n?y ns:location.religion_percentage.religion ?x .\n}\n"


q_list = [type_name]
q_list = [rel1, ett1]
for query in q_list:
    sparql = SPARQLWrapper("http://localhost:3002/sparql")
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    results = sparql.queryAndConvert()
    print(len(results["results"]["bindings"]))
    '''for i in results["results"]["bindings"]:
        if i['relation']['value'].split('/')[-1] not in relation_list and 'wiki' not in i['relation']['value'].split('/')[-1] and 'user' not in i['relation']['value'].split('/')[-1]:
            relation_list.append(i['relation']['value'].split('/')[-1])'''
    for i in results["results"]["bindings"]:
        if 'Entity' in i:
            print(i['Entity']['value']) 
        if 'tailEntity' in i:
            print(i['tailEntity']['value'])
'''
print(len(relation_list))
type_list = []
for i in relation_list:
    if len(i.split('.')) != 3:
        continue
    elif i[:-len(i.split('.')[2])-1] not in type_list:
        type_list.append(i[:-len(i.split('.')[2])-1])
print(type_list)
'''
'''
print(get_entity_name('http://rdf.freebase.com/ns/m.078tg'))
print(get_entity_name('http://rdf.freebase.com/ns/m.0jdd'))
print(get_entity_name('http://rdf.freebase.com/ns/m.02r0hl7'))
print(get_entity_name('http://rdf.freebase.com/ns/m.0h_1ft4'))
'''
