from prompt_list import *
import json
import time
import openai
import re
import requests
import random
from prompt_list import *
from sentence_transformers import util
from sentence_transformers import SentenceTransformer
import os
import importlib.util

_llm_api_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    "utils",
    "llm_api.py",
)
_llm_api_spec = importlib.util.spec_from_file_location("clue_on_graph_llm_api", _llm_api_path)
_llm_api = importlib.util.module_from_spec(_llm_api_spec)
_llm_api_spec.loader.exec_module(_llm_api)
get_chat_completion_extra_kwargs = _llm_api.get_chat_completion_extra_kwargs
is_openai_compatible_engine = _llm_api.is_openai_compatible_engine
from reference_utils import maybe_prepend_reference_context
from decomposition_memory import decomposition_memory_context, parse_planning_steps
from constraint_compiler import (
    format_constraints_for_prompt,
    is_constraint_pushdown_enabled,
    constraint_routing_mode,
    format_available_constraint_keys,
    parse_subobjective_routing,
    resolve_subobjective_routing,
    normalize_hop_routing,
    unassigned_compiled_constraints,
)
from constraint_runtime import ground_subobjectives, ground_subobjective_routing
from output_paths import get_current_run
from jsonl_io import append_jsonl_record

color_yellow = "\033[93m"
color_green = "\033[92m"
color_red= "\033[91m"
color_end = "\033[0m"

LLM_SYSTEM_MESSAGE = "You are an AI assistant that helps people find information."
CONTEXT_SAFETY_TOKENS = 256
MIN_COMPLETION_TOKENS = 16
LLM_REQUEST_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "180"))
LLM_MAX_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "5"))
# tiktoken.encode on multi-megabyte prompts is itself a hang; use a char heuristic above this.
_TIKTOKEN_MAX_CHARS = 80000
_TIKTOKEN_ENCODING = None


def _get_tiktoken_encoding(engine: str = ""):
    global _TIKTOKEN_ENCODING
    if _TIKTOKEN_ENCODING is not None:
        return _TIKTOKEN_ENCODING
    try:
        import tiktoken
    except ImportError:
        return None
    for candidate in (engine, "gpt-3.5-turbo", "cl100k_base"):
        if not candidate:
            continue
        try:
            if candidate == "cl100k_base":
                _TIKTOKEN_ENCODING = tiktoken.get_encoding(candidate)
            else:
                _TIKTOKEN_ENCODING = tiktoken.encoding_for_model(candidate)
            return _TIKTOKEN_ENCODING
        except Exception:
            continue
    return None


def estimate_token_count(text: str, engine: str = "") -> int:
    """Token count via tiktoken when available; otherwise a conservative heuristic."""
    text = str(text or "")
    if not text:
        return 0
    if len(text) > _TIKTOKEN_MAX_CHARS:
        return max(1, int(len(text) / 3.5))
    enc = _get_tiktoken_encoding(engine)
    if enc is not None:
        return len(enc.encode(text))
    by_words = int(len(text.split()) * 1.3)
    by_chars = int(len(text) / 3.5)
    return max(1, int(max(by_words, by_chars) * 1.45))


def get_model_context_limit(engine: str) -> int:
    name = (engine or "").lower()
    if any(k in name for k in ("gpt-4o", "gpt-4-turbo", "gpt-4.1", "o1", "o3")):
        return 128000
    if "gpt-4-32k" in name:
        return 32768
    if "gpt-4" in name:
        return 8192
    if "gpt-3.5-turbo-16k" in name or "gpt-3.5" in name:
        return 16385
    if "deepseek" in name:
        return 65536
    if "qwen" in name:
        return 32768
    return 16385


def truncate_text_to_token_budget(
    text: str,
    token_budget: int,
    suffix: str = "\n...[truncated]",
    engine: str = "",
) -> str:
    """Keep as many leading lines/words as fit in token_budget."""
    text = str(text or "").strip("\n")
    if token_budget <= 0:
        return ""
    if estimate_token_count(text, engine) <= token_budget:
        return text

    suffix_tokens = estimate_token_count(suffix, engine)
    budget = max(1, token_budget - suffix_tokens)
    # Never run tiktoken / line-scan over megabyte strings; slice first.
    max_chars = max(64, budget * 4)
    if len(text) > max_chars:
        text = text[:max_chars]
        if estimate_token_count(text, engine) <= budget:
            return text.rstrip() + suffix

    lines = text.split("\n")
    kept = []
    for line in lines:
        trial = "\n".join(kept + [line]) if kept else line
        if estimate_token_count(trial, engine) <= budget:
            kept.append(line)
            continue
        if not kept:
            char_cut = line[: max(1, budget * 3)]
            words = char_cut.split()
            lo, hi = 0, len(words)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if estimate_token_count(" ".join(words[:mid]), engine) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            if lo > 0:
                kept.append(" ".join(words[:lo]))
            else:
                kept.append(char_cut)
        break
    if not kept:
        return suffix.strip()
    return "\n".join(kept) + suffix


