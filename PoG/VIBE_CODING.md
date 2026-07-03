# PoG 项目 Vibe Coding 原则备忘

> 适用于 `Freebase-setup/clue-on-graph/PoG/` 下的开发。基于本仓库实际踩过的坑总结，新增/改动代码时请遵守。

## 1. 长跑训练必须支持断点续跑

- 训练一次要跑数百~数千个样本，中途一定会因为 API 报错、网络断连等原因失败。
- 必须有 progress marker（参考 `memory/<run>/progress.jsonl`，每完成一个样本追加一条 `parse_id`）。
- 启动时读取 progress，跳过已完成样本；对"写到一半但没有 progress 标记"的样本，先清掉它的残留记录再重跑（参考 `output_paths.filter_jsonl_by_parse_id`）。
- **禁止"裸 except → 整段从头重跑"**。捕获异常时必须 `traceback.print_exc()` 打印真实错误，否则排障时是黑箱。
- 单条 LLM 调用失败不应导致整批重来；要么单条重试，要么标记后跳过。

反面教材：早期 `run_decomposition_memory_train` 在外层 `while True` + 裸 `except` 包裹，API 一报错就从 0/600 重来，导致 `decomposition_memory/...20260703_180427.jsonl` 膨胀到 6437 条但只有 195 个唯一问题（重复 33 次）。

## 2. LLM API 调用韧性

- `utils.run_llm` 没有内部重试，直接调 OpenAI 兼容接口。
- 项目经第三方代理（`OPENAI_API_BASE`）访问 LLM，长跑中偶发超时 / 429 / 502 / 连接重置是常态，不是异常情况。
- 新增依赖 LLM 的逻辑都要假设"这次调用可能失败"，并配合断点续跑或重试。

## 3. 输出路径组织

- 每次启动训练生成独立的时间戳文件夹：
  - memory 训练：`memory/<config_tag>_n<count>_<timestamp>/`，内含 `decomposition_memory.jsonl`、`relation_memory.jsonl`、`progress.jsonl`
  - test 运行：`result/<config_tag>_n<count>_<timestamp>/`，内含 `results.jsonl`、`pog_trace.jsonl`、`run_meta.json`
- 同一次启动的所有产物放同一文件夹，不要散落到不同时间戳的目录。
- 不要把多次启动的产物 append 进同一个文件，会重复膨胀。
- `memory_output_dir` 必须缓存在 `args` 上，外层重试时复用同一目录（否则 `default_memory_output_dir` 每次调用生成新时间戳，断点续跑失效）。

## 4. Memory 生成顺序

### 4.1 核心原则：按推理时的顺序逐跳生成

所有 memory 生成模块都必须**按 PoG 推理时实际的执行顺序**穿插运行，而不是"每个 memory 各自全量遍历一遍"。

PoG 推理时的顺序是：先做问题分解（decomposition / planning），然后逐跳检索——每跳先选 relation，再基于选出的 relation 做后续判断（反思、纠错等），然后进入下一跳。memory 生成要复刻这个顺序：

- **样本级**：先生成 decomposition memory（一次 LLM 调用得到 plan，对应推理开始时的分解步骤）。
- **跳级（每跳循环内）**：
  1. 先生成本跳的 relation memory（基于当前 frontier + gold relation）；
  2. 再基于本跳选出的 relation 生成后续依赖它的 memory（如反思 / 纠错 memory）；
  3. 推进到下一跳，重复。

### 4.2 新增 memory 模块时的要求

- 新增任何 memory 生成模块时，**必须按推理顺序嵌入到 `run_combined_memory_train` 的逐跳循环里**，不能另起一个全量遍历函数。
- 例：若新增"纠错相关 memory"，它的位置是——每跳先生成 relation memory，再根据选出的 relation 生成反思/纠错 memory，然后下一跳。而不是先跑完所有 relation memory 再跑所有纠错 memory。
- 这样保证：
  - 同一次启动的所有 memory 落在同一个 run 文件夹里；
  - 上游 memory 的产物（如选出的 relation）能直接喂给下游 memory（如纠错），不用重复调用；
  - 崩溃重启时按样本粒度续跑，不会出现"relation memory 跑完了但纠错 memory 没跑"的半成品状态。

### 4.3 当前实现

- `--train_memory_family`：`decomposition` 只跑 decomposition，`relation_choice` 只跑 relation，`all` 两者都跑。统一入口是 `run_combined_memory_train`。
- 不要做"先全量 decomposition、再全量 relation"的两段式遍历——那样两个 memory 落在不同时间戳的文件里，且任意一段崩溃都要重来。

## 5. 模块文件组织

- **不同 memory 功能的函数放在各自独立的 `.py` 文件里**，不要全塞进 `main_freebase.py`：
  - `decomposition_memory.py` — decomposition memory 的 prompt 构建、解析、读写、检索
  - `relation_memory.py` — relation memory 的生成、合并、读写、标签统计
  - 未来新增的纠错 / 反思 memory → 新建 `correction_memory.py` / `reflection_memory.py` 等
- `main_freebase.py` 只负责**汇总编排**：解析参数、加载数据集、按推理顺序调用各 memory 模块的函数、管理 run 输出目录和断点续跑。
- 各 memory 模块文件对外暴露清晰的入口函数（如 `build_gold_planning_prompt`、`append_decomposition_memory`、`append_train_relation_memories`），`main_freebase.py` import 后调用，不要把生成逻辑写在 main 里。
- 共用的底层工具（JSONL 读写、路径管理）放 `jsonl_io.py` / `output_paths.py`，不要在各 memory 文件里重复实现。

