# PoG 自主路径探索与关系语义记忆：实现与验收方案

方案日期：2026-10-19。方法与接口已实现；效果验证须通过真实 KG、模型和独立测试集完成。

## 1. 目标与训练协议

模型从问题与 topic entity 出发，自主分解问题、选择关系方向和下一跳实体。外层控制器在达到训练 parse 的参考关系跳数 H 时停止，独立监督器再评价路径。失败后积累有来源的关系解释与对比经验，从起点重新探索，最多三次。训练结束后冻结经验，在测试中只读使用。

本阶段不训练停止或回溯，不引入 Q 值，不在测试期间标注关系或更新长期记忆。

```text
训练问题与起始实体
→ 仅根据问题生成子目标
→ 自主探索 H 条实际 KG 边
→ 参考路径与答案联合评价
→ 失败诊断、按需语义标注与对比解释
→ 新记忆用于本题下一次完整探索
→ 成功或达到三次上限
→ 所有记录写入后提交 progress
→ 下一训练问题读取已提交记忆
```

### 1.1 信息隔离与终止

- 选择器只得到问题、topic entity、非监督子目标、实际探索状态与允许检索的经验。
- 监督器独占参考关系、SPARQL、答案、参考延续实体与 H；这些字段不进入首次 actor prompt。
- 不使用旧的 gold-informed 分解，不为凑足 H 而插入 gold relation 子目标。
- 同题重试复用首次分解，但清空路径、题内工作记忆和旧缓存。只保留新长期经验。
- 每条实际 KG 边计一跳，CVT 两条边计两跳。CVT 辅助邻居探测不增加主路径跳数。
- 达到 H 后不再扩展。空候选、筛选为空或提前到达字面值时保留失败/不完整路径，不补路径，不回退生成答案。
- 本阶段不调用停止/回溯决策，因此模型的 `Sufficient=Yes` 不改变探索预算。
- 同题、同起点、相同 H 的 sibling parses 可作为参考，但其路径和答案分别匹配，禁止交叉拼接。

## 2. 代码修改位置、接口和数据流

### 2.1 训练入口与参数