def truncate_knowledge_triplets_for_prompt(
    prompt_prefix: str,
    chain_prompt: str,
    engine: str,
    requested_max_tokens: int,
    safety: int = 128,
    system_overhead: int = 24,
) -> str:
    """Truncate Knowledge Triplets so prefix + triplets + completion fit the model window."""
    chain_prompt = str(chain_prompt or "")
    if not chain_prompt.strip():
        return chain_prompt

    limit = get_model_context_limit(engine)
    prefix_tokens = estimate_token_count(prompt_prefix, engine) + system_overhead
    completion_reserve = max(MIN_COMPLETION_TOKENS, int(requested_max_tokens))
    triplets_budget = limit - prefix_tokens - completion_reserve - safety
    if triplets_budget < 256:
        completion_reserve = min(completion_reserve, 256)
        triplets_budget = limit - prefix_tokens - completion_reserve - safety
    triplets_budget = max(64, triplets_budget)

    original_est = estimate_token_count(chain_prompt, engine)
    if original_est <= triplets_budget:
        return chain_prompt

    truncated = truncate_text_to_token_budget(chain_prompt, triplets_budget, engine=engine)
    print(
        f"[context] Knowledge Triplets truncated ~{original_est} -> "
        f"~{estimate_token_count(truncated, engine)} tokens (budget={triplets_budget}, engine={engine})"
    )
    return truncated


def _estimate_messages_tokens(messages, engine: str = "") -> int:
    # Per-message framing overhead is small but non-zero for chat APIs.
    return sum(estimate_token_count(m.get("content", ""), engine) + 4 for m in messages) + 2


