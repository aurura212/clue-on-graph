# SP1：原 PoG 决策点适配与最小环境绑定

> 文档编号：SP1-PLAN  
> 版本：1.3  
> 初始制定日期：2026-08-22  
> 本次修订日期：2026-08-22  
> 状态：计划已完善，待实施  
> 当前阶段：SP1  
> 上位约束：`00_experiment_overall_requirements.md` v1.8  
> 前置必读：`00_experiment_overall_requirements.md`、`01_SP0_protocol_workspace_and_data_contract.md`、`reports/sp0/SP0_experiment_report.md`  
> 前置结论：SP0 PASS，协议 `sp-protocol-v1` 与固定评测集已经冻结

## 1. 本步骤定位与核心问题

SP1 位于协议冻结与首次在线 KG 实验之间。它负责把 SP0 已冻结的 `sp-protocol-v1` 接到 `self-play/` 下现有原 PoG 基线，建立可测试的状态适配层、动作应用边界和最小 Environment binding。

SP1 要回答的核心实验问题是：

> SP0 定义的 Self-Play 协议，能否在不改变原 PoG 基线行为、不调用真实 LLM、不依赖 live Freebase、不给 Actor/Critic 泄漏 Oracle 信息的情况下，准确、确定且可重放地表达 PoG 的搜索状态和基础动作？

SP1 不以提高 KGQA 准确率为目标，也不检验 memory 是否有效。SP1 的成功产物应是一个经过验证的 PoG 协议适配器，使后续 SP2-A 能够在同一动作和状态语义下首次接入 live KG。

## 2. 具体目标

1. 将原 PoG 中混合的 KG 查询、候选构造、LLM 决策、输出解析和状态更新拆分为清楚的接口边界。
2. 将原 PoG snapshot 完整投影为 `VisibleState`，并冻结字段来源、规范化规则和 `state_id` 生成规则。
3. 明确并测试 `EXPAND`、`SELECT_FRONTIER`、`CONTINUE`、`STOP`、`ABSTAIN` 的协议语义。
4. 明确原 PoG recovery 在 SP1 只对应 `SELECT_FRONTIER`；真正的 `BACKTRACK(state)` 暂不支持，不得伪装为已实现。
5. 定义 `Direction.HEAD`、`Direction.TAIL` 与 `entity_search(..., head=...)` 的无歧义映射，以及查询结果的规范三元组方向。
6. 定义自由文本或预制决策输出到合法 `STOP(answer_candidates)` 的答案提交契约，保证答案候选已经被当前状态观察到。
7. 建立独立的 KG、LLM、action step、depth、frontier 和 Critic 预算计数，不从原 PoG 的混合 `call_num` 直接推导。
8. 验证 adapter 默认关闭时原 PoG fixture 行为等价，而不只检查文件哈希。
9. 使用人工 fixture 和可选 recorded I/O 验证确定性 replay、异常分类和来源审计。
10. 使用冻结的 WebQSP smoke 20 做结构级 O0 泄漏检查，并把 WebQSP 20/150 与 CWQ 50 正式登记进 benchmark exclusion registry。
11. 保证所有新增代码、配置、测试和产物只写入 `self-play/`，`../data/` 与 `../cope_alias/` 继续只读。

## 3. 本步骤明确不做

- 不调用真实 LLM，不发送 Explorer、Critic 或 reasoning 请求；允许使用预制 LLM 文本检查离线解析。
- 不把 live Freebase 可用性作为验收条件，也不把 SP1 运行称为正式 live KG 实验；首次正式 live KG 实验属于 SP2-A。
- 不生成、蒸馏、promotion 或注入任何 Self-Play memory。
- 不运行 WebQSP 20/150 或 CWQ 50 的 KGQA 效果评测，不报告 memory 增强结论。
- 不用冻结评测题生成搜索轨迹、Critic 反馈、Oracle witness 或候选经验。
- 不生成正式规模 synthetic discovery 数据。
- 不修改 `../data/`、`../cope_alias/`，也不在其中生成索引、缓存或临时文件。
- 原则上不修改 `main_freebase.py`、`freebase_func.py`、`utils.py` 等原 PoG 基线文件。若确实必须修改，须先在本文件计划变更记录中列出文件、行级范围、理由、默认关闭方式、行为等价验证和回退方式。

## 4. 前置条件与冻结输入

实施 SP1 前必须先完成以下检查，任一失败时停止，不得自动修复、重抽或覆盖：

1. `00_experiment_overall_requirements.md` 当前阶段仍为 SP1，版本不低于 1.8。
2. SP0 报告结论仍为 PASS，协议版本仍为 `sp-protocol-v1`。
3. `eval_set_manifest_v1.json` 的 `manifest_hash` 为 `f6dd56a5b9a2937ad5e1964a25570a410e9be8720254551c78ca7f69e28226be`。
4. 三个固定评测文件及哈希保持不变。