`main_freebase.py:add_relation_memory_args()` 增加：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--relation_train_strategy` | `gold_path` | 旧策略或 `autonomous_path` |
| `--max_path_attempts` | `3` | 完整路径尝试次数 |
| `--train_rollout_path_limit` | `50` | 每层路径前缀预算 |
| `--relation_action_format` | `legacy` | 旧输出或 `directional` 动作编号 |
| `--relation_semantics_mode` | `off` | `off` 或 `on_demand` |
| `--relation_semantics_sample_limit` | `3` | 语义实例采样上限 |

解析后立即调用 `validate_autonomous_args()`，在网络访问前检查组合。新策略仅支持训练 split、WebQSP、`train_memory_family=relation_choice` 和 directional 输出；要求 reference/decomposition/reflection memory 及 constraint pushdown 关闭。非正预算直接报错。

训练数据切片前保留 sibling parses，切片仅决定执行哪些 episode。`memory_train.py:run_combined_memory_train()` 根据新参数分流；旧 gold-path 主体不改写。新入口不调用旧的 gold 路径状态生成函数。

### 2.2 自主探索：`autonomous_path_train.py`

| 函数/类 | 职责 |
|---|---|
| `run_autonomous_path_memory_train` | 配置快照、恢复、全局检索库、逐题提交、统计 |
| `run_training_episode` | 一次自主分解、最多三次探索、反馈 overlay、按需标注 |
| `run_autonomous_rollout` | H 跳控制与分支扩展；不持有参考答案 |
| `extend_path_prefixes` | 按 ID 连接、去重、稳定排序与截断 |
| `make_supervisor` | 为当前 episode 建立同题兼容参考集合 |
| `PoGBackend` | 适配已有 PoG 关系/实体选择、KG 查询和题内记忆函数 |

ActorInput 为字典：`question, topic_entities, subobjectives`。SupervisorInput 为字典：`question_id, parse_id, topic_entities, hop_limit, references`；每条 reference 独立保存 `parse_id, relations, answers`。

`PoGBackend` 仅收到 actor 对象。建立 selector args 时移除 gold 字段、全部 current 缓存、整份参考数据与旧 memory banks；明确清空 reference context。训练时 relation bank 使用逐题更新的长期经验，测试仍按自己的 `relation_memory_mode` 开关读取。

每个路径前缀单独探索，前一关系及方向来自真实前缀。复用：

```text
relation_search_prune
→ entity_search
→ entity_condition_prune
→ 按筛选后真实边扩展路径
→ update_memory(memory_override=当前题内记忆, persist_memory=False)
```

路径按起点、实体 ID、关系及方向保存；同名实体使用唯一展示标签，不用名称反推身份。多个前缀到达同一实体仍各自保留。每层先去重，再稳定截断到 50 条；候选预算截断写入 trace。

RolloutResult：`paths, step_traces, observed_edges, working_memory, stop_reason, truncated, usage`。实体筛选不能返回未出现在该轮查询证据中的边。

### 2.3 有向动作：`freebase_func.py`

`relation_search_prune()` 在关系预筛后，把同一关系的出边和入边拆成动作并分配本 prompt 局部编号：

```text
R1: people.person.place_of_birth | outgoing
R2: people.person.place_of_birth | incoming
```

`construct_relation_prune_prompt()` 在 directional 模式使用专门指令，不混用旧的裸关系输出范例。模型返回 `{"selected_actions":["R2"]}`。

`parse_directional_actions()` 使用 JSON 解析，只接受已展示、非空的动作编号列表；出现未知编号时整次选择失败，不回退到任意关系。解析后保留 `head` 字段适配原实体查询函数。legacy 模式保留原入口。

PathEdge 的必要字段：

```python
{
    "subject_id": "m.person",
    "relation": "people.person.place_of_birth",
    "object_id": "m.city",
    "traversal_from": "m.city",
    "traversal_to": "m.person",
    "direction": "incoming"
}
```

`entity_condition_prune()`、`update_memory()`、`reasoning()` 使用 `format_directed_bucket()` 表达实际事实方向；反向探索时人物仍是出生关系的主语。输出链也按实际事实顺序保存。

`utils.py:break_question()` 在新训练策略中补入 topic entity 名称，但不含参考关系或跳数。

## 3. 监督评价与按需记忆

### 3.1 路径评价：`path_evaluation.py`

`evaluate_rollout()` 检查路径起点、实际查询证据、事实与探索方向、边连接以及长度。逐路径匹配参考关系序列，并仅使用对应 parse 的答案判断末端。满足这些条件时返回 `reference_path_found=True`，结束本题重试；该结果不等于全部问题约束已获语义证明。

全部有效 H 跳路径末端组成预测集合。分别对每个参考 parse 计算 precision、recall、F1 和集合 exact match，主指标对应当前 parse。发现一条有效路径但存在其他错误末端时，答案集合指标仍降低。

实体答案优先使用规范化 ID，不使用名称子串；数字等字面值按确定性规则规范化。非参考路径命中答案时记为 `alternative_unverified`，不提升为确定正确解法，也不作为确定语义负例。

`diagnose_rollout()` 区分：

```text
candidate_recall_failure
relation_reference_deviation
entity_selection_failure
empty_query_result
direction_or_connectivity_error
budget_truncation
unresolved
```

参考关系在原始候选中却被预筛删除属于召回失败。参考关系偏离只表示路径监督不一致，不直接表示语义错误。查询异常和空结果分别记录。

`build_reference_evidence()` 仅供监督器使用：同时查询出边和入边，通过对应答案反向筛选参考路径。状态预算为 50；截断或查询异常时 `complete=False`，不能根据缺少证据断言实体选择错误。

### 3.2 关系标注：`relation_semantics.py`

只处理成功路径上使用的关系，以及失败路径首次参考偏离处的所选关系与参考关系。后续偏离不重复归因，其他可疑关系留在轨迹中。

以 `(KG 标识, relation ID)` 缓存解释，缺失时 `get_relation_evidence()` 读取可用名称、描述、schema、expected type 和最多 3 条实例。实例类型属于观察，不作为完整 schema 定义。

`annotate_relation_meaning()` 要求模型输出：`meaning, subject_role, object_role, outgoing, incoming, boundaries`。所有模型解释标记 inferred；缺失字段为 unknown，解析失败保留 unknown。原始证据、来源和模型响应保存用于审计。

遇到新混淆时，`PoGBackend.contrast()` 结合当前子目标、所选关系解释和参考关系解释，生成 `difference, applicability, uncertainty`。禁止仅凭不匹配参考路径断言关系无效。

off 模式仍可积累用法与参考偏离经验，但不调用语义标注模型；它用于控制语义内容的消融。

### 3.3 可检索记忆：`relation_memory.py`

新记录 `schema_version=2`：

```text
memory_id, memory_type(usage/contrast)
question_id, parse_id, attempt_index
state(subobjective/entity_role/path_prefix)
selected_action, reference_action
semantic_claims, reference_semantic_claims
contrast_explanation, evidence_refs
diagnosis, verification_status
```

`build_relation_contrast_memory()` 生成稳定 ID；同题同一情境不重复写入。已有对比解释不会被后续重试覆盖丢失。

检索同时匹配所选关系和参考关系，继续使用 question/state hybrid 排序、top-k 与 token budget，优先方向一致的经验。旧记录按原 formatter 展示，缺失方向不补造。

formatter 仅投影语义解释、情境和对比字段，不展示训练答案 ID、参考 SPARQL、监督分数或内部记录。关系定义去重，情境对比保留；超预算的样例跳过。无 embedding 的离线 v2 测试可使用词汇相似度，正常训练/测试继续使用已有 encoder。

## 4. 提交、恢复与运行方法

输出目录包含：

```text
path_train_manifest.json    配置、训练顺序、数据摘要、初始记忆快照与 KG 标识
relation_memory.jsonl       可检索的用法与对比经验
relation_semantics.jsonl    带证据的关系解释
path_train_traces.jsonl     每次探索、评价和诊断
progress.jsonl             每题最后写入的提交标记与文件记录数
path_train_summary.json    首次/最终成功率、诊断计数、覆盖数与成本
```

题内新记录先进入 overlay，下一次尝试立即可读；题目提交后才合并进全局 bank。写完三种记录后最后写 progress。恢复时仅载入已提交记录，检查各文件记录数，移除未提交记录及截断尾部；已提交文件缺失时报告错误，禁止继续追加。配置、数据、初始记忆或输出路径变化时拒绝复用原目录。

`runtime_metrics.py` 用 context-local 计数覆盖主路径及辅助 KG 请求，不改变旧推理。calls/tokens 按现有 PoG 返回的逻辑调用与 token 统计。

旧训练、旧记忆和反思训练保留。已有 `run_PoG_train.sh` / `run_PoG_test.sh` 转发新参数；它们被仓库忽略，因此额外提供受版本管理的独立入口 `train_autonomous_path.sh`。

### 4.1 训练

环境需要已有 LLM 依赖、embedding 模型和可查询 Freebase。先配置 `OPENAI_API_KEY` 与 `OPENAI_API_BASE`，在 PoG 目录运行：

```bash
MEMORY_OUTPUT_DIR=memory/autonomous_pilot LIMIT=10 bash train_autonomous_path.sh
```

直接调用入口的核心参数：

```bash
python main_freebase.py \
  --run_mode train --split train --dataset webqsp \
  --train_memory_family relation_choice --relation_train_strategy autonomous_path \
  --relation_action_format directional --relation_semantics_mode on_demand \
  --max_path_attempts 3 --train_rollout_path_limit 50 \
  --reference_mode none --decomposition_memory_mode none \
  --reflection_memory_mode none --constraint_pushdown off \
  --memory_output_dir memory/autonomous_pilot --limit 10