def fit_messages_and_max_tokens(messages, engine: str, requested_max_tokens: int):
    """Shrink user content / max_tokens so prompt + completion stay under the context limit."""
    limit = get_model_context_limit(engine)
    requested_max_tokens = max(MIN_COMPLETION_TOKENS, int(requested_max_tokens))
    messages = [dict(m) for m in messages]
    user_idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)

    for attempt in range(8):
        prompt_est = _estimate_messages_tokens(messages, engine)
        available = limit - prompt_est - CONTEXT_SAFETY_TOKENS
        effective = min(requested_max_tokens, max(MIN_COMPLETION_TOKENS, available))

        if prompt_est + effective + CONTEXT_SAFETY_TOKENS <= limit:
            if effective < requested_max_tokens:
                print(
                    f"[context] max_tokens reduced {requested_max_tokens} -> {effective} "
                    f"(prompt≈{prompt_est}, limit={limit}, engine={engine})"
                )
            return messages, effective

        if user_idx is None:
            return messages, MIN_COMPLETION_TOKENS

        other_est = _estimate_messages_tokens([m for i, m in enumerate(messages) if i != user_idx], engine)
        completion_reserve = min(requested_max_tokens, max(MIN_COMPLETION_TOKENS, effective))
        user_budget = limit - other_est - completion_reserve - CONTEXT_SAFETY_TOKENS
        if user_budget < 64:
            completion_reserve = MIN_COMPLETION_TOKENS
            user_budget = max(32, limit - other_est - completion_reserve - CONTEXT_SAFETY_TOKENS)

        original = messages[user_idx].get("content", "")
        original_est = estimate_token_count(original, engine)
        if original_est > user_budget:
            messages[user_idx]["content"] = truncate_text_to_token_budget(
                original,
                user_budget,
                suffix="\n...[truncated to fit context window]",
                engine=engine,
            )
            print(
                f"[context] prompt truncated ~{original_est} -> "
                f"~{estimate_token_count(messages[user_idx]['content'], engine)} tokens "
                f"(user_budget={user_budget}, engine={engine}, attempt={attempt + 1})"
            )
        else:
            requested_max_tokens = max(MIN_COMPLETION_TOKENS, completion_reserve // 2)

    prompt_est = _estimate_messages_tokens(messages, engine)
    effective = max(MIN_COMPLETION_TOKENS, limit - prompt_est - CONTEXT_SAFETY_TOKENS)
    print(
        f"[context] max_tokens fallback {requested_max_tokens} -> {effective} "
        f"(prompt≈{prompt_est}, limit={limit}, engine={engine})"
    )
    return messages, effective


def _is_transient_llm_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return True
    return type(exc).__name__ in {"ConnectTimeout", "ReadTimeout", "ConnectError", "RemoteProtocolError"}


def retrieve_top_docs(query, docs, model, width=3):
    query_emb = model.encode(query)
    doc_emb = model.encode(docs)
    scores = util.dot_score(query_emb, doc_emb)[0].cpu().tolist()
    doc_score_pairs = sorted(list(zip(docs, scores)), key=lambda x: x[1], reverse=True)
    top_docs = [pair[0] for pair in doc_score_pairs[:width]]
    top_scores = [pair[1] for pair in doc_score_pairs[:width]]
    return top_docs, top_scores

def run_llm(prompt, temperature, max_tokens, openai_api_keys, engine="gpt-3.5-turbo", print_in=True, print_out=True):
    if print_in:
        if len(str(prompt or "")) > 20000:
            print(color_green + str(prompt)[:20000] + "\n...[prompt omitted]..." + color_end)
        else:
            print(color_green+prompt+color_end)

    if is_openai_compatible_engine(engine):
        messages = [
            {"role": "system", "content": LLM_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]
        messages, effective_max_tokens = fit_messages_and_max_tokens(messages, engine, max_tokens)
        client = openai.OpenAI(
            api_key=openai_api_keys,
            base_url=os.environ['OPENAI_API_BASE'],
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=0,
        )
        completion_kwargs = {
            "model": engine,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
            "frequency_penalty": 0,
            "presence_penalty": 0,
        }
        completion_kwargs.update(get_chat_completion_extra_kwargs(engine))

        completion = None
        last_error = None
        for attempt in range(LLM_MAX_RETRIES):
            try:
                completion = client.chat.completions.create(**completion_kwargs)
                break
            except openai.BadRequestError as exc:
                last_error = exc
                err_text = str(exc).lower()
                if "context_length_exceeded" not in err_text or attempt >= LLM_MAX_RETRIES - 1:
                    raise
                print(f"[context] API rejected request (attempt {attempt + 1}), shrinking prompt and retrying...")
                user_idx = next(
                    (i for i in range(len(completion_kwargs["messages"]) - 1, -1, -1)
                     if completion_kwargs["messages"][i].get("role") == "user"),
                    None,
                )
                if user_idx is None:
                    raise
                original = completion_kwargs["messages"][user_idx]["content"]
                shrink_budget = max(
                    32,
                    int(estimate_token_count(original, engine) * 0.65),
                )
                completion_kwargs["messages"][user_idx]["content"] = truncate_text_to_token_budget(
                    original,
                    shrink_budget,
                    suffix="\n...[truncated after context_length_exceeded]",
                    engine=engine,
                )
                completion_kwargs["messages"], completion_kwargs["max_tokens"] = fit_messages_and_max_tokens(
                    completion_kwargs["messages"],
                    engine,
                    max(MIN_COMPLETION_TOKENS, completion_kwargs["max_tokens"] // 2),
                )
            except Exception as exc:
                last_error = exc
                if not _is_transient_llm_error(exc) or attempt >= LLM_MAX_RETRIES - 1:
                    raise
                wait_s = min(2 ** attempt, 30)
                print(
                    f"[llm] transient error ({type(exc).__name__}), "
                    f"retry {attempt + 1}/{LLM_MAX_RETRIES} in {wait_s}s..."
                )
                time.sleep(wait_s)
        if completion is None:
            raise last_error

        result = completion.choices[0].message.content

        token_num = {"total": completion.usage.total_tokens, "input": completion.usage.prompt_tokens, "output": completion.usage.completion_tokens}

        if print_out:
            print(color_yellow + result + color_end)
        return result, token_num

    raise ValueError(f"Unsupported LLM engine: {engine}")


def convert_dict_name(ent_rel_ent_dict, entid_name):
    name_dict = {}
    for topic_e, h_t_dict in ent_rel_ent_dict.items():
        if entid_name[topic_e] not in name_dict.keys():
            name_dict[entid_name[topic_e]] = {}

        for h_t, r_e_dict in h_t_dict.items():
            if h_t not in name_dict[entid_name[topic_e]].keys():
                name_dict[entid_name[topic_e]][h_t] = {}
            
            for rela, e_list in r_e_dict.items():
                if rela not in name_dict[entid_name[topic_e]][h_t].keys():
                    name_dict[entid_name[topic_e]][h_t][rela] = []
                for ent in e_list:
                    if entid_name[ent] not in name_dict[entid_name[topic_e]][h_t][rela]:
                        name_dict[entid_name[topic_e]][h_t][rela].append(entid_name[ent])
    return name_dict


def format_capped_list(items, limit=70):
    items = list(items)
    if len(items) <= limit:
        return str(items)
    return str(items[:limit]) + f" ...(+{len(items) - limit} more)"


def summarize_name_dict(name_dict, max_per_rel=20):
    """Shrink convert_dict_name output so pprint cannot freeze the terminal."""
    summary = {}
    for topic, h_t_dict in name_dict.items():
        summary[topic] = {}
        for h_t, r_e_dict in h_t_dict.items():
            summary[topic][h_t] = {}
            for rela, e_list in r_e_dict.items():
                e_list = list(e_list)
                if len(e_list) > max_per_rel:
                    summary[topic][h_t][rela] = e_list[:max_per_rel] + [f"... +{len(e_list) - max_per_rel} more"]
                else:
                    summary[topic][h_t][rela] = e_list
    return summary

    

def save_2_jsonl(question, question_string, answer, cluster_chain_of_entities, call_num, all_t, start_time, file_name=None, pog_trace=None):
    tt = time.time()-start_time
    result_dict = {
        question_string: question,
        "results": answer,
        "reasoning_chains": cluster_chain_of_entities,
        "call_num": call_num,
        "total_token": all_t['total'],
        "input_token": all_t['input'],
        "output_token": all_t['output'],
        "time": tt,
    }

    run = get_current_run()
    if run:
        results_path = run["results_path"]
        trace_path = run["trace_path"]
    else:
        tag = file_name or "default"
        results_path = f"PoG_{tag}.jsonl"
        trace_path = f"PoG_{tag}_trace.jsonl"

    append_jsonl_record(results_path, result_dict)

    if pog_trace is not None:
        trace_dict = {question_string: question, "pog_trace": pog_trace}
        append_jsonl_record(trace_path, trace_dict)


def extract_add_ent(string):
    first_brace_p = string.find('[')
    last_brace_p = string.rfind(']')
    string = string[first_brace_p:last_brace_p+1]
    try:
        new_string = eval(string)
    except:
        s_list = string.split('\', \'')
        if len(s_list) == 1:
            new_string = [s_list[0].strip('[\'').strip('\']')]
        else:
            new_string = [s.strip('[\'').strip('\']') for s in s_list]
    return new_string

def extract_memory(string):
    first_brace_p = string.find('{')
    last_brace_p = string.rfind('}')
    string = string[first_brace_p:last_brace_p+1]
    return string

def extract_reason_and_anwer(string):
    first_brace_p = string.find('{')
    last_brace_p = string.rfind('}')
    string = string[first_brace_p:last_brace_p+1]
    answer = re.search(r'"Answer":\s*"(.*?)"', string)
    try:
        if answer:
            answer = answer.group(1)
        else:
            answer = re.search(r'"Answer":\s*(\[[^\]]+\])', string).group(1)
    except:
        return None, None, None, None

    reason = re.search(r'"R":\s*"(.*?)"', string).group(1)
    sufficient = re.search(r'"Sufficient":\s*"(.*?)"', string).group(1)
    progress = extract_subobjective_progress(string)
    print("Answer:", answer)
    print("Reason:", reason)
    print("Sufficient:", sufficient)
    if progress is not None:
        print("Subobjective_Progress:", progress)
    return answer, reason, sufficient, progress


def extract_subobjective_progress(string):
    """Parse 1-based completed-subobjective count. None if the field is absent."""
    match = re.search(r'"Subobjective_Progress"\s*:\s*"?(\d+)"?', str(string or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None

def extract_add_and_reason(string):
    first_brace_p = string.find('{')
    last_brace_p = string.rfind('}')
    string = string[first_brace_p:last_brace_p+1]
    flag = re.search(r'"Add":\s*"(.*?)"', string).group(1)
    reason = re.search(r'"Reason":\s*"(.*?)"', string).group(1)

    print("Add:", flag)
    print("Reason:", reason)
    if 'yes' in flag.lower():
        return True, reason
    else:
        return False, reason
    


def generate_without_explored_paths(question, subquestions, args):
    prompt = cot_prompt + question 
    prompt = maybe_prepend_reference_context(prompt, args, stage="cot")

    response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False)
    return response, token_num


def extract_list_output(text):
    first_brace_p = text.find('[')
    last_brace_p = text.rfind(']')
    if first_brace_p >= 0 and last_brace_p > first_brace_p:
        return text[first_brace_p:last_brace_p+1]
    return str(text).strip()


def break_question(question, args):
    routing_mode = constraint_routing_mode(args)
    use_routing_prompt = routing_mode in {"auto", "on"}
    prompt = (subobjective_with_constraints_prompt if use_routing_prompt else subobjective_prompt) + question
    if is_constraint_pushdown_enabled(args):
        topic_names = [str(name) for name in (getattr(args, "current_topic_entity", {}) or {}).values() if name]
        extra_parts = []
        if topic_names:
            extra_parts.append("Topic Entities: " + "; ".join(topic_names))
        constraint_context = format_constraints_for_prompt(getattr(args, "current_constraints", {}) or {})
        if constraint_context:
            extra_parts.append(constraint_context)
        if use_routing_prompt:
            available_keys = format_available_constraint_keys(getattr(args, "current_constraints", {}) or {})
            if available_keys:
                extra_parts.append("Available constraint keys: " + "; ".join(available_keys))
                extra_parts.append(
                    "You must assign every available constraint key to at least one subobjective. "
                    "Use [] only for steps that do not apply a constraint. "
                    "Do not invent extra keys."
                )
        extra_parts.append(
            "Do not add extra quoted office/type filters. Answer-type words are not graph constraints. "
            "Topic entities are start nodes. Extra quoted filters must come from Topic Entities or Question Constraints."
        )
        prompt += "\n" + "\n".join(extra_parts)
    memory_context = decomposition_memory_context(
        getattr(args, "decomposition_memory_bank", []),
        question,
        getattr(args, "current_topic_entity", {}) or {},
        args,
        getattr(args, "sentence_model", None),
    )
    if memory_context:
        prompt = memory_context + "\n\nCurrent task:\n" + prompt
    prompt = maybe_prepend_reference_context(prompt, args, stage="decomposition")
    response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type, False, False)
    setattr(args, "current_decomposition_memory_context", memory_context)
    setattr(args, "current_decomposition_prompt", prompt)
    setattr(args, "current_decomposition_raw_output", response)
    raw_list = extract_list_output(response)

    routing = None
    routing_status = "off"
    if use_routing_prompt:
        routing = parse_subobjective_routing(raw_list)
        if routing is not None:
            routing_status = "ok"
            steps = [item["step"] for item in routing]
            routing, grounding_trace = ground_subobjective_routing(routing, question, args)
            routing = normalize_hop_routing(routing)
            steps = [item["step"] for item in routing]
        else:
            steps = parse_planning_steps(raw_list)
            steps, grounding_trace = ground_subobjectives(steps, question, args)
            routing_status = "legacy_list" if steps else "failed"
            if routing_mode == "auto":
                print("Warning: constraint routing parse failed; falling back to full constraints")
            else:
                print("Warning: constraint routing unavailable; skipping SPARQL constraint pushdown")
    else:
        steps = parse_planning_steps(raw_list)
        steps, grounding_trace = ground_subobjectives(steps, question, args)

    setattr(args, "current_decomposition_grounding", grounding_trace)
    setattr(args, "sub_constraint_routing", routing)
    compiled = getattr(args, "current_constraints", None) or {}
    if routing:
        resolved = resolve_subobjective_routing(routing, compiled)
        if compiled:
            compiled["resolved_routing"] = resolved
            compiled["sub_constraint_routing"] = routing
        setattr(args, "resolved_constraint_routing", resolved)
        leftover = unassigned_compiled_constraints(resolved, compiled)
        leftover_keys = []
        leftover_keys.extend(f"entity:{item.get('mention')}" for item in leftover.get("entity_constraints") or [])
        leftover_keys.extend(f"time:{item.get('kind')}" for item in leftover.get("time_constraints") or [])
        leftover_keys.extend(f"rank:{item.get('kind')}" for item in leftover.get("order_constraints") or [])
        if leftover_keys:
            print("Warning: decomposition omitted constraint keys; keeping them active on every hop: " + ", ".join(leftover_keys))
    else:
        if compiled:
            compiled["resolved_routing"] = None
            compiled["sub_constraint_routing"] = None
        setattr(args, "resolved_constraint_routing", None)
    setattr(args, "constraint_routing_status", routing_status)

    if steps:
        response = json.dumps(steps, ensure_ascii=False)
    else:
        response = raw_list
    return response, token_num

def get_subquestions(q_mem_f_path, question, args):
    sub_questions, token_num = break_question(question, args)
    with open(q_mem_f_path+'/'+'subq', 'w', encoding='utf-8') as f:
        f.write(str(sub_questions))

    return sub_questions, token_num

def if_finish_list(question, lst, depth_ent_rel_ent_dict, entid_name, name_entid, q_mem_f_path, results, cluster_chain_of_entities, args, model):
    cur_call_time = 0
    cur_token = {'total': 0, 'input': 0, 'output': 0}

    with open(q_mem_f_path+'/mem', 'r', encoding='utf-8') as f:
        his_mem = f.read()

    if all(elem == "[FINISH_ID]" for elem in lst):
        new_lst = []
    else:
        new_lst = [elem for elem in lst if elem != "[FINISH_ID]"]
    
    all_ent_set = set()
    for dep, ent_rel_ent_dict in depth_ent_rel_ent_dict.items():
        for topic_e, h_t_dict in ent_rel_ent_dict.items():
            all_ent_set.add(topic_e)
            for h_t, r_e_dict in h_t_dict.items():
                for rela, e_list in r_e_dict.items():
                    if all(entid_name[item].startswith('m.') for item in e_list) and len(e_list)>10:
                        e_list = random.sample(e_list, 10)
                        
                    if len(e_list) > 70:
                        print('··········exceed 70 entities··········')
                        sorted_e_list = [entid_name[e_id] for e_id in e_list]
                        topn_entities, topn_scores = retrieve_top_docs(question, sorted_e_list, model, 70)
                        print('sentence:', topn_entities)
                        e_list = [name_entid[e_n] for e_n in topn_entities]
                        all_ent_set |= (set(e_list))

    chain_prompt = '\n'.join([', '.join([str(x) for x in chain]) for sublist in cluster_chain_of_entities for chain in sublist])

    from kg_memory_retrieval import retrieve_reflection_records, should_use_kg_memory_at_stage
    from reflection_structural_memory import (
        build_reflection_event,
        compact_event_for_trace,
        explored_relation_paths,
        maybe_prepend_reflection_evidence,
    )

    frontier_names = list(set(sorted([entid_name[ent_i] for ent_i in new_lst])))
    explored_paths = explored_relation_paths(depth_ent_rel_ent_dict)
    entity_records_a = []
    if should_use_kg_memory_at_stage(args, "reflection_judge") or should_use_kg_memory_at_stage(args, "reflection"):
        entity_records_a = retrieve_reflection_records(args, list(new_lst))
    event_a = build_reflection_event(
        stage="reflection_a",
        args=args,
        candidate_frontier=frontier_names,
        entity_records=entity_records_a,
        already_explored_paths=explored_paths,
    )

    prefix = (
        judge_reverse + question
        + '\nEntities set to be retrieved: ' + str(list(set(sorted([entid_name[ent_i] for ent_i in new_lst]))))
        + '\nMemory: ' + his_mem
        + '\nKnowledge Triplets:'
    )
    prefix = maybe_prepend_reflection_evidence(prefix, event_a)
    budget_prefix = maybe_prepend_reference_context(prefix, args, stage="reverse")
    chain_prompt = truncate_knowledge_triplets_for_prompt(
        budget_prefix, chain_prompt, args.LLM_type, args.max_length,
    )
    prompt = maybe_prepend_reference_context(prefix + chain_prompt, args, stage="reverse")

    cur_call_time += 1
    response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type)
    for kk in token_num.keys():
        cur_token[kk] += token_num[kk]

    flag, reason = extract_add_and_reason(response)
    event_a["llm_decision"] = "continue" if flag else "stop"
    event_a["selected_entity"] = None
    reflection_trace = {
        "decision_a": {
            "add": bool(flag),
            "reason": reason,
            "llm_raw_output": response,
            "frontier_entities": frontier_names,
            "evidence": compact_event_for_trace(event_a),
        },
        "decision_b": {
            "invoked": False,
            "candidate_entities": [],
            "selected_entities": [],
            "selected_entity_ids": [],
            "llm_raw_output": None,
            "evidence": compact_event_for_trace(
                build_reflection_event(
                    stage="reflection_b",
                    args=args,
                    candidate_frontier=[],
                    records=[],
                    already_explored_paths=explored_paths,
                )
            ),
        },
    }

    if flag:
        other_entities = sorted(list(all_ent_set - set(new_lst)))
        other_entities_name = [entid_name[ent_i] for ent_i in other_entities]
        
        print('filter already', [entid_name[ent_i] for ent_i in new_lst], [entid_name[ent_i] for ent_i in all_ent_set], other_entities_name)

        entity_records_b = []
        if should_use_kg_memory_at_stage(args, "reflection_select") or should_use_kg_memory_at_stage(args, "reflection"):
            entity_records_b = retrieve_reflection_records(args, list(other_entities))
        event_b = build_reflection_event(
            stage="reflection_b",
            args=args,
            candidate_frontier=sorted(other_entities_name),
            entity_records=[
                (entid_name.get(eid, eid), record) for eid, record in entity_records_b
            ],
            already_explored_paths=explored_paths,
        )

        prompt = add_ent_prompt+question+'\nReason: '+reason+'\nCandidate Entities: ' + format_capped_list(sorted(other_entities_name), 70)+'\nMemory: '+his_mem
        prompt = maybe_prepend_reflection_evidence(prompt, event_b)
        prompt = maybe_prepend_reference_context(prompt, args, stage="add_entity")

        cur_call_time += 1
        select_response, token_num = run_llm(prompt, args.temperature_reasoning, args.max_length, args.opeani_api_keys, args.LLM_type)

        for kk in token_num.keys():
            cur_token[kk] += token_num[kk]

        add_ent_list = extract_add_ent(select_response)
        add_ent_list = [name_entid[ent_i] for ent_i in add_ent_list if ent_i in other_entities_name]
        add_ent_list = sorted(add_ent_list)
        event_b["llm_decision"] = ",".join(entid_name[ent_i] for ent_i in add_ent_list) if add_ent_list else ""
        event_b["selected_entity"] = entid_name[add_ent_list[0]] if add_ent_list else None
        reflection_trace["decision_b"] = {
            "invoked": True,
            "candidate_entities": sorted(other_entities_name),
            "selected_entities": [entid_name[ent_i] for ent_i in add_ent_list],
            "selected_entity_ids": list(add_ent_list),
            "llm_raw_output": select_response,
            "evidence": compact_event_for_trace(event_b),
        }
        if add_ent_list:
            print('add reverse ent:', len(add_ent_list), [entid_name[ent_i] for ent_i in add_ent_list])
            return new_lst, add_ent_list, cur_call_time, cur_token, reflection_trace
    return new_lst, [], cur_call_time, cur_token, reflection_trace

    
def prepare_dataset(dataset_name):
    if dataset_name == 'cwq':
        with open('../data/cwq.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'cwq0':
        with open('../data/cwq0.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'cwq1':
        with open('../data/cwq1.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'cwq2':
        with open('../data/cwq2.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'cwq_split':
        with open('../data/cwq_split.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'webqsp':
        with open('../data/WebQSP.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'RawQuestion'
    elif dataset_name == 'webqsp_split':
        with open('../data/WebQSP_split.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'RawQuestion'
    elif dataset_name == 'grailqa':
        with open('../data/grailqa.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    elif dataset_name == 'grailqa_split':
        with open('../data/grailqa_split.json',encoding='utf-8') as f:
            datas = json.load(f)
        question_string = 'question'
    else:
        print("dataset not found, you should pick from {cwq, webqsp, grailqa}.")
        exit(-1)
    return datas, question_string