| 固定数据文件 | 用途 | n | SP0 冻结 SHA-256 | SP1 允许用途 |
|---|---|---:|---|---|
| `artifacts/datasets/webqsp_smoke_20.jsonl` | 冒烟集 | 20 | `e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393` | 公共字段状态投影、O0 泄漏检查、exclusion 登记，不运行模型 |
| `artifacts/datasets/webqsp_model_compare_150.jsonl` | 正式模型对比集 | 150 | `37276867bb297991e83c335a6d4bb4f5657642fae2c77fb16eeac56eb310628c` | 只校验哈希和生成 exclusion 记录 |
| `artifacts/datasets/cwq_model_compare_50.jsonl` | 正式模型对比集 | 50 | `fa5f957de02ac804253d722fc1cc1a22652450a0480a1b5b4bd582ab4c5cb25b` | 只校验哈希和生成 exclusion 记录 |

当前 `benchmark_exclusion_registry_v1.json` 只有 2 条 SP0 fixture 记录，不是正式评测隔离名单。SP1 必须从以上三个冻结文件构造正式 registry，不能沿用 fixture 内容冒充完成。

## 5. 预期目录和产物

文件职责可在不改变协议和验收门槛的前提下适度拆分，但必须在日志中记录实际文件与计划文件的对应关系。预期产物如下：

```text
self-play/
├── src/sp_memory/
│   ├── pog_adapter.py
│   ├── environment_binding.py
│   ├── answer_submission.py
│   └── sp1_checks.py
├── configs/
│   └── sp1_adapter_v1.json
├── scripts/
│   └── run_sp1_checks.py
├── tests/
│   ├── test_pog_adapter.py
│   ├── test_environment_binding.py
│   ├── test_answer_submission.py
│   └── fixtures/sp1/
├── artifacts/
│   ├── protocol/pog_decision_map_v1.json
│   ├── recorded_io/sp1/
│   └── registries/benchmark_exclusion_registry_v1.json
├── runs/<sp1-run-id>/
└── reports/sp1/
    └── SP1_experiment_report.md
```

`artifacts/recorded_io/sp1/` 只在实际存在 recorded I/O 时创建，不要求为了满足目录形式而在 SP1 发起 live KG 请求。

## 6. 需要实现的代码功能

### 6.1 三层接口边界

SP1 必须把原 PoG 的复合函数拆解为以下逻辑边界：

1. **Environment 候选与查询边界**：枚举某一可见实体的关系候选，或执行已经验证合法的 `EXPAND`。该层不得调用 LLM。
2. **Actor 决策边界**：消费 ActorView 并产生协议 Action。SP1 不运行真实 Actor，只使用人工 Action 或预制输出验证接口。
3. **动作解析与应用边界**：校验 Action，转换为原 PoG 参数，应用 fixture/recorded 结果并产生新 `VisibleState`。非法动作必须拒绝，不能静默修复。

不得直接调用整个 `relation_search_prune` 或 `reasoning` 并同时声称 SP1 没有调用 LLM，因为这两个函数内部包含 `run_llm`。适配层应绑定它们的候选输入、输出解析或状态应用位置，而不是绑定整个复合调用。

### 6.2 决策点地图

| 协议阶段 | 原 PoG 相关位置 | SP1 绑定方式 | SP1 结论 |
|---|---|---|---|
| `RELATION_SELECTION` | `relation_search_prune` 中 head/tail relation 查询和 `select_relations` 解析 | 将 relation 枚举与 LLM 评分拆开；枚举结果转为 `VisibleRelation`；预制文本只用于离线解析测试 | 支持候选暴露和 `EXPAND` 动作，不调用整个函数的 LLM 路径 |
| `CONTINUE_STOP` | `reasoning`、`main_freebase.py` stop 标志 | 预制 reasoning 输出进入独立 parser，再映射为 `CONTINUE`、`STOP` 或解析失败 | 支持离线映射，不调用 `reasoning` 的真实 LLM 路径 |
| `ANSWER_SUBMISSION` | `extract_reason_and_anwer`、实体名称/ID 映射 | 将候选规范化为已观察实体 ID 或已观察 literal，再构造 `STOP` | 只有 observed candidate 才能提交 |
| `BACKTRACK_RECOVERY` | `if_finish_list`、`add_pre_info` | 历史实体重选只映射为 `SELECT_FRONTIER` | `BACKTRACK(state)` 在 SP1 不支持 |

决策点地图必须另存为版本化 JSON 产物，包含源文件、函数、职责、是否含 LLM、协议 stage、支持动作和不支持动作。

### 6.3 PoG snapshot 到 `VisibleState` 的投影契约

适配器输入应是显式的 PoG snapshot 对象，不得通过读取全局变量或隐藏文件补齐状态。snapshot 至少登记以下来源：

- `task_id`、`question` 和公开 source entity；
- `topic_entity` 或当前实体集合；
- `ent_rel_ent_dict`、`depth_ent_rel_ent_dict`；
- `cluster_chain_of_entities`；
- 当前 frontier、已失败或耗尽分支；
- 经过摘要的动作历史；
- adapter 独立预算计数器；
- 当前 `DecisionStage`。

字段映射必须满足：

