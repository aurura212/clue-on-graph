try:
    from SPARQLWrapper import SPARQLWrapper, JSON
except ImportError:
    SPARQLWrapper = None
    JSON = None


def require_sparqlwrapper():
    if SPARQLWrapper is None or JSON is None:
        raise ImportError("SPARQLWrapper is required for Freebase queries.")
    return SPARQLWrapper, JSON

SPARQLPATH = "http://127.0.0.1:8890/sparql"  # depend on your own internal address and port, shown in Freebase readme.md

# pre-defined sparqls
sparql_head_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ns:%s ?relation ?x .\n}"""
sparql_tail_relations = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ?x ?relation ns:%s .\n}"""

sparql_head_relations_literal = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  %s ?relation ?x .\n}"""
sparql_tail_relations_literal = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ?x ?relation %s .\n}"""

sparql_head_relations_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tail\nWHERE {\n  %s %s ?tail .\n}"""
sparql_tail_relations_values = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ?x ?relation %s .\n}"""

sparql_tail_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\nns:%s ns:%s ?tailEntity .\n}""" 
sparql_tail_entities_extract_with_type = """PREFIX ns: <http://rdf.freebase.com/ns/>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nSELECT ?tailEntity\nWHERE {\nns:%s rdf:type ?tailEntity .\n}""" 
sparql_head_entities_extract = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n?tailEntity ns:%s ns:%s  .\n}"""
sparql_id = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT DISTINCT ?tailEntity\nWHERE {\n  {\n    ?entity ns:type.object.name ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\n  UNION\n  {\n    ?entity <http://www.w3.org/2002/07/owl#sameAs> ?tailEntity .\n    FILTER(?entity = ns:%s)\n  }\n}"""
    
sparql_head_entities_extract_values = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n%s %s \n ?tailEntity  %s  %s  .\n}"""
sparql_tail_entities_extract_values = """PREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?tailEntity\nWHERE {\n%s %s \n %s %s ?tailEntity .\n}""" 


def abandon_rels(relation):
    if relation == "type.object.type" or relation == "type.object.name" or relation == "type.object.key" or relation.startswith('common.') or relation.startswith('kg.') or relation.startswith("wikipedia.") or relation == 'type.type.instance' or relation.startswith("freebase.") or "sameAs" in relation or "#" in relation:
        return True
    else:
        return False


def type_search(mid):
    SPARQLWrapper, JSON = require_sparqlwrapper()
    sparql = SPARQLWrapper(SPARQLPATH)
    query = """\nPREFIX ns: <http://rdf.freebase.com/ns/>\nSELECT ?relation\nWHERE {\n  ns:%s ?relation ?x .\n}"""
    type_list = []
    #domain_list = []
    sparql.setQuery(query%mid)
    sparql.setReturnFormat(JSON)
    results = sparql.queryAndConvert()
    #print(len(results["results"]["bindings"]))
    for i in results["results"]["bindings"]:
        #if "http://rdf.freebase.com/ns/" in i['relation']['value']:
        #   type_list.append(i['type']['value'].split('/')[-1])
            #domain_list.append(i['type']['value'].split('/')[-1].split('.')[0])
         
        r = i['relation']['value'].split('/')[-1]
        t = r.split('.')
        if 'user' in r or len(r[:-len(t[-1])-1]) == 0 or 'wikipedia' in r[:-len(t[-1])-1]:
            continue
        else:
            type_list.append(r[:-len(t[-1])-1])
    type_list = list(set(type_list))
    return type_list

def execute_sparql(sparql_txt):
    SPARQLWrapper, JSON = require_sparqlwrapper()
    sparql_txt = 'PREFIX : <http://rdf.freebase.com/ns/>\n'+sparql_txt
    try:
        sparql = SPARQLWrapper(SPARQLPATH)
        sparql.setQuery(sparql_txt)
        sparql.setReturnFormat(JSON)
        sparql.addExtraURITag("timeout", "10000")
        results = sparql.query().convert()

        res = []
        for x in results["results"]["bindings"]:
            res_item = {}
            for k, v in x.items():
                res_item[k] = v['value']
            res.append(res_item)
        return res
    except Exception as e:
        #print(e)
        print("Freebase query error")
        print(sparql_txt)
        return []