```

直接调用还须按现有入口提供模型和 API 参数。恢复时使用相同数据切片、配置、seed memory 与 `memory_output_dir`。

### 4.2 测试

按正常测试入口运行，指定：

```text
--run_mode test
--relation_train_strategy gold_path
--relation_action_format directional
--relation_memory_mode prompt
--relation_memory_path memory/autonomous_pilot/relation_memory.jsonl
```

测试只加载该文件，无需读取监督轨迹或 relation_semantics 文件，无在线标注/写入。测试问题必须与训练问题隔离。

## 5. 自动化验证与实验验收

`test_autonomous_path_memory.py` 使用 mock LLM/KG，覆盖有向动作、非法输出、H 跳停止、字面值提前结束、CVT 跳数、分叉汇合、同名实体、循环、查询证据、sibling parses、答案集合、替代路径、候选召回失败、监督截断未知、重试即时读写、工作记忆清空、监督隔离、语义缓存、提交中断与恢复。

同时运行原反思与约束测试。约束测试中两个漏导入的已有测试函数补充局部 import，不改变业务断言。

```bash
python -B -m unittest discover -s . -p 'test_*.py'
```

真实小样本阶段先检查 10 道训练题的路径、方向、语义来源和重试差异，再进行全量训练与测试。离线测试通过不代表模型效果已验证。

正式实验比较：无记忆、旧 gold-path 样例、自主探索无语义、单次探索加语义、最多三次完整方案。统一方向修复、模型、候选预算和问题集合。报告训练首次与最终成功率、召回/实体筛选失败、关系与记忆覆盖、calls/tokens/KG requests；测试报告原评价指标与额外成本。

训练重试成功只说明监督反馈在训练题上有效，迁移结论必须来自冻结记忆后的独立测试集。