| `VisibleState` 字段 | 构造规则 |
|---|---|
| `visible_entities` | source entity、已观察三元组两端和当前 frontier 的并集；去重后 canonical sort |
| `visible_relations` | 仅包含已经枚举并可供当前动作选择的 `(entity, relation, direction)`；去重后按三元 key 排序 |
| `observed_triples_or_summaries` | 只保留已经由 fixture/recorded 环境返回的规范三元组或公开摘要，不包含未来邻居 |
| `frontier` | 当前合法候选实体；去重并排序，不包括仅存在于 Oracle witness 的实体 |
| `failed_or_exhausted_branches` | 只记录已经实际尝试并失败或耗尽的分支标识 |
| `action_history_summary` | 保持动作时间顺序；每项使用稳定、无 Oracle 内容的摘要格式 |
| `remaining_budget` | 从独立 adapter counter 计算，不读取混合 `call_num` 作为唯一来源 |
| `decision_stage` | 由调用入口显式传入，不根据答案标签推断 |

### 6.4 规范化和 `state_id`

必须冻结以下 canonicalization 规则：

- entity、relation、frontier 和失败分支先去重，再按 Unicode code point 升序排列；
- 三元组统一为 `subject`、`relation`、`object` 三个字符串字段，并按三字段排序；
- `visible_relations` 按 `entity`、`relation`、`direction` 排序；
- 动作历史保持时间顺序，不做集合化；
- 不把运行时间、文件路径、token 文本、对象内存地址等非语义字段写入状态 hash；
- `state_id` 由不含 `state_id` 自身的 canonical state payload 和 `protocol_version` 计算；
- 相同语义 snapshot 即使输入容器顺序不同，也必须得到相同 `VisibleState` 和 `state_id`；任一语义字段变化必须改变 hash。

### 6.5 最小 Environment binding 与方向语义

SP1 定义纯接口 `EXPAND(entity, relation, direction)` 到原 PoG `entity_search(entity, relation, head=...)` 的参数和结果形状。方向描述的是当前实体在规范三元组中的位置：

| 协议方向 | 原 PoG 参数 | 查询含义 | 规范三元组 |
|---|---|---|---|
| `Direction.HEAD` | `head=True` | 当前实体作为 head/source，查询其 tail/object | `(entity, relation, returned_target)` |
| `Direction.TAIL` | `head=False` | 当前实体作为 tail/object，反向查询 head/source | `(returned_source, relation, entity)` |

Environment result 必须区分：

- 成功且返回一个或多个结果；
- 成功但结果为空；
- literal 结果；
- 重复结果；
- malformed response；
- timeout 或 endpoint/system error；
- adapter 输入或 schema 错误。

空结果是合法环境结果，不得伪装成系统错误；timeout 和 malformed response 也不得伪装成合法空结果。`[FINISH_ID]` 只能作为原 PoG 兼容标记处理，不能作为真实 KG 实体或答案候选进入状态。

### 6.6 `STOP` 与答案提交契约

SP1 必须实现独立 answer-submission adapter，输入为预制解析结果和当前 observed set，输出为合法 `STOP`、明确的解析失败，或由调用者决定的 `ABSTAIN`。规则如下：

1. 优先接受已经是 observed entity ID 的候选。
2. 实体名称只能通过当前 PoG snapshot 中可见的 `entid_name` / `name_entid` 映射到 observed ID；不得使用 benchmark 标准答案帮助映射。
3. literal 只有在已作为 KG 返回值出现在 observed set 时才能提交。
4. 多答案去重后使用稳定顺序提交。
5. 名称映射到多个 observed ID 时判为 ambiguous，不得任选一个。
6. 无法映射、候选为空或包含未观察答案时，返回 `FailureClass.ANSWER_EXTRACTION_FAILURE` 或触发既有 `UNOBSERVED_ANSWER`，不得构造合法 `STOP`。
7. Verifier 可以在独立 O4 view 中读取标签判分，但 Verifier 结果不得反向修改本次答案映射。

### 6.7 recovery 与 `BACKTRACK` 支持边界

`if_finish_list` 和 `add_pre_info` 只是在历史已见实体中选择实体并补回前驱信息，没有恢复任意历史状态 snapshot。因此 SP1 采用以下规则：

- 历史可见实体的重新选择映射为 `SELECT_FRONTIER(entity)`；
- `BACKTRACK(entity)` 不作为新的独立语义使用，避免与 `SELECT_FRONTIER` 重复；
- `BACKTRACK(state:<id>)` 标记为 unsupported；
- 调用 unsupported backtrack 时必须产生结构化结果：`failure_class=action_space_failure`、`error_code=UNSUPPORTED_BACKTRACK_STATE`；
- 不得更新状态、预算或动作历史来伪装回溯成功。

若后续需要真正支持 `BACKTRACK(state)`，必须在新的阶段计划中增加 checkpoint registry、完整状态恢复、预算是否恢复的规则和确定性测试，不能在 SP1 无记录扩展。

### 6.8 独立预算与计数

SP1 adapter 必须显式维护 SP0 `Budget` 中的六类计数：

