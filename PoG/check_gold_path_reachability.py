import argparse
import json
import os
import re
import sys
from collections import defaultdict, deque
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from SPARQLWrapper import JSON, SPARQLWrapper
except ImportError:
    JSON = None
    SPARQLWrapper = None


SPARQLPATH = "http://localhost:8890/sparql"
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
RAW_TRAIN_DIR = os.path.join(PROJECT_ROOT, "data", "raw_train_set")
OUT_DIR = os.path.join(SCRIPT_DIR, "gold_path_check")


DATASETS = {
    "webqsp": os.path.join(RAW_TRAIN_DIR, "WebQSP.train.json"),
    "cwq": os.path.join(RAW_TRAIN_DIR, "ComplexWebQuestions_train.json"),
    "grailqa": os.path.join(RAW_TRAIN_DIR, "grailqa_v1.0_train.json"),
}


def discover_project_root(explicit_root=""):
    candidates = []
    if explicit_root:
        candidates.append(os.path.abspath(explicit_root))
    candidates.extend(
        [
            os.path.abspath(os.path.join(os.getcwd(), "..")),
            os.path.abspath(os.path.join(SCRIPT_DIR, "..")),
            os.getcwd(),
        ]
    )
    for candidate in candidates:
        if os.path.exists(os.path.join(candidate, "data", "raw_train_set")):
            return candidate
    return candidates[0]


def dataset_paths(project_root):
    raw_train_dir = os.path.join(project_root, "data", "raw_train_set")
    return {
        "webqsp": os.path.join(raw_train_dir, "WebQSP.train.json"),
        "cwq": os.path.join(raw_train_dir, "ComplexWebQuestions_train.json"),
        "grailqa": os.path.join(raw_train_dir, "grailqa_v1.0_train.json"),
    }


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_mid(value):
    if value is None:
        return ""
    value = str(value).strip()
    value = value.strip("<>")
    value = value.replace("http://rdf.freebase.com/ns/", "")
    if value.startswith("ns:"):
        value = value[3:]
    if value.startswith(":"):
        value = value[1:]
    return value


def normalize_sparql_value(value):
    value = normalize_mid(value)
    if "^^" in value:
        value = value.split("^^", 1)[0]
    return value.strip('"')


def is_mid(value):
    value = normalize_mid(value)
    return value.startswith("m.") or value.startswith("g.")


def execute_sparql(query):
    if SPARQLWrapper is not None:
        sparql = SPARQLWrapper(SPARQLPATH)
        sparql.setQuery(query)
        sparql.setReturnFormat(JSON)
        return sparql.query().convert()["results"]["bindings"]

    payload = urlencode({"query": query, "format": "application/sparql-results+json"}).encode("utf-8")
    request = Request(
        SPARQLPATH,
        data=payload,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))["results"]["bindings"]


def execute_gold_sparql_query(sparql_query):
    if not sparql_query:
        return {
            "executed": False,
            "error": "missing_sparql",
            "values": [],
            "sample_values": [],
        }
    try:
        bindings = execute_sparql(sparql_query)
    except Exception as exc:
        return {
            "executed": False,
            "error": str(exc),
            "values": [],
            "sample_values": [],
        }

    values = set()
    for binding in bindings:
        if "x" in binding:
            values.add(normalize_sparql_value(binding["x"]["value"]))
        elif "value" in binding:
            values.add(normalize_sparql_value(binding["value"]["value"]))
        elif binding:
            first_key = sorted(binding.keys())[0]
            values.add(normalize_sparql_value(binding[first_key]["value"]))

    values = sorted(values)
    return {
        "executed": True,
        "error": None,
        "values": values,
        "sample_values": values[:50],
    }


def gold_sparql_hits_answers(sparql_result, answers):
    answer_set = {normalize_sparql_value(answer) for answer in answers}
    value_set = {normalize_sparql_value(value) for value in sparql_result.get("values", [])}
    return sorted(answer_set & value_set)


def answer_values_from_answer_list(answer_list):
    values = []
    for answer in answer_list or []:
        for key in ("answer_id", "AnswerArgument", "answer_argument"):
            if answer.get(key):
                values.append(normalize_sparql_value(answer.get(key)))
                break
    return sorted(set(values))


