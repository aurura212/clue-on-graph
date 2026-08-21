# SP0：实验空间、协议与数据契约冻结计划

> 文档编号：SP0-PLAN  
> 版本：1.4  
> 制定日期：2026-08-21  
> 状态：待实施  
> 前置必读：00_experiment_overall_requirements.md

## 1. 本步骤定位

SP0 是 Self-Play 实验的第一步。本步骤不以提高 KGQA 准确率为目标，而是先建立后续实验可信所必需的基础：固定实验空间、输入数据契约、状态与动作协议、Oracle 权限边界、运行 manifest、泄漏审计和最小确定性重放能力。

SP0 通过之前，不得开始大规模合成任务生成、LLM rollout、Critic 纠错、经验蒸馏、memory 注入现有原 PoG 或 benchmark 效果实验。

## 2. 具体目标

1. 将 clue_on_graph/self-play/ 固定为本实验所有新增代码和产物的唯一写入空间。
2. 将 ../data/ 和 ../cope_alias/ 固定为只读输入，并建立可审计的数据文件 registry。
3. 定义任务、Oracle、可见状态、动作、动作结果、轨迹和运行 manifest 数据契约。
4. 建立 Actor/Critic 可见字段白名单和 Oracle 隐藏字段黑名单。
5. 实现合法动作检查、预算检查、协议错误分类和最小确定性 replay。
6. 建立 benchmark exposure/exclusion registry 的格式和校验入口；本步骤只验证结构，不生成正式 split。
7. 用不调用 LLM、不过问 benchmark 效果的协议实验验证以上功能。
8. 形成可供下一步在现有原 PoG 基线上开展 memory 实验使用的冻结协议版本 sp-protocol-v1。
9. 在 SP0 阶段一次性抽取并冻结 WebQSP/CWQ 的固定评测数据集，供后续冒烟测试和不同模型对比测试重复使用。

## 3. 本步骤明确不做

- 不生成正式规模的 synthetic discovery/validation/test 数据集；但必须实现并验证后续 WebQSP/CWQ 随机抽样配置，不在本步骤运行模型对比测试；
- 不调用 LLM 运行 Explorer 或 Critic；
- 不生成、蒸馏或 promotion 正式 memory；
- 不在本步骤实现或评估 Self-Play memory 对现有原 PoG 的增强效果；
- 不运行 KGQA 效果对比，也不读取 final 指标；
- 不修改 ../data/、../cope_alias/ 或 ../PoG/；
- 不把现有原 PoG 代码误记为 Self-Play memory 实现；本步骤只登记其基线入口、依赖和接口边界；
- 不根据测试结果调整未来研究假设。

## 4. 预期目录和产物

SP0 实施后至少产生：

~~~text
self-play/
├── exp_plan/
│   ├── 00_experiment_overall_requirements.md
│   └── 01_SP0_protocol_workspace_and_data_contract.md
├── src/sp_memory/
│   ├── __init__.py
│   ├── paths.py
│   ├── schemas.py
│   ├── visibility.py
│   ├── action_validator.py
│   ├── manifest.py
│   ├── replay.py
│   └── sampling.py
├── configs/sp0_protocol_v1.json
├── scripts/
│   ├── build_input_registry.py
│   ├── audit_workspace.py
│   ├── run_sp0_checks.py
│   └── sample_eval_sets.py
├── tests/
│   ├── fixtures/
│   ├── test_paths.py
│   ├── test_schemas.py
│   ├── test_visibility.py
│   ├── test_action_validator.py
│   └── test_replay.py
├── artifacts/
│   ├── manifests/
│   ├── registries/
│   └── protocol/
├── runs/
├── logs/
└── reports/
~~~

文件名可根据仓库现状小幅调整，但职责、可测试接口和全部产物位于 self-play/ 的要求不得改变。调整必须记录在本文日志区。

## 5. 需要实现的代码功能

### 5.1 原 PoG 基线登记与路径写入边界

self-play/ 根目录下已有代码就是当前实验使用的原 PoG 代码。SP0 不重写这部分代码，也不把它当作待重新接入的外部系统；需要完成的是基线清单和接口登记：

- 记录原 PoG 的主要入口、配置入口、KG 查询入口、答案解析入口和运行依赖；
- 记录后续 memory 计划作用于哪个决策点，以及采用适配层、检索模块还是 prompt/action score 接口；
- 运行 SP0 前后检查原 PoG 基线文件未被本实验的协议测试意外修改；
- 后续 memory 代码和产物仍统一放在 self-play/，并保留与原 PoG 基线的 commit、配置和调用关系。