| 预算 | SP1 更新规则 |
|---|---|
| `used_steps` | 每个通过合法性验证并实际应用的协议 Action 增加 1；被拒绝动作不增加 |
| `used_kg_calls` | 按 fixture/recorded I/O 所代表的实际逻辑 SPARQL 调用数增加；relation head/tail 枚举通常分别计数，`EXPAND` 按实际查询数计数 |
| `used_llm_calls` | SP1 始终为 0；预制文本解析不计 LLM call |
| `used_depth` | 成功扩展到下一 hop 时按协议规则增加；空 expansion 不增加 depth |
| `used_frontier_size` | 等于当前去重 frontier 的实际大小，不用累计调用数代替 |
| `used_critic_rounds` | SP1 始终为 0 |

所有 Environment 调用和 Action 应用都必须记录 before/after budget。达到上限时在执行前拒绝，并返回 `BUDGET_EXCEEDED` / `budget_insufficient`。原 PoG token 统计可作为附加成本字段保存，但不能代替协议预算。

### 6.9 结构化错误与失败分类

协议动作错误继续使用 SP0 `ProtocolError` 和 `ViolationCode`。Environment binding 另行返回结构化 `EnvironmentResult` 或等价对象，至少包含：

- `status`；
- `results`；
- `kg_call_delta`；
- `failure_class`；
- `error_code`；
- `message`；
- `provenance_ref`。

推荐映射：

| 情况 | 分类 |
|---|---|
| 不可见实体、关系或错误方向 | 既有 `ProtocolError` |
| unsupported `BACKTRACK(state)` | `action_space_failure` |
| 合法查询但空结果 | 成功，结果列表为空 |
| timeout、连接失败、malformed KG response | `system_failure` |
| 答案无法映射或提取 | `answer_extraction_failure` |
| 预算不足 | `budget_insufficient` |

不得出现未分类异常；未知异常必须先保存 traceback 和输入引用，再将 run 标为 FAILED 或 INVALID。

### 6.10 fixture 与 recorded I/O 证据契约

人工 fixture 与 recorded I/O 必须分开登记：

- **人工 fixture**：用于正常、边界、非法动作和异常注入测试；必须标记 `source_type=hand_fixture`。
- **recorded I/O**：若已有，则用于验证原 PoG 输入输出形状的真实性；必须标记 `source_type=recorded_pog_io`。

每条记录至少保存：record ID、来源函数、构造或录制时间、query 或参数、原始输出、规范化输出、原始内容 SHA-256、endpoint/snapshot 标识（如适用）、是否含 Oracle 字段、允许用途。含 Oracle 字段的记录不得进入 Actor/Critic fixture。

两类记录都只能作为 SP1 接口证据，不能作为 SP3 candidate experience 或正式 memory 证据。没有 recorded I/O 时，SP1 可以依靠人工 fixture 完成验收，但必须在风险清单中记录“尚未验证 live Freebase”，并由 SP2-A 补齐。

### 6.11 原 PoG 基线完整性与行为等价

优先通过 wrapper、显式依赖注入或测试 monkeypatch 接入，不修改原 PoG 基线文件。检查包括：

1. SP0 登记的基线文件 SHA-256 未发生非预注册变化。
2. `adapter_enabled=false` 时，在相同 fixture、相同函数参数和相同替代 KG/LLM 输出下，原路径与接入后的默认路径具有相同返回结构、结果顺序和状态变化。
3. 测试中把 `run_llm` 替换为一旦调用即失败的 guard，证明 SP1 adapter 路径没有隐式调用 LLM。
4. 如果必须修改基线文件，应同时保存变更前后 hash、差异清单、关闭 adapter 的等价结果和回退说明。

只证明文件 hash 不变不足以证明行为等价；行为等价是独立验收项。

### 6.12 O0 泄漏和正式 benchmark exclusion registry

O0 检查分两层：

1. 使用人工 fixture 注入顶层、嵌套和文本形式的答案 ID、答案名称、logical query、witness、future neighbors 等敏感内容，确认既有审计全部拒绝。
2. 对 WebQSP smoke 20 的每条记录，只用公开字段构造初始状态和 Actor/Critic view；Verifier 单独读取标签。20/20 的 Actor/Critic view 和渲染文本必须通过 O0 审计。

WebQSP 150 和 CWQ 50 不进入 Actor/Critic 投影流程，只用于 hash 校验和 exclusion 构建。正式 exclusion registry 要求：

- 从三个冻结文件生成 220 条记录，不重抽、不补题；
- 保存 dataset、原始 split/task ID、normalized question hash、topic entities、answer entities、exposure source 和时间；
- normalized question 的规范化算法和版本写入配置，registry 内容 hash 冻结；
- 后续 discovery/task generation 至少按 task ID 和 normalized question hash 拒绝重叠，topic entities 用于审计和分布分析；
- registry 含 Oracle 标签，只允许 exclusion/Verifier 工具读取，不得进入 Actor/Critic view。

`sp1-question-normalization-v1` 必须固定为以下算法，所有实现和重建过程不得自行简化或替换：