def values_for_step(entity_ids, relation, forward=True):
    if not entity_ids:
        return set(), None
    values = " ".join(f"ns:{normalize_mid(entity_id)}" for entity_id in sorted(entity_ids))
    if forward:
        body = f"VALUES ?s {{ {values} }}\n?s ns:{relation} ?o ."
    else:
        body = f"VALUES ?o {{ {values} }}\n?s ns:{relation} ?o ."
    query = (
        "PREFIX ns: <http://rdf.freebase.com/ns/>\n"
        "SELECT DISTINCT ?x WHERE {\n"
        f"{body}\n"
        f"BIND({'?o' if forward else '?s'} AS ?x)\n"
        "}"
    )
    try:
        bindings = execute_sparql(query)
    except Exception as exc:
        return set(), str(exc)
    return {normalize_mid(item["x"]["value"]) for item in bindings if "x" in item}, None


def execute_path(start_entities, path_steps, max_frontier=100000):
    frontier = {normalize_mid(entity_id) for entity_id in start_entities if is_mid(entity_id)}
    trace = []
    for step in path_steps:
        relation = step["relation"]
        forward = step.get("forward", True)
        next_frontier, error = values_for_step(frontier, relation, forward=forward)
        trace.append(
            {
                "relation": relation,
                "direction": "forward" if forward else "reverse",
                "input_count": len(frontier),
                "output_count": len(next_frontier),
                "sample_outputs": sorted(next_frontier)[:20],
                "error": error,
            }
        )
        if error or not next_frontier:
            return set(), trace, error
        if len(next_frontier) > max_frontier:
            next_frontier = set(sorted(next_frontier)[:max_frontier])
            trace[-1]["truncated_to"] = max_frontier
        frontier = next_frontier
    return frontier, trace, None


def answer_ids_from_answer_list(answer_list):
    ids = []
    for answer in answer_list or []:
        for key in ("answer_id", "AnswerArgument", "answer_argument"):
            if answer.get(key) and is_mid(answer.get(key)):
                ids.append(normalize_mid(answer.get(key)))
                break
    return sorted(set(ids))


def webqsp_records(data):
    for question in data.get("Questions", []):
        for parse in question.get("Parses", []):
            chain = parse.get("InferentialChain") or []
            topic_mid = normalize_mid(parse.get("TopicEntityMid"))
            answers = answer_ids_from_answer_list(parse.get("Answers"))
            yield {
                "dataset": "webqsp",
                "qid": question.get("QuestionId"),
                "parse_id": parse.get("ParseId"),
                "question": question.get("RawQuestion"),
                "topic_entities": [topic_mid] if is_mid(topic_mid) else [],
                "answers": answers,
                "answer_values": answer_values_from_answer_list(parse.get("Answers")),
                "paths": [[{"relation": rel, "forward": True} for rel in chain]],
                "path_source": "InferentialChain",
                "gold_sparql": parse.get("Sparql"),
            }


TRIPLE_RE = re.compile(
    r"(?P<s>(?:ns:|:)?m\.[A-Za-z0-9_]+|\?[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<p>(?:ns:|:)?[A-Za-z][A-Za-z0-9_.]*)\s+"
    r"(?P<o>(?:ns:|:)?m\.[A-Za-z0-9_]+|\?[A-Za-z_][A-Za-z0-9_]*)\s*\."
)


def parse_sparql_triples(sparql):
    triples = []
    for match in TRIPLE_RE.finditer(sparql or ""):
        relation = match.group("p")
        relation = relation[3:] if relation.startswith("ns:") else relation
        relation = relation[1:] if relation.startswith(":") else relation
        if relation in {"type.object.type"}:
            continue
        triples.append((match.group("s"), relation, match.group("o")))
    return triples


def build_paths_from_triples(triples, start_entities, target_var="?x", max_len=5, max_paths=20):
    adjacency = defaultdict(list)
    for subject, relation, obj in triples:
        subject_norm = normalize_mid(subject) if is_mid(subject) else subject
        obj_norm = normalize_mid(obj) if is_mid(obj) else obj
        adjacency[subject_norm].append((obj_norm, {"relation": relation, "forward": True}))
        adjacency[obj_norm].append((subject_norm, {"relation": relation, "forward": False}))

    starts = [normalize_mid(entity) for entity in start_entities if is_mid(entity)]
    paths = []
    queue = deque((start, []) for start in starts)
    while queue and len(paths) < max_paths:
        node, path = queue.popleft()
        if node == target_var and path:
            paths.append(path)
            continue
        if len(path) >= max_len:
            continue
        for next_node, step in adjacency.get(node, []):
            if any(prev_node == next_node for prev_node, _ in path):
                continue
            queue.append((next_node, path + [(next_node, step)]))
    return [[step for _, step in path] for path in paths]


