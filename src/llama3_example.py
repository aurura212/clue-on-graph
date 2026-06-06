import transformers
#注意transformers的版本要新一点，否则不能识别新的LLM
from transformers import AutoModelForCausalLM, AutoTokenizer,BitsAndBytesConfig
import torch
import os

os.environ["CUDA_VISIBLE_DEVICES"] = "2,3,6"#设置使用服务器中哪一个gpu，否则会占用所有的GPU

#model_id的地址位于服务器上，如果在服务器上跑这个代码，可以直接使用这个。此外这个文件夹包含其他开源LLM，一般都可以用这个框架（起码qwen的可以）
#这里为了压缩模型，开了模型量化；一般来说，24G显存（一张4090）能运行8B的模型；在4bit量化下，48G显存（二张4090）能运行70B模型
model_id = "/data/share_weight/Meta-Llama-3.1-70B-Instruct"
quantization_config = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_compute_dtype=torch.float16,bnb_4bit_use_double_quant=True,bnb_4bit_quant_type="nf4")
quantized_model = AutoModelForCausalLM.from_pretrained(
    model_id, device_map="auto", quantization_config=quantization_config)
tokenizer = AutoTokenizer.from_pretrained(model_id)
pipeline = transformers.pipeline("text-generation", model=quantized_model, tokenizer=tokenizer,max_new_tokens=1024,pad_token_id=128001)

#不使用模型量化的话直接这样就行
#model_id = "/data/share_weight/Llama-3.1-8B-Instruct"
#pipeline = transformers.pipeline("text-generation", model=model_id, model_kwargs={"torch_dtype": torch.bfloat16}, device="cuda:2",max_new_tokens=1024,pad_token_id=128001)

input_text = open(
    os.path.join("./prompt/relation_finding.md"),
    'r', encoding='utf-8'
).read()

test_text='''
"Give you a question and ask you to identify the entities that the question might involve so that I can search answer of the question through the knowledge graph. 
First, you should extract the known entities in the problem, and then predict the entities related to the known entities. 
If you are not sure of the name of the entity, use concise words to describe the relationship between the entity and the known entity. 
You should use "->" to link two relevant entities, and output the chain in the format like "###entity1 -> entity2 -> entity3###". 
Question: when is the last time the the team has a team moscot named Lou Seal won the world series
'''

input_text = open(
    os.path.join("./prompt/test2.md"),
    'r', encoding='utf-8'
).read()

messages = [{"role": "user", "content": input_text}]
prompt = pipeline.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
terminators = [pipeline.tokenizer.eos_token_id, pipeline.tokenizer.convert_tokens_to_ids("<|eot_id|>")]
outputs = pipeline(prompt, eos_token_id=terminators, do_sample=True)
pred_solution="".join(outputs[0]["generated_text"][len(prompt):])
print(pred_solution)