1. 对原始问题文本执行 Unicode NFKC 规范化；
2. 执行与 Python `str.casefold()` 等价的 Unicode 大小写折叠；
3. 将每一段连续 Unicode whitespace 替换为一个 ASCII 空格 `U+0020`；
4. 删除首尾空格；
5. 保留标点，不做去标点、词干化、分词或同义改写；
6. 对规范化结果的 UTF-8 字节计算 SHA-256，并保存算法版本和 hash。

当前 SP0 产生的 2 条 exclusion fixture 只用于接口、泄漏和 replay 测试，不计入正式 220 条 benchmark exclusion registry，也不得与正式记录合并为 222 条。它们应保留在 fixture/test 数据区域并标记 `record_scope=fixture_only`；正式 registry 必须仅由冻结的 WebQSP smoke 20、WebQSP compare 150 和 CWQ compare 50 重建，记录数严格等于 220。

`cwq_model_compare_50.jsonl` 是从 `data/cwq.json` 冻结的实验对比子集，其中 task ID 同时包含 `WebQTrn-*` 和 `WebQTest-*`。SP1 不重抽、不重新命名，也不得把它表述为标准 CWQ test benchmark；后续论文若需要标准 test split，必须另行预注册。

### 6.13 一键检查入口与 run 产物

`scripts/run_sp1_checks.py` 必须：

1. 先校验 overall、SP0 配置、manifest、固定数据和基线 hash；
2. 强制 `allow_llm=false`、`allow_live_kg=false`、`adapter_enabled` 按实验项显式设置；
3. 运行 E1.1-E1.12 和相关单元测试；
4. 为每次运行创建独立 `runs/<run_id>/`；
5. 保存配置副本、输入 hash、环境信息、检查结果、错误、指标和 manifest；
6. 失败运行不得覆盖已有成功 run；
7. 退出码 0 只表示全部强制检查达到门槛。

## 7. SP1 配置冻结

`configs/sp1_adapter_v1.json` 至少包含：

```json
{
  "plan_version": "SP1-PLAN 1.3",
  "protocol_version": "sp-protocol-v1",
  "overall_version": "SP-GENERAL 1.8",
  "adapter_enabled_default": false,
  "allow_llm": false,
  "allow_live_kg": false,
  "backtrack_state_policy": "unsupported",
  "direction_semantics": "current_entity_role",
  "answer_submission_requires_observed": true,
  "canonicalization_version": "sp1-canonical-v1",
  "question_normalization_version": "sp1-question-normalization-v1",
  "eval_manifest_hash": "f6dd56a5b9a2937ad5e1964a25570a410e9be8720254551c78ca7f69e28226be",
  "expected_exclusion_records": 220
}
```

实际配置还应登记 fixture/recorded I/O hash、SP0 budget 引用、基线 hash、失败分类版本和测试随机种子。配置中不得保存 API key、endpoint 密钥或完整 secret 环境变量。

## 8. SP1 检查实验

### E1.1 基线文件完整性与 adapter-disabled 行为等价

- 校验 SP0 登记的原 PoG 文件 hash。
- 在固定 fixture 上比较原路径与 `adapter_enabled=false` 路径的输入、返回、排序和状态变化。
- **通过条件**：非预注册基线 hash 变化数为 0；fixture 行为等价率 100%。

### E1.2 决策边界和零真实 LLM 调用

- 检查决策点地图是否覆盖 relation selection、continue/stop、answer submission 和 recovery。
- 用 fail-fast guard 替换 `run_llm`，运行全部 SP1 adapter 检查。
- **通过条件**：真实 LLM call 为 0；整个 `relation_search_prune` / `reasoning` 未被错误当成无 LLM Environment 接口调用。

### E1.3 PoG snapshot 到 `VisibleState` 的完整投影

- 构造覆盖多实体、多方向、多深度、空 frontier、失败分支和动作历史的 fixture。
- 逐字段对照投影结果和预期。
- **通过条件**：合法 fixture 投影成功率 100%；缺失必要字段和 schema 错误拒绝率 100%。

### E1.4 canonicalization 与 `state_id` 确定性

- 同一 snapshot 重复投影至少 3 次。
- 打乱 entity、relation、triple 和 frontier 输入顺序后重新投影。
- 分别修改一个语义字段并检查 hash。
- **通过条件**：同语义状态一致率 100%；语义变化 hash 变化率 100%。

### E1.5 O0 泄漏检查

- 运行人工敏感字段和值注入测试。
- 对 WebQSP smoke 20 逐条构造公共 Actor/Critic view 和独立 VerifierView。
- **通过条件**：泄漏注入检测率 100%；20/20 Actor/Critic 敏感字段数为 0；Verifier 20/20 可读取标签且标签不反向进入 O0。

### E1.6 relation candidate 与 `EXPAND` 双向映射

- 测试 HEAD、TAIL、空结果、重复实体、literal、未知实体/关系和 `[FINISH_ID]`。
- 检查协议 Action 到原参数以及环境结果到规范三元组的往返。
- **通过条件**：合法方向映射正确率 100%；方向反转为 0；非法或不可见动作拒绝率 100%。

