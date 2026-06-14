import json
import argparse
import numpy as np
from utils import *
import re

def exact_match(response, answers):
    clean_result = response.strip().replace(" ","").lower()
    for answer in answers:
        clean_answer = answer.strip().replace(" ","").lower()
        if clean_result == clean_answer or clean_result in clean_answer or clean_answer in clean_result:
            return True
    return False

def match(s1: str, s2: str) -> bool:
    s1 = s1.lower()
    s2 = s2.lower()
    return s1 == s2 or s2 in s1 or s1 in s2

def eval_acc(prediction, answer):
    matched = 0.
    for a in answer:
        if match(prediction, a):
            matched += 1
    return matched / len(answer)

def eval_hit(prediction, answer):
    for a in answer:
        if match(prediction, a):
            return 1
    return 0

def eval_f1(prediction, answer):
    if len(answer) == 0:
        return 0, 0, 0
    if len(prediction) == 0:
        return 0, 0, 0
    if type(prediction[0]) != str:
        prediction = [str(i) for i in prediction]
    matched = 0
    prediction_str = ' '.join(prediction)

    '''for a in answer:
        if type(a) == str:
            if match(prediction_str, a):
                matched += 1
        else:
            for i in a:
                for j in prediction:
                    if match(j, i):
                        matched += 1
                        break
                
                if i in prediction:
                    matched += 1
                    break
    '''
    for i in prediction:
        got = 0
        for j in answer:
            if type(j) == str:
                if match(i, j):
                    matched += 1
                    got = 1
                    break
            else:
                for k in j:
                    if match(i, k):
                        matched += 1
                        got = 1
                        break
            if got == 1:
                break
    precision = matched / len(prediction)
    recall = matched / len(answer)
    '''if precision >= 1:
        precision = 1
    if recall >= 1: 
        recall = 1'''
    if precision + recall == 0:
        return 0, precision, recall
    else:
        return 2 * precision * recall / (precision + recall), precision, recall

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

def question_process(fpath):
    if fpath.endswith('jsonl'):
        data = read_jsonl(fpath)
    else:
        data = readjson(fpath)

    return data

def check_string(string):
    return "{" in string

def clean_results(string):
    if "{" in string:
        start = string.find("{") + 1
        end = string.find("}")
        content = string[start:end]
        return content
    else:
        return "NULL"


def response2list(response):

    if response=="NULL" or response==None:
        predict_answer = []
    else:
        if '[' in response and ']' in response:
            try:
                predict_answer = eval(response)
            except:
                predict_answer = response[response.find('[')+1:response.find(']')].split(',')
        else:
            predict_answer = [response]
        predict_answer = list(set(predict_answer))
    
    return predict_answer