def mids_from_sparql(sparql):
    return sorted(set(normalize_mid(item) for item in re.findall(r"(?:ns:|:)m\.[A-Za-z0-9_]+", sparql or "")))


def cwq_records(data):
    for item in data:
        answers = answer_ids_from_answer_list(item.get("answers"))
        answer_values = answer_values_from_answer_list(item.get("answers"))
        triples = parse_sparql_triples(item.get("sparql"))
        answer_set = set(answers)
        topic_entities = [mid for mid in mids_from_sparql(item.get("sparql")) if mid not in answer_set]
        paths = build_paths_from_triples(triples, topic_entities, target_var="?x")
        yield {
            "dataset": "cwq",
            "qid": item.get("ID"),
            "parse_id": None,
            "question": item.get("question"),
            "topic_entities": topic_entities,
            "answers": answers,
            "answer_values": answer_values,
            "paths": paths,
            "path_source": "SPARQL_triple_graph",
            "gold_sparql": item.get("sparql"),
        }


def grailqa_records(data):
    for item in data:
        answers = answer_ids_from_answer_list(item.get("answer"))
        answer_values = answer_values_from_answer_list(item.get("answer"))
        graph_query = item.get("graph_query") or {}
        nodes = {node.get("nid"): node for node in graph_query.get("nodes", [])}
        triples = []
        for edge in graph_query.get("edges", []):
            start_node = nodes.get(edge.get("start"), {})
            end_node = nodes.get(edge.get("end"), {})
            triples.append((f"?n{edge.get('start')}", edge.get("relation"), f"?n{edge.get('end')}"))
            if start_node.get("node_type") == "entity":
                triples.append((start_node.get("id"), edge.get("relation"), f"?n{edge.get('end')}"))
            if end_node.get("node_type") == "entity":
                triples.append((f"?n{edge.get('start')}", edge.get("relation"), end_node.get("id")))
        topic_entities = [
            normalize_mid(node.get("id"))
            for node in nodes.values()
            if node.get("node_type") == "entity" and is_mid(node.get("id"))
        ]
        target_nodes = [f"?n{node.get('nid')}" for node in nodes.values() if node.get("question_node") == 1]
        paths = []
        for target_node in target_nodes:
            paths.extend(build_paths_from_triples(triples, topic_entities, target_var=target_node))
        yield {
            "dataset": "grailqa",
            "qid": item.get("qid"),
            "parse_id": None,
            "question": item.get("question"),
            "topic_entities": sorted(set(topic_entities)),
            "answers": answers,
            "answer_values": answer_values,
            "paths": paths,
            "path_source": "graph_query_edges",
            "gold_sparql": item.get("sparql_query"),
        }


def iter_dataset_records(dataset_name, path):
    data = load_json(path)
    if dataset_name == "webqsp":
        yield from webqsp_records(data)
    elif dataset_name == "cwq":
        yield from cwq_records(data)
    elif dataset_name == "grailqa":
        yield from grailqa_records(data)
    else:
        raise ValueError(f"Unsupported dataset: {dataset_name}")


def check_record(record, max_paths_per_example):
    answers = set(record["answers"])
    answer_values = set(record.get("answer_values") or record["answers"])
    checked_paths = []
    reachable_answers = set()
    best_reached_count = 0
    gold_sparql_result = execute_gold_sparql_query(record.get("gold_sparql"))
    gold_sparql_hit_answers = gold_sparql_hits_answers(gold_sparql_result, answer_values)

    for path in record["paths"][:max_paths_per_example]:
        reached, trace, error = execute_path(record["topic_entities"], path)
        hit_answers = sorted(answers & reached)
        reachable_answers.update(hit_answers)
        best_reached_count = max(best_reached_count, len(reached))
        checked_paths.append(
            {
                "path": path,
                "reachable_count": len(reached),
                "hit_answers": hit_answers,
                "trace": trace,
                "error": error,
            }
        )

    status = "reachable" if reachable_answers else "unreachable"
    if not record["paths"]:
        status = "no_path_extracted"
    elif not record["answers"]:
        status = "no_entity_answer"
    elif not record["topic_entities"]:
        status = "no_topic_entity"

    return {
        **record,
        "num_paths_extracted": len(record["paths"]),
        "num_paths_checked": len(checked_paths),
        "status": status,
        "reachable": status == "reachable",
        "reachable_answers": sorted(reachable_answers),
        "gold_sparql_executed": gold_sparql_result["executed"],
        "gold_sparql_error": gold_sparql_result["error"],
        "gold_sparql_return_count": len(gold_sparql_result["values"]),
        "gold_sparql_sample_values": gold_sparql_result["sample_values"],
        "gold_sparql_hit_answers": gold_sparql_hit_answers,
        "gold_sparql_hits_answer": bool(gold_sparql_hit_answers),
        "best_reached_count": best_reached_count,
        "checked_paths": checked_paths,
    }