### E1.7 continue/stop/abstain 与答案提交

- 使用预制解析文本覆盖 continue、单答案、多答案、literal、空答案、未观察答案、歧义名称和 malformed 输出。
- **通过条件**：observed candidate 的合法 STOP 构造率 100%；未观察或歧义 candidate 接受数为 0；解析失败均有结构化分类。

### E1.8 recovery 与 unsupported backtrack

- 测试历史可见实体重选到 `SELECT_FRONTIER`。
- 测试 `BACKTRACK(state:<id>)` 被明确拒绝。
- **通过条件**：`SELECT_FRONTIER` 合法映射率 100%；unsupported backtrack 误成功数为 0；拒绝后状态和预算不变。

### E1.9 budget/counter delta 与超限行为

- 对 relation 枚举、成功 EXPAND、空 EXPAND、拒绝动作、STOP 和系统异常分别验证 before/after budget。
- 对六类预算执行边界和超限测试。
- **通过条件**：预算 delta 正确率 100%；SP1 `used_llm_calls=0`、`used_critic_rounds=0`；超限动作执行数为 0。

### E1.10 Environment 正常、空结果和系统异常分类

- 注入正常结果、空结果、timeout、malformed response、schema mismatch 和未知异常。
- **通过条件**：空结果与系统错误区分率 100%；预期异常分类正确率 100%；未分类异常数为 0。

### E1.11 fixture/recorded I/O 来源审计与 replay

- 校验所有 fixture 和已有 recorded I/O 的来源字段与 SHA-256。
- 每个 fixture 重放至少 3 次，并检查 canonical output 和 state hash。
- **通过条件**：来源完整率 100%；同输入 replay 一致率 100%；Oracle fixture 进入 Actor/Critic 的次数为 0。

### E1.12 固定数据、正式 exclusion registry 与一键复现

- 校验 manifest 和 20/150/50 文件 hash。
- 建立固定 normalization regression vectors，至少覆盖 ASCII 大小写、全角字符、Unicode 组合/分解形式、`ß` 等 casefold 差异、tab/newline/NBSP 连续空白、首尾空白和标点保留；每个向量保存原文、预期规范化文本和预期 SHA-256。
- 按 `sp1-question-normalization-v1` 从三个冻结文件构造并验证严格 220 条正式 exclusion 记录；确认 2 条 SP0 fixture 未混入正式 registry；连续生成两次检查 content hash。
- 执行一次成功 run 和至少一个预期失败 fixture，验证失败不覆盖成功产物。
- **通过条件**：固定文件 hash 变化数为 0；normalization regression vectors 通过率 100%；registry 记录数严格为 220；重复生成内容 hash 一致；一键成功运行退出 0；预期失败运行非 0 且成功 run 完整。

## 9. 需要报告的指标

| 指标 | 目标 |
|---|---:|
| 非预注册基线文件 hash 变化数 | 0 |
| adapter-disabled fixture 行为等价率 | 100% |
| 真实 LLM 调用数 | 0 |
| SP1 正式 live KG 调用数 | 0 |
| 合法 snapshot 投影成功率 | 100% |
| 非法 snapshot 拒绝率 | 100% |
| 同语义状态/hash 一致率 | 100% |
| 语义变化 hash 变化率 | 100% |
| O0 泄漏检测率 | 100% |
| WebQSP smoke Actor/Critic 敏感字段数 | 0 |
| HEAD/TAIL 方向映射正确率 | 100% |
| 方向反转数 | 0 |
| observed STOP 构造正确率 | 100% |
| 未观察或歧义答案被接受数 | 0 |
| unsupported backtrack 误成功数 | 0 |
| budget delta 正确率 | 100% |
| Environment 失败分类正确率 | 100% |
| 未分类异常数 | 0 |
| fixture replay 一致率 | 100% |
| question normalization regression vectors 通过率 | 100% |
| exclusion registry 记录数 | 220 |
| exclusion registry 重建一致率 | 100% |
| 固定评测文件 hash 变化数 | 0 |
| `data/`、`cope_alias/` 登记文件 hash 变化数 | 0 |

## 10. SP1 验收门槛

SP1 判定 PASS 必须同时满足：

1. E1.1-E1.12 全部完成并达到通过条件。
2. 真实 LLM 调用数和 SP1 正式 live KG 调用数均为 0。
3. O0 泄漏检测率、动作映射正确率、预算 delta 正确率和 replay 一致率均为 100%。
4. `BACKTRACK(state)` 被明确标为 unsupported，不存在伪恢复成功。
5. 未观察答案不能通过 `STOP`；答案提交失败有明确分类。
6. adapter 默认关闭时原 PoG fixture 行为等价，基线文件没有非预注册变化。
7. 正式 benchmark exclusion registry 仅由三个冻结评测文件生成，严格覆盖 220 条记录，2 条 SP0 fixture 未计入，且 normalization/version/content hash 可复现。
8. SP0 manifest、20/150/50 文件以及只读输入 hash 不变。
9. 所有产物位于 `self-play/`，成功和失败 run 独立保存。
10. 日志区、指标汇总和风险清单完整，并已生成 `reports/sp1/SP1_experiment_report.md`；报告路径和 SHA-256 已登记。