## 6. JSONL 读写约定

- 本项目用 **pretty-print 多行 JSONL**（`jsonl_io.py`，`indent=4`，一个对象跨多行）。
- 读取必须用 `iter_jsonl_records`（基于 `json.JSONDecoder.raw_decode`），**不能按行 `json.loads`**——按行读会把一个对象的第一行当独立 JSON 解析而报错。
- 写入用 `append_jsonl_record` / `append_decomposition_memory` / `append_relation_memory`，不要自己拼 `json.dumps` + `write`。

## 7. train 与 test 共享入口

- `main_freebase.py` 同时承载 train 和 test 两种 `run_mode`，外层是同一个 `while True`。
- 改 train 流程时必须确认不破坏 test 模式（test 模式靠 `load_processed_questions` 跳过已处理题）。
- `SentenceTransformer('../msmarco-distilbert-base-tas-b')` 只在需要 relation memory 时加载；decomposition-only 运行不要加载，避免引入不必要的依赖故障和几十秒启动开销。

## 8. 环境与依赖

- `torch` 与 `torchvision` 必须版本配套：
  - torch 2.6.0 ↔ torchvision 0.21.0（当前安装的是 `2.6.0+cu124` / `0.21.0+cu124`）
  - 不匹配会报 `operator torchvision::nms does not exist`
- `flash-attn` 已卸载（旧版 2.7.2.post1 是针对 torch 2.5 编译的，与新 torch ABI 不兼容）。SentenceTransformer 不依赖 flash-attn，无需重装。
- 改动涉及 torch / torchvision / transformers 时，先确认版本配对，不要混装 nightly 构建（如 `2.5.1.post303` 这种带 `.post` 后缀的）。
- `pip install --force-reinstall` 在旧安装损坏时可能因卸载阶段丢文件而失败；先 `pip uninstall` + 手动清残留 `torch/`、`*.dist-info` 目录，再干净安装。

## 9. bash 启动脚本约定

- 用环境变量带默认值传参：`VAR="${VAR:-default}"`，再在脚本里组装 `python` 的 `--flag` 参数。
- 新增 Python 参数时，要同时在脚本里加 env 变量和 `if [ -n "$VAR" ]; then args+=(--flag "$VAR"); fi` 透传。
- 脚本开头 `set -euo pipefail` + `cd "$(dirname "$0")"`，保证从任意目录执行都正确。
- 参考 `run_memory_all_train.sh` 的写法。

## 9.1 ⚠️ 每次改代码都要核对启动脚本

- **任何一次更新 `main_freebase.py` 或各 memory 模块后，必须回头检查所有 `run_*.sh` 启动脚本是否与当前代码一致**，重点核对：
  - 脚本传的 `--flag` 在 `argparse` 里是否还存在、名字是否一致、`choices` 是否还合法；
  - 脚本里 `--train_memory_family` 等枚举值是否仍是 argparse 允许的取值；
  - 脚本默认 env 值传进去后不会触发 `unrecognized arguments` 或 `invalid choice`。
- 反向同理：新增 argparse 参数时，要同步给需要它的启动脚本加上 env 透传；删除/重命名 argparse 参数时，要把所有脚本里的对应 `--flag` 一起清理。
- 已踩过的坑：`run_PoG_train.sh` 引用了已不存在的 `--evidence_state_memory_output_path` / `--failure_reflection_memory_output_path` / `--correction_action_memory_output_path` 和 `--train_memory_family experience`，默认不传时能跑，一旦设了对应 env 就崩。这类"半对接"脚本要主动清理，不能留着等踩。
- 核对方法：改完代码后 `grep` 一遍 `run_*.sh` 里出现的每个 `--flag` 是否都能在 `main_freebase.py` 的 `add_argument` 里找到。

## 10. 代码风格

- 中文注释可用，但注释要解释"为什么 / 取舍 / 约束"，不要复述代码在做什么。
- 不写 `# Import the module` / `# Define the function` / `# Increment the counter` 这类无意义注释。
- 保留现有打印风格（`color_green` / `color_yellow` / `tqdm` 进度条）。
- 新函数尽量带类型注解，与 `decomposition_memory.py`、`output_paths.py` 风格一致。

## 11. Freebase / KG 约定

- Freebase mid 以 `m.` 或 `g.` 开头；`entity_search`、`execute_gold_step` 等只接受这类 mid，非 mid 会被跳过。
- `gold_relation_path` 是训练的监督信号，逐跳驱动 relation memory 生成。
- 训练模式目前只支持 `webqsp`，不要默认假设支持其它数据集（相关函数会显式 `raise ValueError`）。

## 12. 改动前先看

- 改 memory 相关逻辑前，先读 `decomposition_memory.py`、`relation_memory.py`、`output_paths.py`、`main_freebase.py` 的 `run_combined_memory_train`。
- 改路径/文件名前，先看 `output_paths.py` 里的 `build_run_folder_name` / `get_output_file_tag` / `default_*_output_path`，不要自己另造一套命名。
- 改 LLM 调用前，先看 `utils.run_llm` 和 `is_openai_compatible_engine`。