def update_summary(summary, row):
    dataset_summary = summary[row["dataset"]]
    dataset_summary["total"] += 1
    dataset_summary[row["status"]] += 1
    if row["reachable"]:
        dataset_summary["reachable"] += 1
    if row["answers"]:
        dataset_summary["with_entity_answer"] += 1
    if row["topic_entities"]:
        dataset_summary["with_topic_entity"] += 1
    if row["paths"]:
        dataset_summary["with_extracted_path"] += 1
    if row["gold_sparql_executed"]:
        dataset_summary["gold_sparql_executed"] += 1
    if row["gold_sparql_hits_answer"]:
        dataset_summary["gold_sparql_hits_answer"] += 1
    if row["gold_sparql_error"]:
        dataset_summary["gold_sparql_error"] += 1


def main():
    global SPARQLPATH
    parser = argparse.ArgumentParser(
        description="Check whether training gold relation paths can reach gold answer entities in the current Freebase SPARQL KG."
    )
    parser.add_argument("--datasets", nargs="+", default=["webqsp", "cwq", "grailqa"], choices=sorted(DATASETS))
    parser.add_argument("--sparql", default=SPARQLPATH, help="SPARQL endpoint URL.")
    parser.add_argument("--project_root", default="", help="Path to clue_on_graph. Auto-detected by default.")
    parser.add_argument("--limit", type=int, default=-1, help="Limit examples per dataset for debugging.")
    parser.add_argument("--max_paths_per_example", type=int, default=20)
    parser.add_argument("--output_dir", default=OUT_DIR)
    args = parser.parse_args()

    SPARQLPATH = args.sparql
    project_root = discover_project_root(args.project_root)
    paths = dataset_paths(project_root)

    os.makedirs(args.output_dir, exist_ok=True)
    summary = defaultdict(lambda: defaultdict(int))
    all_summary_rows = {}

    for dataset_name in args.datasets:
        dataset_path = paths[dataset_name]
        if not os.path.exists(dataset_path):
            raise FileNotFoundError(dataset_path)

        rows = []
        for index, record in enumerate(iter_dataset_records(dataset_name, dataset_path)):
            if args.limit >= 0 and index >= args.limit:
                break
            row = check_record(record, args.max_paths_per_example)
            rows.append(row)
            update_summary(summary, row)
            if (index + 1) % 100 == 0:
                print(f"[{dataset_name}] checked {index + 1} examples", file=sys.stderr)

        detail_path = os.path.join(args.output_dir, f"{dataset_name}_gold_path_reachability.jsonl")
        write_jsonl(detail_path, rows)
        print(f"[{dataset_name}] wrote details: {detail_path}")

    for dataset_name in args.datasets:
        item = summary[dataset_name]
        total = item["total"]
        all_summary_rows[dataset_name] = {
            "total": total,
            "reachable": item["reachable"],
            "reachable_rate": (item["reachable"] / total) if total else 0.0,
            "with_entity_answer": item["with_entity_answer"],
            "with_topic_entity": item["with_topic_entity"],
            "with_extracted_path": item["with_extracted_path"],
            "gold_sparql_executed": item["gold_sparql_executed"],
            "gold_sparql_hits_answer": item["gold_sparql_hits_answer"],
            "gold_sparql_hit_rate": (item["gold_sparql_hits_answer"] / total) if total else 0.0,
            "gold_sparql_error": item["gold_sparql_error"],
            "no_entity_answer": item["no_entity_answer"],
            "no_topic_entity": item["no_topic_entity"],
            "no_path_extracted": item["no_path_extracted"],
            "unreachable": item["unreachable"],
        }

    summary_json = os.path.join(args.output_dir, "summary.json")
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(all_summary_rows, f, ensure_ascii=False, indent=4)

    print(json.dumps(all_summary_rows, ensure_ascii=False, indent=2))
    print(f"Wrote summary: {summary_json}")


if __name__ == "__main__":
    main()