出现以下任一情况必须 FAIL，不能 CONDITIONAL PASS：

- Oracle/test 标签进入 Actor/Critic 或 prompt；
- HEAD/TAIL 方向反转；
- 未观察答案被接受为合法 STOP；
- unsupported backtrack 被记录为成功状态恢复；
- adapter-disabled 改变原 PoG 基线行为；
- 固定评测数据被重抽、补题、覆盖或 hash 改变；
- 正式 exclusion registry 缺失或不能复现；
- 同输入 replay 不确定；
- `data/`、`cope_alias/` 或其他边界外目录发生新增写入；
- 关键异常没有分类或失败运行覆盖成功产物。

CONDITIONAL PASS 只允许用于不影响接口正确性、数据隔离和复现性的次要文档或可选 recorded I/O 缺口。没有 recorded I/O 本身不阻止 PASS，但必须明确由 SP2-A 完成 live KG 验证。

## 11. SP1 阶段收口与后续启动边界

SP1 实验工作结束后，无论最终结论为 PASS、CONDITIONAL PASS 还是 FAIL，都必须完成以下收口：

1. 在本文件日志区补全所有有效和无效运行、异常、指标、证据路径及最终验收；
2. 生成 `reports/sp1/SP1_experiment_report.md`，汇总研究目标、计划/协议版本、代码/配置/数据哈希、Run ID、实验设置、结果、失败分类、验收结论、未解决风险和产物索引；
3. 计算并登记实验报告 SHA-256；
4. 更新 overall 的 SP1 状态、报告路径、报告 hash、收口日期和阶段历史。

生成下一阶段实验计划不属于 SP1 的结束产物，也不是 SP1 PASS、报告生成或阶段收口的条件。SP1 完成收口后可以停留在阶段间状态；只有后续决定启动 SP2-A 时，才需按照 overall v1.8 更新新阶段的启动条件和允许工作，并在 SP2-A 代码实现或实验运行前制定、登记对应计划。

SP1 PASS 后仍不得直接调用 LLM、生成 memory 或运行 150/50 效果对比。首次后续实验仍应是 SP2-A：使用预制合法动作接入 live KG，验证真实查询、方向、异常和计数，且不调用 LLM。只有 SP2-A PASS 后，SP2-B 才能进行无 memory 的 LLM+KG rollout。

## 12. 实验日志区

> 从本节末尾按时间追加，不删除、不覆盖旧记录。每次计划变更、代码实现、测试、排错和验收判断均需单独记录。错误运行保留并标记 INVALID。

### 12.1 日志索引

| 日志 ID | 日期时间 | 类型 | Run ID/Commit | 状态 | 简述 |
|---|---|---|---|---|---|
| SP1-LOG-001 | 2026-08-22 | 计划变更 | N/A | SUCCESS | 根据 SP1 评估和 overall v1.7 将计划从 v1.0 完善为 v1.1，尚未实施代码或运行实验 |
| SP1-LOG-002 | 2026-08-22 | 计划变更 | N/A | SUCCESS | 将计划更新为 v1.2：冻结问题归一化算法，并明确 2 条 SP0 fixture 不计入正式 220 条 exclusion registry；未实施代码或运行实验 |
| SP1-LOG-003 | 2026-08-22 | 计划变更 | N/A | SUCCESS | 根据 overall v1.8 将计划更新为 v1.3：要求 SP1 结束后生成阶段报告，并取消生成下一阶段计划的强制要求；未实施代码或运行实验 |

### 12.2 单次实现或运行记录模板

#### [SP1-LOG-XXX] 标题

- **日期时间：**
- **类型：** 实现 / 测试 / 排错 / 计划变更 / 验收
- **计划版本：** SP1-PLAN 1.3
- **协议版本：** `sp-protocol-v1`
- **Run ID：**
- **Git commit / dirty：**
- **目标：**
- **实际命令：**
- **输入与 SHA-256：**
- **配置与 SHA-256：**
- **是否调用真实 LLM / live KG：**
- **代码或文件变化：**
- **关键结果与指标：**
- **异常及分类：**
- **产物路径：**
- **有效性：** VALID / INVALID
- **结论：**
- **下一行动：**

### 12.3 SP1 指标汇总表