### 5.2 路径与写入边界

实现统一路径模块 paths.py：

- 从代码文件位置解析 self-play/ 根目录，不依赖命令执行时的当前目录；
- 提供 data/、cope_alias/ 的只读输入路径；
- 提供 configs、artifacts、runs、logs、reports 输出路径；
- 对输出路径执行绝对路径解析和边界检查；
- 拒绝写入 self-play/ 之外的路径；
- 防止通过 ..、符号链接或大小写差异绕过边界；
- 测试使用临时目录，不污染正式 artifacts。

### 5.3 核心 Schema

在 schemas.py 中定义版本化、可序列化的数据结构。初始实现优先使用标准库 dataclasses、Enum 和显式校验；若引入新依赖，须记录必要性和版本。

#### TaskRecord

公开字段：task_id、question、source_entities、source_entity_names、task_split、task_generator_version、input_snapshot_id。

隐藏 Oracle 字段：logical_query、answer_entity_ids、normalized_answers、witness_paths、task_validity、oracle_version。

#### VisibleState

至少包含 task_id、question、visible_entities、visible_relations、observed_triples_or_summaries、frontier、failed_or_exhausted_branches、action_history_summary、remaining_budget、decision_stage。

VisibleState 禁止出现答案、gold path、gold SPARQL、未来邻居、counterfactual outcome 和隐藏 reward。

#### Action

初始动作类型：

~~~text
EXPAND(entity, relation, direction)
SELECT_FRONTIER(entity)
BACKTRACK(entity_or_state)
CONTINUE
STOP(answer_candidates)
ABSTAIN(reason_code)
~~~

每个动作包含 action_id、action_type、参数、来源角色、state_id 和 protocol_version。

#### StepOutcome

包含 accepted、protocol_violation、visible_result、new_frontier_items、budget_delta、state_id_before、state_id_after 和 deterministic_result_hash。Oracle 派生评价字段必须与 Actor 可见结果分区保存。

#### TrajectoryRecord

包含 trajectory_id、task_id、protocol_version、initial_state_hash、ordered_steps、terminal_submission、termination_reason、cost_summary 和 replay_hash。

#### RunManifest

包含 run_id、plan_version、protocol_version、git_commit、git_dirty、command、config_hash、input_files、seed、model_metadata、start_time、end_time、status 和 output_files。

所有结构必须支持 JSON round-trip，并在缺字段、未知版本、类型错误和非法枚举时明确失败。

### 5.4 可见性隔离

在 visibility.py 中实现单向投影：

~~~text
TaskRecord + EnvironmentState -> ActorView
TaskRecord + EnvironmentState -> CriticView
TaskRecord + EnvironmentState -> VerifierView
~~~

要求：

- ActorView 和在线 CriticView 默认对应 O0；
- O1-O3 只能通过显式、版本化的离线反馈对象提供，不能复用 O4 对象；
- VerifierView 可以读取判定所需 O4 字段；
- projection 后递归审计，禁止隐藏字段出现在嵌套字典、文本或 metadata；
- prompt 构造前再次审计，发现泄漏立即失败，不能只发 warning。

初始敏感字段注册表至少覆盖：answer_entity_ids、normalized_answers、witness_paths、gold_path、gold_sparql、logical_query、future_neighbors、hidden_reward 和 counterfactual_outcome。

### 5.5 动作合法性与预算

在 action_validator.py 中实现：

- EXPAND 的 entity、relation、direction 来自当前可见候选；
- SELECT_FRONTIER 和 BACKTRACK 目标已可见且合法；
- STOP 只能提交当前已观察的答案候选；
- ABSTAIN 使用预定义 reason code；
- 动作执行前检查 step、depth、KG call、LLM call、Critic round 和 frontier size；
- 非法动作返回结构化 violation code，不静默修正；
- 相同状态、动作、snapshot 和配置得到相同结果。

初始 violation code：UNKNOWN_ACTION、INVISIBLE_ENTITY、INVISIBLE_RELATION、INVALID_DIRECTION、INVALID_BACKTRACK_TARGET、UNOBSERVED_ANSWER、BUDGET_EXCEEDED、SCHEMA_VERSION_MISMATCH 和 ORACLE_LEAKAGE。

### 5.6 输入 Registry 与哈希

