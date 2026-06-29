export CUDA_VISIBLE_DEVICES="3"

python src/kgqa.py\
    --dataset WebQSP \
    --a 0 \
    --b 400 \
    --temperature 0.3 \
    --max_token 2048 \
    --max_token_reasoning 4096 \
    --max_que 150 \
    --llm gpt35 \
    --openai_api_key sk-MkBT5fWL6211EE2Cb33aT3BLbkFJ69557aEb83f4492Ea869 \
    --hop 0
    