def align_f1(dataset_name, question_string, data, ground_truth_datas, aname_dict, alias_dict, add_ans_alias_dict):
    answer_list= []
    alias_ans = []
    origin_data = [j for j in ground_truth_datas if j[question_string] == data[question_string]][0]
    if dataset_name == 'cwq':
        add_data = aname_dict[data[question_string]]
        add_ans_alias_data = add_ans_alias_dict[data[question_string]]
        add_data += add_ans_alias_data
        if 'answers' in origin_data:
            answers = origin_data["answers"]
        else:
            answers = origin_data["answer"]
            
        if answers not in add_data:
            add_data.append(answers)
        
        answer_list = add_data
        alias_list = []
        for x in answer_list:
            if x in alias_dict.keys():
                alias_list += alias_dict[x]
        answer_list = list(set(answer_list))
        for i in answer_list:
            al = [i]
            if i in alias_dict.keys():
                al += alias_dict[i]
            alias_ans.append(al)

        answer_list = alias_ans

    elif dataset_name == 'webqsp':
        answers = origin_data["Parses"]
        for answer in answers:
            for name in answer['Answers']:
                if name['EntityName'] == None:
                    answer_list.append(name['AnswerArgument'])
                else:
                    answer_list.append(name['EntityName'])

        for i in answer_list:
            al = [i]
            if i in alias_dict.keys():
                al += alias_dict[i]
            alias_ans.append(al)
        answer_list = list(set(answer_list))
        alias_list = []
        for x in answer_list:
            if x in alias_dict.keys():
                alias_list += alias_dict[x]
        
        answer_list = alias_ans

    elif dataset_name == 'grailqa':
        answers = origin_data["answer"]
        for answer in answers:
            if "entity_name" in answer:
                answer_list.append(answer['entity_name'])
            else:
                answer_list.append(answer['answer_argument'])
        answer_list = [[i] for i in answer_list]
    #print("answers:", answer_list)
    return answer_list, origin_data

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str,
                        default="cwq", help="choose the dataset.")
    parser.add_argument("--output_file", type=str,
                        default="../PoG/PoG_cwq_gpt-3.5-turbo", help="the output file name.") # gpt-3.5-turbo gpt-4.1-mini

    args = parser.parse_args()

    ground_truth_datas, question_string, output_datas = prepare_dataset_for_eval(args.dataset, args.output_file)

    count_q = {}
    right_q = {}
    re_list = []
    error_list = []

    num_right = 0
    num_error = 0
    error_question = []

    type_field = ''
    part_q = False
    aname_dict = {}
    alias_dict = {}
    add_ans_alias_dict = {}
    call_num_list = []
    time_list = []
    token_num_list = {
        "input": [],
        "output": [],
        "total": []
    }

    precision_list = []
    recall_list = []

    if args.dataset == 'cwq':
        type_field = 'compositionality_type'
        with open('../cope_alias/cwq_aname_dict.json', 'r', encoding='utf-8') as f:
            aname_dict = json.load(f)
        with open('../cope_alias/CWQ_aliase_data31158.json', 'r', encoding='utf-8') as f:
            alias_dict = json.load(f)
        with open('../cope_alias/ComplexWebQuestions_test_wans.json', 'r', encoding='utf-8') as f:
            q_all_list = json.load(f)
            for q_item in q_all_list:
                ans_list = []
                for ans_item in q_item['answers']:
                    if ans_item['answer']:
                        ans_list.append(ans_item['answer'])
                    else:
                        ans_list.append(ans_item['answer_id'])
                    if 'aliases' in ans_item.keys():
                        ans_list += ans_item['aliases']
                
                add_ans_alias_dict[q_item['question']] = ans_list

    elif args.dataset == 'webqsp':
        with open('../cope_alias/WQSP_aliase_data.json', 'r', encoding='utf-8') as f:
            alias_dict = json.load(f)
    elif args.dataset == 'grailqa':
        type_field = 'level'
            
    if part_q:
        q_set = []
        with open('../../pog/eval/analysis_question', 'r', encoding='utf-8') as f:
            for line in f.readlines():
                q_set.append(line.strip())

    precision_list = []
    recall_list = []

    for data in output_datas:
        if part_q and data[question_string] not in q_set:
            continue

        print(data[question_string])
        answers, ori_data = align_f1(args.dataset, question_string, data, ground_truth_datas, aname_dict, alias_dict, add_ans_alias_dict)

        start_i = data['results'].find('{')
        if start_i != -1:
            try:
                results = json.loads(data['results'][start_i:])
                
                if 'A' in results.keys():
                    response = str(results['A']['Answer'])
                else:
                    response = str(results['Answer'])

                print("response", response)
                predict_answer = response2list(response)
                f1, precision, recall = eval_f1(predict_answer, answers)
                print("predict_answer:", predict_answer, "answers:", answers, "f1:", f1, "precision:", precision, "recall:", recall)
            except:
                pattern = r'"Answer":\s*["\']([^"\']+)["\']'
                match_ = list(re.finditer(pattern, data['results'][start_i:]))
                if match_:
                    response = match_[-1].group(1)
                    predict_answer = response2list(response)
                    f1, precision, recall = eval_f1(predict_answer, answers)
                    print("predict_answer:", predict_answer, "answers:", answers, "f1:", f1, "precision:", precision, "recall:", recall)
                    
                else:
                    pattern = r'"Answer":\s*(\[[^\]]+\])'
                    match_ = re.search(pattern, data['results'][start_i:])
                    if match_:
                        list_string = match_.group(1)
                        f1, precision, recall = eval_f1(list_string, answers)
                        print("predict_answer:", predict_answer, "answers:", answers, "f1:", f1, "precision:", precision, "recall:", recall)
                        
                    else:
                        predict_answer = response2list(response)
                        f1, precision, recall = eval_f1(predict_answer, answers)
                        print("predict_answer:", predict_answer, "answers:", answers, "f1:", f1, "precision:", precision, "recall:", recall)
        else:
            response = data['results']
            predict_answer = response2list(response)
            f1, precision, recall = eval_f1(predict_answer, answers)
            print("predict_answer:", predict_answer, "answers:", answers, "f1:", f1, "precision:", precision, "recall:", recall)
            
        precision_list.append(precision)
        recall_list.append(recall)

final_precision = sum(precision_list) / len(precision_list)
final_recall = sum(recall_list) / len(recall_list)
print('precision:', final_precision)
print('recall:', final_recall)
print('f1:', 2 * final_precision * final_recall / (final_precision + final_recall))