build_input_registry.py 只读取 ../data/ 和 ../cope_alias/，输出到：

~~~text
self-play/artifacts/registries/input_registry_v1.json
~~~

每个文件记录：相对路径、来源根目录、大小、修改时间、SHA-256、用途标签、是否包含 benchmark question/answer/alias，以及允许用于 discovery、validation、test 还是仅 exclusion。

纳入范围必须在配置中显式列出，不能依赖不稳定的目录遍历顺序。明显无关的大型文件可以不纳入，但排除规则要有记录。

### 5.7 Exposure/Exclusion Registry

定义并实现下列 registry 的结构与校验器：

~~~text
self-play/artifacts/registries/benchmark_exclusion_registry_v1.json
~~~

记录项至少支持 dataset、split、task ID、规范化问题哈希、topic entity、answer entity、暴露来源和时间。SP0 只使用 fixture 验证去重、冲突和序列化，不得宣称已完成正式 benchmark 隔离。

### 5.8 Run Manifest

在 manifest.py 中实现：

- 为每次检查生成唯一 run ID；
- 在 self-play/runs/<run_id>/ 保存冻结配置、manifest、stdout/stderr 摘要和结果；
- 计算配置与输出文件哈希；
- 记录 git commit 和 dirty status，但不修改或清理现有工作树；
- 异常退出时尽可能保存 FAILED manifest 和错误类型；
- 禁止保存 API key、token 或完整环境变量。

### 5.9 最小确定性 Replay

在 replay.py 中基于人工 fixture 实现最小状态机，不要求接入完整 Freebase：

- fixture 给出小型局部图、初始状态、可见候选和隐藏 Oracle；
- 执行一条合法成功轨迹并保存逐步哈希；
- 重放时重新验证动作可见性和预算；
- 相同输入连续 replay 的状态、outcome 和 replay hash 完全一致；
- 修改动作、snapshot 或预算后 hash 必须变化；
- 非法动作在同一步被拒绝；
- Verifier 可依据 Oracle 判定答案，ActorView/CriticView 不含 Oracle。

fixture 只用于协议测试，不能作为未来 memory 证据。

### 5.10 评测抽样配置与清单

实现 sampling.py 和 sample_eval_sets.py：

- 从 data/ 或 cope_alias/ 中登记的 WebQSP、CWQ 数据源分别进行一次性抽取；
- 使用固定随机种子、无放回简单随机抽样；seed 只用于首次构建固定数据集，不得在后续测试运行中重新抽样；
- 生成固定数据集文件和对应 manifest，保存题目 ID、数据集名称、抽样 seed、源文件哈希、样本用途、生成时间和固定数据集文件哈希；
- 生成固定冒烟测试数据集 `artifacts/datasets/webqsp_smoke_20.jsonl`，包含 20 条 WebQSP，且只用于冒烟测试；
- 生成固定 WebQSP 模型对比数据集 `artifacts/datasets/webqsp_model_compare_150.jsonl`，包含 150 条 WebQSP；
- 生成固定 CWQ 模型对比数据集 `artifacts/datasets/cwq_model_compare_50.jsonl`，包含 50 条 CWQ；
- 三个固定数据集分别抽取、分别冻结、分别生成 manifest、分别统计指标；
- 同一用途的所有模型、原 PoG 基线和 memory 对照必须读取同一个已冻结数据集，不得重新调用抽样器；
- 固定数据集和 manifest 只写入 self-play/artifacts/，不得写回 data/ 或 cope_alias/；
- 对重复题目、缺失题目、题目哈希冲突、固定数据集缺失、固定数据集哈希不匹配和源文件变化返回明确错误。

本步骤只负责一次性构建并验证固定数据集和 manifest 的一致性；实际模型测试在后续阶段计划中安排。后续阶段运行前必须校验固定数据集及 manifest 哈希，校验失败时停止运行，不得自动重新抽样、补题或覆盖原固定数据集。后续阶段不得用 20 条 WebQSP 冒烟数据集替代 150 条 WebQSP 或 50 条 CWQ 模型对比数据集，也不得把 WebQSP 与 CWQ 合并报告为单一指标。

本节是当前 SP0 阶段关于测试数据形成和复用的具体执行要求。后续阶段如果需要新的 validation 或 test 数据，必须在对应阶段计划文件中重新定义其一次性抽取和冻结规则；不得把本节的固定数据集自动扩展为后续阶段的新评测集。