| 指标 | 目标 | 实际 | 证据路径 | 结论 |
|---|---:|---:|---|---|
| E1.1-E1.12 通过率 | 100% | 待填写 | 待填写 | 待判断 |
| 真实 LLM 调用数 | 0 | 待填写 | 待填写 | 待判断 |
| SP1 正式 live KG 调用数 | 0 | 待填写 | 待填写 | 待判断 |
| adapter-disabled 行为等价率 | 100% | 待填写 | 待填写 | 待判断 |
| O0 泄漏检测率 | 100% | 待填写 | 待填写 | 待判断 |
| HEAD/TAIL 映射正确率 | 100% | 待填写 | 待填写 | 待判断 |
| STOP 非法候选接受数 | 0 | 待填写 | 待填写 | 待判断 |
| unsupported backtrack 误成功数 | 0 | 待填写 | 待填写 | 待判断 |
| budget delta 正确率 | 100% | 待填写 | 待填写 | 待判断 |
| replay 一致率 | 100% | 待填写 | 待填写 | 待判断 |
| question normalization regression vectors 通过率 | 100% | 待填写 | 待填写 | 待判断 |
| exclusion registry 记录数 | 220 | 待填写 | 待填写 | 待判断 |
| 固定数据 hash 变化数 | 0 | 待填写 | 待填写 | 待判断 |
| 未分类异常数 | 0 | 待填写 | 待填写 | 待判断 |

### 12.4 问题与风险清单

| 风险 ID | 风险 | 当前状态 | SP1 处理 | 后续责任阶段 |
|---|---|---|---|---|
| R1 | fixture replay 不能代表真实 Freebase | 已知 | SP1 只验证接口和确定性，不夸大证据 | SP2-A live KG |
| R2 | 原 PoG 决策函数混合 LLM 和 KG | 已知 | 拆分三层边界并使用 LLM fail-fast guard | SP1 |
| R3 | 原 PoG 没有真正 state backtrack | 已知 | SP1 只支持 `SELECT_FRONTIER`，state backtrack 明确 unsupported | 后续单独计划 |
| R4 | 自由文本答案到 observed ID 可能歧义 | 已知 | 独立答案提交契约，歧义时拒绝 | SP1、SP2-B |
| R5 | CWQ 50 含混合 `WebQTrn` / `WebQTest` ID | 已知 | 保持冻结并表述为实验对比子集，不称标准 test benchmark | 后续正式报告 |
| R6 | 当前 exclusion registry 只有 2 条 fixture，可能被误并入正式计数 | 待解决 | fixture 保留为 `fixture_only`；SP1 仅由三个冻结文件构造严格 220 条正式 registry，并冻结 normalization/version/content hash | SP1 |
| R7 | 工作树 dirty 影响复现 | 已知 | 每次 run 记录 commit、dirty 和输入 hash | 全阶段 |
| R8 | `pog_w.sh` 含 API key | 已知 | 不复制、不写入配置或 run 产物；SP1 不调用 LLM | 全阶段 |

### 12.5 计划变更记录

| 日期 | 版本 | 修改前 | 修改后 | 原因 | 对可比性的影响 |
|---|---|---|---|---|---|
| 2026-08-22 | 1.1 | v1.0 只定义四项检查；把 `relation_search_prune` / `reasoning` 直接视为绑定点；把原 PoG recovery 同时映射为 `BACKTRACK` / `SELECT_FRONTIER`；未定义 STOP、方向、预算确定性、异常和正式 exclusion | 拆分 Environment/Actor/动作应用边界；state backtrack 标记 unsupported；补充答案提交、HEAD/TAIL、独立预算、确定性、行为等价、来源审计、220 条 exclusion 和 E1.1-E1.12 | 根据 SP0 已完成状态、原 PoG 实际函数行为和 overall v1.7 的阶段顺序完善 SP1，使其能够作为可执行验收计划 | SP1 尚未实施，因此不改变已有实验结果；后续实现和验收统一以 v1.1 为准 |
| 2026-08-22 | 1.2 | v1.1 只登记归一化算法版本，未固定具体步骤；现有 2 条 fixture 与正式 registry 的计数边界不够明确 | 固定 NFKC、casefold、Unicode whitespace 折叠、trim、保留标点和 UTF-8 SHA-256 六步算法；fixture 标记为 `fixture_only`，正式 registry 严格为 220 条 | 防止不同实现产生不同 question hash，或误把 fixture 合并成 222 条正式记录 | 尚未实施实验，不影响已有结果；后续实现和验收统一以 v1.2 为准 |
| 2026-08-22 | 1.3 | v1.2 把生成下一阶段计划列为 SP1 PASS 后的强制动作，阶段报告路径和 hash 登记不够明确 | 将 `reports/sp1/SP1_experiment_report.md` 设为 SP1 必须收口产物；取消把下一阶段计划作为 SP1 验收或收口条件 | 与 overall v1.8 对齐，把实验结果沉淀和后续阶段规划解耦 | 尚未实施实验，不影响已有结果；后续实现和验收统一以 v1.3 为准 |

### 12.6 SP1 最终验收记录

- **验收日期：** 待填写
- **计划版本与 SHA-256：** 待填写
- **配置版本与 SHA-256：** 待填写
- **有效 Run ID：** 待填写
- **Git commit / dirty：** 待填写
- **E1.1-E1.12 结论：** 待填写
- **基线行为等价：** 待填写
- **O0 泄漏结论：** 待填写
- **固定数据与 exclusion registry hash：** 待填写
- **未解决风险：** 待填写
- **最终结论：** PASS / CONDITIONAL PASS / FAIL / 待判断
- **实验报告路径与 SHA-256：** 待填写
- **是否完成 SP1 阶段收口：** 是 / 否 / 待判断