def id2entity_name_or_type_en(entity_id):
    if entity_id.startswith("m.") == False and entity_id.startswith("g.") == False:
        return entity_id
    
    SPARQLWrapper, JSON = require_sparqlwrapper()
    sparql_query = sparql_id % (entity_id, entity_id)
    sparql = SPARQLWrapper(SPARQLPATH)
    sparql.setQuery(sparql_query)
    sparql.setReturnFormat(JSON)
    try:
        results = sparql.queryAndConvert()

        if len(results["results"]["bindings"])==0:
            return entity_id
        else:
            for lines in results["results"]["bindings"]:
                if lines['tailEntity']['xml:lang']=='en':
                    return lines['tailEntity']['value']
                
            return results["results"]["bindings"][0]['tailEntity']['value']
    except:
        return entity_id


def table_result_to_list(res):
    #  transform table from this form: [{'p': 'http://rdf.freebase.com/ns/common.topic.image',
    #   's': 'http://rdf.freebase.com/ns/m.0crkzcy'},
    #  {'p': 'http://rdf.freebase.com/ns/meteorology.tropical_cyclone.tropical_cyclone_season',
    #   's': 'http://rdf.freebase.com/ns/m.06tgzm'}]
    # to this form:
    # {'p': ['http://www.w3.org/1999/02/22-rdf-syntax-ns#type',
    #   'http://www.w3.org/1999/02/22-rdf-syntax-ns#type',]
    #  's': ['http://rdf.freebase.com/ns/common.topic',
    #   'http://rdf.freebase.com/ns/common.topic']}
    if len(res) == 0:
        return []
    else:
        key_list = res[0].keys()
        result = {}
        for key in key_list:
            result[key] = list(set([item[key] for item in res]))
        return result


def freebase_value_to_id_or_literal(value):
    value = str(value)
    if value.startswith("http://rdf.freebase.com/ns/"):
        return value.rsplit("/", 1)[-1]
    return value


def get_relation_neighbors(entity_id, relation, direction="forward"):
    if direction == "forward":
        query = """SELECT ?tailEntity
WHERE {
  :%s :%s ?tailEntity .
}""" % (entity_id, relation)
    elif direction == "backward":
        query = """SELECT ?tailEntity
WHERE {
  ?tailEntity :%s :%s .
}""" % (relation, entity_id)
    else:
        raise ValueError("Unsupported direction: %s" % direction)

    rows = execute_sparql(query)
    return [
        freebase_value_to_id_or_literal(row.get("tailEntity", ""))
        for row in rows
        if row.get("tailEntity")
    ]


def instantiate_relation_path(entity_id, entity_label, relations, max_que=150, directions=None):
    if directions is None:
        directions = ("forward", "backward")

    queue = [(entity_id, entity_label, [])]
    failure_reasons = []
    max_grounded_depth = 0

    for depth, relation in enumerate(relations):
        next_queue = []
        for current_id, current_label, current_path in queue:
            neighbors = []
            for direction in directions:
                try:
                    for neighbor_id in get_relation_neighbors(current_id, relation, direction):
                        neighbors.append((neighbor_id, direction))
                except Exception as exc:
                    failure_reasons.append(
                        "%s: query failed for %s/%s: %s"
                        % (current_label, relation, direction, exc)
                    )

            if not neighbors:
                failure_reasons.append(
                    "%s: relation %s has no direct neighbor" % (current_label, relation)
                )
                continue

            max_grounded_depth = max(max_grounded_depth, depth + 1)
            for neighbor_id, direction in neighbors[:max_que]:
                neighbor_label = id2entity_name_or_type_en(neighbor_id)
                rel_text = relation if direction == "forward" else "(R %s)" % relation
                next_queue.append((
                    neighbor_id,
                    neighbor_label,
                    current_path + [(current_label, rel_text, neighbor_label)],
                ))
                if len(next_queue) >= max_que:
                    break
            if len(next_queue) >= max_que:
                break

        queue = next_queue
        if not queue:
            break

    result_paths = [
        path for _, _, path in queue
        if path and len(path) == len(relations)
    ]
    return result_paths, max_grounded_depth, failure_reasons