### 5.11 一键检查入口

run_sp0_checks.py 应以一个命令完成：

1. 配置和路径检查；
2. input registry 构建或验证；
3. Schema round-trip；
4. 可见性和泄漏测试；
5. 合法/非法动作测试；
6. 确定性 replay 测试；
7. workspace 写入审计；
8. 输出机器可读 sp0_check_result.json 和人类可读摘要。

关键检查失败时返回非零退出码。

## 6. 配置冻结

configs/sp0_protocol_v1.json 至少包含：

~~~json
{
  "protocol_version": "sp-protocol-v1",
  "workspace_policy": {
    "write_root": "self-play",
    "read_only_input_roots": ["../data", "../cope_alias"]
  },
  "oracle_policy": {
    "online_actor_level": "O0",
    "online_critic_level": "O0",
    "verifier_level": "O4",
    "offline_feedback_levels": ["O1", "O2", "O3"]
  },
  "budgets": {
    "max_depth": 4,
    "max_steps": 12,
    "max_kg_calls": 16,
    "max_llm_calls": 8,
    "max_critic_rounds": 2,
    "max_frontier_size": 80
  }
}
~~~

这些预算在 SP0 只用于验证字段和边界行为，不代表已通过真实任务调优。后续调整必须在对应步骤预注册并升级配置版本。

## 7. SP0 检查实验

### E0.1 原 PoG 基线与 Workspace 写入隔离

**目的：** 确认 self-play/ 下现有原 PoG 代码作为基线被保留，同时验证新增输出只能落入 self-play/。

**补充做法：** 在实施前建立原 PoG 基线清单，记录入口文件、依赖、配置和哈希；协议检查不得覆盖这些文件。

**做法：** 对合法输出路径执行小文件写入；对 ../data/、../cope_alias/、../PoG/、绝对外部路径和含 .. 的路径尝试写入；比较共享输入实验前后哈希。

**通过条件：** 合法路径全部可用；越界路径 100% 被拒绝；共享输入哈希零变化。

### E0.2 Schema 与动作协议

**目的：** 验证稳定数据契约。

**做法：** 对每类 Schema 做 JSON round-trip；构造缺字段、危险字段、错误类型、未知版本和非法枚举；每种动作至少一个合法和一个非法样例。

**通过条件：** 合法样例 100% 通过；非法样例 100% 被拒绝且具有结构化 code。

### E0.3 Oracle 隔离与泄漏

**目的：** 证明 Actor/Critic 输入不含隐藏真值。

**做法：** 用完整 O4 fixture 构造各角色 view；递归扫描 O0 view 和模拟 prompt；将答案 ID、答案文本、witness、逻辑查询和未来邻居分别注入顶层、嵌套字段和 metadata；验证 O1-O3 只能经专门反馈类型进入。

**通过条件：** 所有注入泄漏被阻断；正常 O0 view 敏感字段为 0；VerifierView 可正常判分。

### E0.4 输入 Registry 可复现性

**目的：** 固定输入数据身份。

**做法：** 相同配置连续生成两次 registry 并比较排序、文件集合和 SHA-256；改变 fixture 文件验证差异可被发现；确认所有输出留在 self-play/。

**通过条件：** 未变输入生成相同内容哈希；改变输入被明确报告；无外部写入。

### E0.5 确定性 Replay

**目的：** 验证轨迹无需 LLM 即可复现。

**做法：** 对合法成功、合法失败、预算耗尽、非法 relation 和非法 backtrack 各运行 fixture；每个 fixture 至少重复 3 次；比较每一步 state hash、最终 outcome 和 replay hash；修改单一动作后验证变化。

**通过条件：** 同输入重复一致率 100%；非法动作定位一致率 100%；单因素修改被 hash 捕获。

### E0.6 评测抽样清单可复现性

**目的：** 验证 WebQSP/CWQ 固定评测数据集的一次性构建、样本规模、哈希冻结和跨模型共享规则。

**做法：**

- 首次运行抽样器，从已登记的 WebQSP 和 CWQ 源文件分别构建三个固定数据集；
- 生成 20 条 WebQSP 冒烟数据集、150 条 WebQSP 模型对比数据集和 50 条 CWQ 模型对比数据集；
- 保存三个数据集文件、题目 ID 顺序、seed、源文件哈希、数据集文件哈希和 manifest；
- 重复执行检查入口时只校验已有固定数据集，不得覆盖数据集或重新抽样；
- 模拟不同模型/基线读取固定数据集，确认它们读取的是同一文件哈希；
- 改变 seed、源文件哈希或固定数据集内容，确认校验失败并报告，不能自动生成替代数据集。

**通过条件：** 三个固定数据集的样本量准确；首次构建后重复检查不改变数据集文件；不同模型读取相同数据集文件和哈希；固定数据集或源文件变化可被发现并阻止运行；所有数据集和 manifest 只写入 self-play/。

### E0.7 一键复现与失败保存

**目的：** 验证完整检查可由单一入口重复执行。

**做法：** 在固定数据集已构建的前提下正常执行两次；启用一个固定数据集缺失或哈希不匹配的失败 fixture 再执行一次；检查退出码、manifest、结果 JSON 和错误；确认失败运行不覆盖成功运行，且失败时不会自动重新抽样。SP0 不运行 20/150/50 条题的模型效果测试。

**通过条件：** 成功返回 0；失败返回非 0；每次运行拥有独立完整的 run 目录和状态。

## 8. 需要报告的指标

SP0 不报告 KGQA EM/F1。至少报告：

| 指标 | 目标 |
|---|---:|
| 越界写入拒绝率 | 100% |
| 共享输入文件哈希变化数 | 0 |
| 合法 Schema/动作接受率 | 100% |
| 非法 Schema/动作拒绝率 | 100% |
| Oracle 泄漏检测率 | 100% |
| O0 view 敏感字段数 | 0 |
| 同输入 replay 一致率 | 100% |
| Registry 重建一致率 | 100% |
| 关键检查通过率 | 100% |
| 未分类异常数 | 0 |

同时记录测试数量和覆盖的 violation/leakage 类型，防止通过率来自样例过少。

## 9. SP0 验收门槛

只有同时满足以下条件，SP0 才可标记 PASS：

1. E0.1-E0.7 全部完成并达到通过条件；
2. ../data/ 和 ../cope_alias/ 实验前后内容哈希无变化；
3. 所有新增实验文件均位于 self-play/；
4. Actor/Critic O0 view 和模拟 prompt 中 Oracle 敏感字段为 0；
5. 同输入确定性 replay 一致率为 100%；
6. 非法动作和协议版本冲突均被明确拒绝；
7. 正常和失败运行均保存可审计 manifest；
8. 配置、Schema 和 registry 版本已冻结并有哈希；
9. 单元测试全部通过，不存在静默 skip 的关键测试；
10. 本文件日志区已记录实现、命令、产物、指标、问题和结论。
11. WebQSP/CWQ 抽样配置、seed、样本清单和源文件哈希已冻结并可复现。

Oracle 泄漏、越界写入、共享输入被修改或 replay 不确定时，SP0 必须判定 FAIL，不得使用 CONDITIONAL PASS。

## 10. SP0 完成后的下一阶段规则

SP0 通过后，尚不能直接开始下一阶段实现，也不能直接向现有原 PoG 注入 memory。必须先：

1. 在本文记录最终验收结论；
2. 新建下一步计划文件；
3. 在 00_experiment_overall_requirements.md 的步骤索引中登记该文件；
4. 审查下一阶段如何在现有原 PoG 基线上使用 KG、生成 synthetic tasks、保存隐藏 Oracle 并注入 memory；
5. 完成文档变更后再开始下一步。

## 11. 实验日志区

> 从本节末尾按时间追加，不删除、不覆盖旧记录。每次代码实现、测试、排错、协议变更和验收判断均需单独记录。无效运行保留并标记 INVALID。

### 11.1 日志索引

| 日志 ID | 日期时间 | 类型 | Run ID/Commit | 状态 | 简述 |
|---|---|---|---|---|---|
| 待填写 | 待填写 | 实现/测试/变更/验收 | 待填写 | 待填写 | 待填写 |

### 11.2 单次实现或运行记录模板

#### [SP0-LOG-XXX] 标题

- **日期时间：**
- **记录人：**
- **类型：** 实现 / 测试 / 排错 / 计划变更 / 验收
- **状态：** SUCCESS / FAILED / INVALID / PARTIAL
- **Run ID：**
- **Git commit 与 dirty status：**
- **对应计划版本：** SP0-PLAN 1.4
- **目标：**
- **已阅读：** 00_experiment_overall_requirements.md、01_SP0_protocol_workspace_and_data_contract.md
- **修改或新增文件：**
- **实际命令：**
- **输入文件与哈希：**
- **配置与哈希：**
- **输出产物与哈希：**
- **关键结果/指标：**
- **预期与实际差异：**
- **异常和失败样例：**
- **原因分析：**
- **采取的修复：**
- **是否需要修改计划：**
- **是否影响已有结果可比性：**
- **下一行动：**

### 11.3 SP0 指标汇总表

| 指标 | 目标 | 实际结果 | 证据文件 | 是否通过 |
|---|---:|---:|---|---|
| 越界写入拒绝率 | 100% | 待填写 | 待填写 | 待判断 |
| 共享输入文件哈希变化数 | 0 | 待填写 | 待填写 | 待判断 |
| 合法 Schema/动作接受率 | 100% | 待填写 | 待填写 | 待判断 |
| 非法 Schema/动作拒绝率 | 100% | 待填写 | 待填写 | 待判断 |
| Oracle 泄漏检测率 | 100% | 待填写 | 待填写 | 待判断 |
| O0 view 敏感字段数 | 0 | 待填写 | 待填写 | 待判断 |
| 同输入 replay 一致率 | 100% | 待填写 | 待填写 | 待判断 |
| Registry 重建一致率 | 100% | 待填写 | 待填写 | 待判断 |
| 关键检查通过率 | 100% | 待填写 | 待填写 | 待判断 |
| 未分类异常数 | 0 | 待填写 | 待填写 | 待判断 |
| WebQSP 冒烟样本量 | 20 | 待填写 | 待填写 | 待判断 |
| WebQSP 模型对比样本量 | 150 | 待填写 | 待填写 | 待判断 |
| CWQ 模型对比样本量 | 50 | 待填写 | 待填写 | 待判断 |
| 抽样清单重建一致率 | 100% | 待填写 | 待填写 | 待判断 |

### 11.4 问题与风险清单

| ID | 发现时间 | 问题/风险 | 严重性 | 当前状态 | 处理或接受理由 |
|---|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 高/中/低 | Open/Resolved/Accepted | 待填写 |

### 11.5 计划变更记录

| 日期 | 版本 | 变更前 | 变更后 | 原因与证据 | 可比性影响 |
|---|---|---|---|---|---|
| 2026-08-21 | 1.2 | 验收范围写为 E0.1-E0.6；抽样用途区分不够明确；模板仍引用 SP0-PLAN 1.0 | 将验收范围修正为 E0.1-E0.7；明确 20 条 WebQSP 仅用于冒烟测试，150 条 WebQSP 和 50 条 CWQ 分别用于模型对比；模板统一引用 SP0-PLAN 1.2 | 根据补充的评测抽样要求和新增 E0.7 一键复现实验修订计划 | 不改变已定义的实验目标；只澄清验收范围、样本用途和文档版本，不影响后续结果可比性 |
| 2026-08-21 | 1.3 | “固定清单”仍可能被理解为每次运行前重新随机抽样 | 明确 SP0 一次性生成三个固定数据集文件；后续运行只校验并读取固定文件和 manifest，校验失败时停止且不得自动补题或重抽 | 根据补充说明澄清测试数据的生成时机和后续复用方式 | 不改变样本规模和比较对象；提高跨运行可比性，后续运行必须使用新冻结数据集时需走计划变更流程 |
| 2026-08-21 | 1.4 | 测试数据形成要求同时分散在总体文件和 SP0 计划文件中 | 将具体抽取、固定数据集文件、manifest、哈希校验和复用要求集中放在本 SP0 计划的第 5.10 节；总体文件仅保留上位原则 | 根据用户补充说明调整文档职责边界 | 不改变测试数据规模和固定方式；降低后续执行时的文档歧义 |

### 11.6 SP0 最终验收记录

- **验收日期：** 待填写
- **计划版本：** SP0-PLAN 1.4
- **协议版本与哈希：** 待填写
- **代码 commit：** 待填写
- **有效 Run ID：** 待填写
- **E0.1-E0.7 结论：** 待填写
- **未解决问题：** 待填写
- **结论：** PASS / CONDITIONAL PASS / FAIL
- **结论依据：** 待填写
- **是否允许准备下一步计划：** 待判断
- **下一步计划文件名称：** 通过后先生成并登记，当前不预设
- **WebQSP/CWQ 抽样协议验收：** 待填写
- **是否已更新总体文件当前阶段：** 待填写
