# SP3：Self-Play 候选经验发现与生成

> 文档编号：SP3-PLAN  
> 版本：1.0  
> 制定日期：2026-08-22  
> 状态：已完成 PASS 并收口（2026-08-23）  
> 当前阶段：SP3 候选经验发现阶段  
> 实验根目录：`clue_on_graph/self-play/`  
> 上位约束：`00_experiment_overall_requirements.md` v1.17  
> 前置计划：`01_SP0_protocol_workspace_and_data_contract.md`、`02_SP1_pog_adapter_and_environment_binding.md`、`03_SP2A_live_kg_environment_validation.md`、`03A_SP2A_supplement_tail_and_dynamic_multihop.md`、`04_SP2B_llm_kg_baseline_rollout.md`  
> 前置报告：`reports/sp2a/SP2A_experiment_report.md`、`reports/sp2b/SP2B_experiment_report.md`  
> 后续步骤：SP4 反事实验证、蒸馏与 promotion  

## 1. 本阶段定位

SP3 是从“无 Self-Play Experience Memory 的 LLM+KG 基线”进入“生成候选经验”的第一阶段。它要回答的问题不是记忆是否已经提升最终准确率，而是：

> 能否在不让 Oracle 直接指导在线 Explorer/Critic 的条件下，通过 Self-Play 运行发现可复现、可解释、可去实体化、可供后续验证的搜索经验？

本阶段使用 `self-play/` 下现有原 PoG 代码作为推理基础，继续复用 SP1/SP2-A 的 adapter、live KG Environment、状态投影、动作校验、轨迹记录和 replay 机制。新增模块只作为原 PoG 决策过程外部的实验控制与候选经验记录层，不替换原 PoG。

SP3 产生的内容只能标记为 `candidate_experience`。候选经验在本阶段不得被 Explorer、Critic 或正式 PoG 推理检索和注入；只有完成 SP4 的反事实验证、蒸馏、held-out validation 和 promotion 后，才可能形成冻结的 `promoted_memory`。

## 2. 实验问题与具体目标

### 2.1 研究问题

1. **RQ-SP3-1：候选经验可构建性**  
   Explorer、Critic、Environment、Oracle 和 Verifier 能否在统一协议下形成完整、合法、可重放的 Self-Play 轨迹？

2. **RQ-SP3-2：Critic 是否能产生可记录的纠错经验**  
   对于提前停止、无效关系反复尝试、预算浪费和答案提交失败等问题，Critic 能否根据当前可见状态提出一个合法且可执行的下一步动作或受限诊断？

3. **RQ-SP3-3：经验是否具有可迁移的抽象形式**  
   一条经验能否被表示为不依赖具体答案实体、具体题目实体和完整 gold path 的“状态条件化搜索规则”？

4. **RQ-SP3-4：Oracle 是否能加速经验发现**  
   O1-O3 级别的离线反馈能否提高候选经验的发现速度或质量，同时保持在线 Explorer/Critic 不读取 Oracle？该问题只在离线 teacher 对照中回答，不能把 teacher 结果写成普通 O0 Self-Play 能力。

### 2.2 阶段目标

1. 建立独立的 SP3 discovery 数据集、任务 registry、exclusion registry 和 manifest，并在正式运行前冻结。
2. 实现或补齐 Explorer 多轨迹 rollout、O0 Critic 受限纠错、Oracle/Verifier 后台判定和候选经验提取功能。
3. 记录每次失败、停滞、纠错和成功恢复的完整 trace，区分系统错误与可经验化的推理失败。
4. 将候选经验转换为去答案化、去实体化、去未来状态的结构化记录。
5. 对候选经验进行 schema、合法动作、replay、Oracle 泄漏、数据污染和版本一致性检查。
6. 比较 Explorer-only、O0 Critic 和 Oracle-guided offline teacher 三种发现流程的候选产出与成本；不进行正式 benchmark 效果评测。
7. 为 SP4 提供一个可审计的候选经验池、失败样本池和反事实验证输入，而不是直接提供正式 memory。

## 3. 本阶段明确不做

- 不读取、检索或注入既有 `candidate_experience`、`promoted_memory` 或其他跨题 Self-Play memory。
- 不把 SP2-B 的成功或失败轨迹直接登记为经验。SP2-B 轨迹只能用于理解失败边界、设计任务分层和调试协议；候选经验必须由 SP3 discovery rollout 重新生成。
- 不使用冻结的 `webqsp_smoke_20.jsonl`、`webqsp_model_compare_150.jsonl` 或 `cwq_model_compare_50.jsonl` 生成候选经验、训练 Critic、调 prompt 或 promotion。
- 不启动 SP4 的最终反事实 promotion、held-out validation 或正式 memory 冻结流程。SP3 可以保存后续反事实所需的候选动作和状态，但不能据此提前 promotion。
- 不在本阶段声称 EM/F1 提升，不把 Verifier 的 O4 判定结果回流到在线 Actor/Critic。
- 不让 Oracle 提供答案、witness、gold path、未来邻居或正确关系给在线 Explorer/Critic。
- 不修改 `clue_on_graph/data/`、`clue_on_graph/cope_alias/` 或 self-play 根目录下原 PoG 基线文件；所有新增代码和产物必须写入 `self-play/`。
- 不通过无限增加 Critic 轮数、LLM 调用或搜索预算掩盖失败。所有预算必须预先冻结并记录。

## 4. 术语和角色边界

### 4.1 Explorer

Explorer 是运行时的 PoG Actor。它只读取问题、source entity、当前 `VisibleState`、合法候选动作、题内 `pog_working_memory` 和剩余预算。它不能读取答案、witness、gold path、Verifier 结果、counterfactual outcome 或历史任务经验。

### 4.2 Critic

Critic 不是答案 Oracle，也不自动拥有正确路线。它的职责是：在 Explorer 失败、停滞、重复无效动作、提前停止或预算风险出现时，基于当前可见状态诊断失败类型，并最多提出一个受限、可校验的恢复动作或停止建议。

在线 O0 Critic 必须只看当前公开状态。Critic 的建议必须经过 Action Validator，不能直接写入 KG。Critic 失败时仍须记录为 `critic_recovery_failure`，不得改写为系统错误。

### 4.3 Oracle

Oracle 仅在后台保存任务的隐藏答案、逻辑查询、witness 和必要的未来信息，用于任务构造、Verifier 判定和离线反馈生成。Oracle 不参与在线决策。

本阶段允许建立以下离线反馈，但必须作为独立对象保存并标注来源：

| 反馈等级 | 内容 | 允许用途 |
|---|---|---|
| O1 | 任务最终成功/失败 | 离线失败分类与候选筛选 |
| O2 | 不含答案的局部进展、停滞或失败类型 | 离线 Critic teacher 诊断 |
| O3 | 同一状态下候选动作结果比较 | 离线 teacher 标签和 SP4 输入准备 |
| O4 | 答案、witness、gold path、逻辑查询 | 仅任务构造、Verifier 和审计，不进入 Actor/Critic 或候选经验正文 |

### 4.4 Verifier

Verifier 对轨迹进行确定性 replay，检查动作是否合法、KG I/O 是否一致、终止是否符合协议、答案是否满足任务判定规则、候选经验是否泄漏 Oracle。Verifier 的判定可以使用 O4，但其输出只能写入后台审计结果。

## 5. 数据和任务冻结方案

### 5.1 Discovery 数据原则

SP3 必须使用独立 discovery 数据，不能从冻结 WebQSP/CWQ 评测集抽题。任务来源可以是 `data/` 中原始数据或 `cope_alias/` 中已有输入，但这两个目录只读；复制的派生任务、任务答案和 manifest 全部放入 `self-play/artifacts/`。

任务不要求由 LLM 临时编造问题。优先使用原始数据中的问题和 topic entity，再由确定性任务构造器生成或读取隐藏 Oracle。这样可以保证问题、答案和 witness 的来源可审计；如果需要生成合成问题，必须固定 generator 版本、随机种子、模板、逻辑查询和 KG 快照，并把生成规则写入 manifest。

### 5.2 建议的冻结数据分层

SP3 采用三个独立层级，所有题目在运行前一次性生成并固定：

| 层级 | 建议规模 | 用途 | 是否生成候选经验 |
|---|---:|---|---|
| SP3-D0 | 12 条 | 手工核查一跳、两跳、空结果、literal、提前停止和预算边界 | 允许生成，但只用于开发和协议检查 |
| SP3-D1 | 60 条 | 主要 discovery rollout，覆盖多种失败类型和 path signature | 允许生成候选经验 |
| SP3-H | 20 条 | held-out 仅用于检查候选经验是否可在未见任务上触发；只在 SP3 末尾运行 | 只做候选可用性观察，不 promotion |

以上规模是本计划的预注册目标。若源数据不足或任务有效性检查失败，必须记录缺口原因，不得用 benchmark 题目临时补齐。D0、D1、H 必须分别保存 JSONL、manifest、源文件哈希、任务生成器版本、随机种子和 exclusion registry。

### 5.3 任务答案和正确路线的确定

每个 discovery task 必须在公开任务记录之外保存 O4 oracle record，至少包含：

- 原始问题、topic entity 和数据来源；
- 固定的逻辑查询或可复现答案计算规则；
- normalized answer 和 answer entity ID；
- 至少一个 witness path 或确定性可验证条件；
- task validity、oracle version、KG snapshot/endpoint 标识。

Explorer/Critic 不读取这些字段。正确寻找过程不是由 Critic 自己宣布，而是由以下证据确定：

1. replay 时每个动作均满足当时的合法动作集合；
2. Environment 返回与记录的 KG I/O 一致；
3. 终止时提交值满足后台 Verifier 的答案规则，或按协议合法 ABSTAIN；
4. 对于需要路径解释的任务，witness 只用于后台判定和后续 SP4 反事实分析；
5. Critic 的主观“看起来正确”不能单独作为经验成立条件。

## 6. Self-Play 运行流程

### SP3.0 启动前检查

在任何 LLM 或 live KG 调用前完成：

1. 同时阅读本文件和 `00_experiment_overall_requirements.md` v1.17。
2. 检查 SP0、SP1、SP2-A、SP2-A 补充和 SP2-B 报告路径、哈希和收口状态。
3. 冻结 SP3 配置、prompt、模型标识、temperature、随机种子、预算、任务 registry 和 exclusion registry。
4. 检查 D0/D1/H 与 WebQSP 20/150、CWQ 50 以及既有曝光记录无题目、规范化问题和 topic entity 重叠。
5. 验证 Actor/Critic 的 O0 view 不包含 Oracle 敏感字段和值。
6. 运行离线单元测试、schema 测试、replay 测试、路径写入测试和 secret scan。
7. 记录 preflight 结果；任何数据污染、Oracle 泄漏、baseline 改动或不可重放问题都阻止进入 rollout。

### SP3.1 Explorer-only 基线轨迹

在 D0 上先运行 Explorer-only，配置与 SP2-B 尽可能一致：

1. 每题从初始可见状态开始，不读取跨题 memory。
2. Explorer 选择动作，Action Validator 验证后才允许 Environment 执行。
3. 保存每一步的 state、候选动作、选中动作、KG I/O、预算、题内工作记忆摘要和 LLM 请求/响应哈希。
4. 任务终止后由 Verifier 后台判定成功、失败或合法 ABSTAIN。
5. 将任务级失败分为 `invalid_task`、`action_space_failure`、`budget_insufficient`、`explorer_failure`、`answer_extraction_failure`、`system_failure`。

Explorer-only 是 SP3 的发现基线，不是新的正式 benchmark 基线；其作用是与加入 Critic 后的恢复结果配对比较。

### SP3.2 O0 online Critic Self-Play

在 D0 通过后运行 D1，流程为：

```text
固定 discovery 问题
  -> Explorer 选择并执行动作
  -> 发生失败、停滞、重复无效搜索或提前停止
  -> O0 Critic 读取当前 VisibleState
  -> Critic 输出失败类型 + 一个受限建议动作
  -> Action Validator 检查
  -> Environment 执行合法建议动作
  -> Explorer 继续或提交答案
  -> Verifier replay 与后台判定
  -> 提取 candidate_experience
```

每题 Critic 次数、额外 LLM 调用数和额外 KG/step 预算必须冻结。Critic 不得看到 O1-O4 反馈，不得读取 D0/D1 其他题目的轨迹或 candidate store。

Critic 重点覆盖以下错误：

- 一跳后对两跳问题提前 `STOP`；
- 在无结果或低价值关系上重复 `EXPAND`；
- 没有利用当前 frontier 继续探索；
- 在预算不足前未及时 `ABSTAIN`；
- 答案抽取失败时没有输出合法的 `STOP` 或 `ABSTAIN`；
- `BACKTRACK` 尚不支持时，是否能采用已登记的 PoG fallback，而不是发出非法动作。

### SP3.3 Oracle-guided offline teacher 对照

该步骤用于判断 Oracle 是否能加速候选经验发现，不是正式在线推理方案：

1. 对 D1 的 Explorer 轨迹由 Verifier 生成 O1/O2/O3 离线反馈。
2. 使用独立的 teacher/analysis 入口生成诊断和候选纠错动作。
3. teacher 可以看到标注等级，但不能把 O4 答案、witness、gold path 或未来邻居写入候选经验正文。
4. teacher 生成的候选必须经过同样的 schema、动作合法性、replay 和泄漏检查。
5. 统计 O0 Critic 与 teacher 的候选发现数量、有效率、恢复率、调用成本和发现所需时间。
6. 明确标注 `oracle_guided_offline_teacher`，不得将其结果报告为 O0 Self-Play 的性能。

如果资源有限，SP3.3 可以在 SP3.2 完成并稳定产出候选后运行；但必须在计划日志中记录是否执行以及原因。

## 7. 候选经验格式和生成规则

### 7.1 候选经验必须回答的问题

一条候选经验至少要表达：

> 在什么类型的可见状态下，当前动作或决策容易失败；应优先考虑哪类合法动作；为什么；适用边界是什么。

经验不是“题目 X 的答案是 Y”，也不是完整的题目路径缓存。

### 7.2 建议结构

候选经验建议保存为 JSONL，每条包含以下字段：

```json
{
  "experience_id": "sp3-candidate-...",
  "source_run_id": "...",
  "source_task_ids": ["..."],
  "discovery_method": "o0_critic|oracle_guided_offline_teacher",
  "trigger": {
    "question_type": "two_hop|empty_result|literal|...",
    "decision_stage": "relation_selection|continue_stop|backtrack_recovery|answer_submission",
    "state_signature": "entity_agnostic_signature",
    "failure_class": "..."
  },
  "recommendation": {
    "action_type": "EXPAND|SELECT_FRONTIER|CONTINUE|STOP|ABSTAIN",
    "direction": "head|tail|null",
    "relation_pattern": "abstract_pattern_or_null",
    "reason": "entity_and_answer_free_rule",
    "negative_constraints": ["..."],
    "budget_condition": "..."
  },
  "evidence": {
    "verified_replay": false,
    "observed_outcome": "...",
    "support_count": 1,
    "counterfactual_status": "deferred_to_sp4"
  },
  "privacy": {
    "answer_removed": true,
    "witness_removed": true,
    "entity_ids_removed": true,
    "gold_path_removed": true,
    "oracle_level": "O0|O1|O2|O3"
  },
  "versions": {
    "protocol_version": "...",
    "plan_version": "SP3-PLAN 1.0",
    "prompt_version": "...",
    "config_hash": "..."
  },
  "status": "candidate"
}
```

字段名可以根据现有 schema 调整，但不能删去来源、触发条件、推荐动作、证据、隐私审计和版本信息。

### 7.3 候选提取和过滤

候选经验从一条或多条已记录轨迹中提取，至少经过以下检查：

1. 经验来源 run 和 task 可追溯。
2. 触发状态属于 Explorer/Critic 当时真实可见的信息。
3. 推荐动作在对应状态的合法动作集合中，或明确标记为无效建议并不得进入候选池。
4. 经验文本和结构不包含答案、答案实体 ID、witness、gold path、未来邻居、完整题目路径和 Oracle 字段。
5. 经验可通过 recorded I/O replay；若尚未经过 SP4 反事实验证，必须标记 `counterfactual_status=deferred_to_sp4`。
6. 不能仅凭 Critic 自评“正确”通过候选过滤；必须有 Verifier trace、动作结果或明确的失败诊断证据。
7. 不能把单题具体实体名简单替换为空字符串后冒充抽象经验；应保留关系角色、状态条件、决策阶段和失败类型等可迁移信息。

## 8. 对照组与指标

### 8.1 必做对照

| 组别 | Explorer | Critic | Oracle 作用 | 用途 |
|---|---|---|---|---|
| G0 | SP2-B 同配置 | 无 | 仅后台 Verifier | 发现基线 |
| G1 | SP2-B 同配置 | O0 online Critic | 不可见 | 测试普通 Self-Play 纠错能力 |
| G2 | SP2-B 同配置 | 离线 teacher | O1-O3，仅离线 | 测试 Oracle 是否加速经验发现 |
| G3 | SP2-B 同配置 | 受限随机/无关建议 | 不可见 | 检查候选产出是否只是增加调用带来的假象 |

G2 的结果不能与 G1 合并为同一方法。G3 若实现成本过高，必须在日志中说明取消原因和对可比性的影响。

### 8.2 主要指标

- 轨迹完整率、合法动作率、终止率和 replay 率；
- Explorer-only 到 O0 Critic 的任务恢复率；
- 按失败类型统计的恢复率，重点关注 `explorer_failure`、`budget_insufficient`、`answer_extraction_failure` 和 `critic_recovery_failure`；
- 候选经验数量、去重后数量、来自不同任务的支持数和覆盖的决策阶段；
- 候选经验 schema 通过率、去泄漏通过率、可 replay 率；
- 平均每题 LLM 调用、KG 调用、步骤数、token 和运行时间；
- G1 与 G2 的候选发现速度、候选有效率和单位候选成本；
- 失败类型是否从系统失败转为可分类失败；
- 经验抽象失败率，例如仍包含实体、答案或完整路径的比例。

### 8.3 本阶段不作为结论的指标

WebQSP/CWQ 正式 EM/F1、memory promotion 后的泛化增益、最终模型优于原 PoG 的结论均不在 SP3 判定范围内。若 discovery task 使用后台 Oracle 得到 exact match，只能作为候选筛选和错误分析证据，不能写成 benchmark 准确率。

## 9. 实现要求

所有新增实现必须位于 `self-play/`。优先复用现有 SP2-B 代码，不复制一套新的 PoG：

1. 增加 SP3 配置读取和启动检查，冻结模型、prompt、预算、数据和 exclusion registry。
2. 增加 Explorer-only 与 O0 Critic 两种 rollout 模式，并在 trace 中记录角色、信息等级和每次纠错原因。
3. 增加离线 O1/O2/O3 feedback 生成入口；feedback 必须使用现有 `OfflineFeedback` 约束，禁止 O4 字段进入在线视图。
4. 增加 candidate experience schema、JSONL store、canonical hash、版本信息和追加写入机制。
5. 增加状态签名和去实体化处理；处理后仍需保留决策阶段、关系方向、失败类型、预算条件和 frontier 结构。
6. 增加候选经验 leakage audit、source trace audit、replay audit 和 exclusion audit。
7. 增加 G0/G1/G2/G3 指标汇总，分别输出任务级、失败类型级和候选经验级统计。
8. 增加单元测试：角色权限、O4 拒绝、候选字段完整性、答案/实体/gold path 清除、非法动作阻断、重复候选去重、断点续写和 replay 一致性。
9. 不修改 `main_freebase.py`、`freebase_func.py` 等原 PoG 基线；若确需适配，只能通过 `src/sp_memory/` 的包装层完成，并记录基线 inventory 变化。

## 10. 预期目录和产物

```text
self-play/
├── exp_plan/05_SP3_candidate_experience_discovery.md
├── configs/sp3_candidate_discovery_v1.json
├── prompts/sp3_explorer_v1.*
├── prompts/sp3_critic_o0_v1.*
├── prompts/sp3_critic_teacher_v1.*
├── artifacts/datasets/sp3_discovery_d0_12.jsonl
├── artifacts/datasets/sp3_discovery_d1_60.jsonl
├── artifacts/datasets/sp3_discovery_holdout_20.jsonl
├── artifacts/registries/sp3_discovery_registry_v1.json
├── artifacts/registries/sp3_exclusion_registry_v1.json
├── artifacts/registries/sp3_exposure_registry_v1.json
├── artifacts/feedback/sp3_o1_o2_o3_feedback_v1.jsonl
├── artifacts/candidates/sp3_candidate_experience_v1.jsonl
├── artifacts/candidates/sp3_candidate_rejection_log_v1.jsonl
├── runs/sp3-<timestamp>-<suffix>/
├── artifacts/protocol/sp3_check_result.json
└── reports/sp3/SP3_experiment_report.md
```

SP3 生成的 candidate store 必须与 SP2-B 的 `pog_working_memory` 分开。`pog_working_memory` 仍然只能按 task_id 和 run_id 隔离，用于当前题目；candidate store 是跨题的实验产物，但本阶段只写不读。

## 11. 验收门槛和阶段结论

### 11.1 关键阻断条件

出现以下任一情况，不得把 SP3 判为 PASS，也不得进入 SP4：

- discovery 题目与 WebQSP/CWQ 冻结评测集或既有曝光题目重叠；
- 在线 Explorer/Critic 读取 O4 或任何答案/witness/gold path；
- candidate experience 中残留答案、实体 ID、完整 gold path 或未来状态；
- 轨迹无法 replay，或 KG 请求绕过统一 Environment；
- `data/`、`cope_alias/` 或原 PoG 基线被写入/修改且无法解释；
- 出现未分类 system failure，或关键配置、数据、prompt 无法复现；
- 将 G2 teacher 的结果冒充 G1 O0 Self-Play 结果。

### 11.2 建议通过标准

SP3 PASS 需要同时满足：

1. SP3.0 preflight 全部通过，D0/D1/H 和 exclusion registry 已冻结。
2. D0 轨迹完整率、合法动作率和 replay 率达到 100%；发现的失败必须有分类。
3. D1 的有效 rollout 至少 95% 可 replay，未分类异常为 0；若低于 100%，每个例外必须有明确分类和原始证据。
4. G1 至少产生一批来自不同任务的 O0 Critic 候选，建议不少于 10 条、来自不少于 5 个 task，并覆盖至少 2 个决策阶段或失败类型；不足时只能 CONDITIONAL PASS。
5. 所有进入 candidate store 的记录 schema 通过率和泄漏审计通过率为 100%。
6. 至少记录 G0 与 G1 的配对恢复结果，以及 G1 与 G2 的发现成本/产出比较；不要求 G1 在 SP3 就证明最终准确率提升。
7. SP3 结束后生成 `reports/sp3/SP3_experiment_report.md`，补全本计划日志，并在 overall 的阶段历史中登记报告和哈希。

### 11.3 结论解释

- **PASS**：基础设施、隔离、replay 和候选经验生成均达标，可进入 SP4。
- **CONDITIONAL PASS**：基础设施有效，但候选数量或多样性不足；可以修复 SP3 发现流程，但不得直接进入 promotion。
- **FAIL**：出现泄漏、污染、不可 replay、未分类系统错误或关键产物缺失；必须修复并生成新有效 Run。

## 12. 实验日志（运行后追加）

本节只追加，不覆盖失败记录。每次实现或运行至少记录：

- 日期、Run ID、计划版本、overall 版本、Git commit 和 dirty status；
- D0/D1/H registry、exclusion registry、输入文件和哈希；
- 模型、provider、temperature、prompt 版本、配置哈希、随机种子和预算；
- 角色及 Oracle level：Explorer/Critic 必须为 O0，teacher 使用 O1-O3 时单独登记；
- 每题逐步 trace、state_id、决策阶段、动作、KG I/O、LLM 请求/响应摘要和 replay 结果；
- 失败分类、Critic 诊断、纠错动作、是否恢复和 Verifier 后台判定；
- candidate experience 的来源、字段审计、去实体化结果、泄漏扫描、重复合并和拒绝原因；
- LLM/KG 调用数、token、步骤、运行时间和单位候选成本；
- `data/`、`cope_alias/`、原 PoG 基线、secret 和 test-label 写入审计；
- 阶段结论：PASS、CONDITIONAL PASS、FAIL 或 INVALID。

### 12.1 运行日志占位

#### LOG-SP3-001 — 2026-08-22 — 代码与 discovery 冻结（无 LLM/KG）

- 日期：2026-08-22
- Run ID：无 live run。冻结产物见 `artifacts/datasets/sp3_discovery_manifest_v1.json`
- 计划版本：SP3-PLAN 1.0
- overall 版本：SP-GENERAL 1.17
- Git commit / dirty status：`75d660f61701da82ff554254209745c8834f6c7f` dirty（SP3 代码与冻结产物尚未提交）
- D0/D1/H registry 与 hash：
  - D0 n=12 `artifacts/datasets/sp3_discovery_d0_12.jsonl` SHA-256 `c1b17f94bd4cda68c615b5888c759387952d12becd5e8cb1a676adfe3a95f1e8`（`empty_result|literal|one_hop|two_hop`）
  - D1 n=60 `artifacts/datasets/sp3_discovery_d1_60.jsonl` SHA-256 `c77261ebd7c49028b11bf91ad39d5015cd96002a4d98103df958c4cd27c08dde`（`one_hop|two_hop`）
  - H n=20 `artifacts/datasets/sp3_discovery_holdout_20.jsonl` SHA-256 `fb250702d3ad47429af80112793de4258c02ce5dd8b5f32de4fbf860c06a7d84`（`one_hop|two_hop`）
  - registry `artifacts/registries/sp3_discovery_registry_v1.json` SHA-256 `ec395434a5c6d780fc500fbbbb41b6f317e31f3e7af870d284134636af330c80`
  - manifest hash `5ecd9719b781d3aa2bcae4eef9d6f7d5cb654ed88b9dc2a03576a48aa017381f`
- exclusion registry 与 hash：`artifacts/registries/sp3_exclusion_registry_v1.json` SHA-256 `dc7fe94274fc6f5f2fc98f7966bd17719841b83ad0d56a7da8167969edcb85db`；exposure `e89daa8134499ba17b97b4dd04db72c672e87380e7e9b2454108fff2f7503605`。未从冻结 WebQSP 20/150 或 CWQ 50 抽样；coverage_gaps 为空
- 配置 / prompt / 模型：`configs/sp3_candidate_discovery_v1.json` SHA-256 `20cc5dbe0a0cf00f5f388d7d856f5fe52e7add030eef93c17f9e60606e7ba720`；模型 `gpt-3.5-turbo-0125`；seed `20260822`；预算 steps 28 / kg 88 / llm 44 / critic_rounds 2；prompt `sp3_explorer_v1` / `sp3_critic_o0_v1` / `sp3_critic_teacher_v1`
- Oracle level 与角色权限审计：Actor/Critic 强制 O0；teacher 仅 O1-O3；`allow_candidate_injection=false`；`allow_self_play_experience_memory_read=false`；candidate store 只写不读。离线单测 14/14，全套 89/89
- D0 结果：未跑 LLM/KG
- D1 G0 结果：未跑
- D1 G1 O0 Critic 结果：未跑
- D1 G2 offline teacher 结果：未跑。G2 标注为 `oracle_guided_offline_teacher`，不得与 G1 合并
- G3 结果（如执行）：代码已实现为随机合法动作、不额外调用 Critic LLM；仍需完整 Explorer rollout。预算不足时可用 `--skip-g3`，须在后续 live 日志说明
- candidate 数量、支持任务数、失败类型覆盖：尚未生成
- replay / schema / leakage 结果：离线 schema/O4/去实体/去重/read-guard 通过；live replay 待跑
- `data/`、`cope_alias/` 和 baseline 写入审计：未改原 PoG 基线；新增仅在 `self-play/`
- 有效/无效 Run：本条为 code + freeze，不是 live discovery 有效 Run
- 阶段结论：尚未验收。允许用户启动 preflight 与 discovery；不得进入 SP4，不得注入候选经验，不得声称 EM/F1
- 证据路径：`scripts/freeze_sp3_discovery.py`、`scripts/run_sp3_discovery.py`、`src/sp_memory/sp3_*.py`、`tests/test_sp3_offline.py`、上述冻结数据集与 registry
- 备注：JSONL 仍含 Oracle 字段供 Verifier/teacher 后台使用；在线 Actor 必须走 `public_task_view`。adapter stage 仍为 `sp2b` 包装，不新增原 PoG 决策路径

#### LOG-SP3-002 — 2026-08-22 — freeze 校验与 preflight PASS（无 LLM/KG）

- 日期：2026-08-22
- Run ID：`sp3-20260822T144305Z-bb131774`（preflight_only）
- 计划版本：SP3-PLAN 1.0
- overall 版本：SP-GENERAL 1.17
- Git commit / dirty status：`75d660f61701da82ff554254209745c8834f6c7f` dirty
- D0/D1/H registry 与 hash：与 LOG-SP3-001 相同，只校验不重抽；manifest `5ecd9719b781d3aa2bcae4eef9d6f7d5cb654ed88b9dc2a03576a48aa017381f`
- exclusion registry 与 hash：同 LOG-SP3-001
- 配置 / prompt / 模型：config SHA-256 `20cc5dbe0a0cf00f5f388d7d856f5fe52e7add030eef93c17f9e60606e7ba720`；未调用真实 LLM
- Oracle level 与角色权限审计：candidate_injection=false；oracle_level_actor=O0
- D0/D1/G2/G3/holdout：未跑 live
- candidate 数量：0
- replay / schema / leakage 结果：单元测试 89/89；preflight PASS
- `data/`、`cope_alias/` 和 baseline 写入审计：无改动
- 有效/无效 Run：有效 preflight run；不是 live discovery 有效 Run
- 阶段结论：尚未验收。允许启动 D0；不得并行启动 D1/G1/G2/G3/holdout
- 证据路径：`runs/sp3-20260822T144305Z-bb131774/`、`artifacts/protocol/sp3_check_result.json`
- 备注：freeze 与 preflight 并行启动；二者均在 1s 内结束

#### LOG-SP3-003 — 2026-08-22 — D0 Explorer-only live PASS

- 日期：2026-08-22
- Run ID：`sp3-20260822T145537Z-0c4deb09`
- 计划版本：SP3-PLAN 1.0
- overall 版本：SP-GENERAL 1.17
- Git commit / dirty status：`75d660f61701da82ff554254209745c8834f6c7f` dirty
- D0/D1/H registry 与 hash：沿用 LOG-SP3-001 冻结产物，只校验不重抽
- exclusion registry 与 hash：同 LOG-SP3-001
- 配置 / prompt / 模型：config SHA-256 `20cc5dbe0a0cf00f5f388d7d856f5fe52e7add030eef93c17f9e60606e7ba720`；`gpt-3.5-turbo-0125`；endpoint `http://localhost:8890/sparql`；`--layer d0 --skip-tests`
- Oracle level 与角色权限审计：Explorer O0；无 Critic；candidate_injection=false；memory read=false
- D0 结果：n=12；轨迹完整率 1.0；replay 1.0；未分类 0；pipeline_ok 1.0；mean LLM=5.0。失败分类：`answer_extraction_failure` 5、`budget_insufficient` 2、`none` 5（STOP_SUBMITTED）。不报 EM/F1
- D1 G0/G1/G2/G3/holdout：未跑
- candidate 数量、支持任务数、失败类型覆盖：D0 未提取候选（Explorer-only 协议检查）
- replay / schema / leakage 结果：D0 replay 12/12；secret_hits=[]；baseline 未改
- `data/`、`cope_alias/` 和 baseline 写入审计：无改动
- 有效/无效 Run：有效 D0 run。首次启动因环境无 API key 失败（exit 2），本 run 为随后成功启动
- 阶段结论：D0 门控通过，允许进入 D1。不是 SP3 阶段 PASS
- 证据路径：`runs/sp3-20260822T145537Z-0c4deb09/`
- 备注：start `2026-08-22T14:55:37Z`，end `2026-08-22T14:57:25Z`

#### LOG-SP3-004 — 2026-08-22/23 — D1 G0–G3、holdout 与阶段 PASS

- 日期：2026-08-22（D1/H 运行）至 2026-08-23（报告收口）
- Run ID：D1 `sp3-20260822T160827Z-941c23fe`；holdout `sp3-20260822T164407Z-ce2cf6e6`。D0 仍为 LOG-SP3-003 的 `sp3-20260822T145537Z-0c4deb09`
- 计划版本：SP3-PLAN 1.0
- overall 版本：运行 SP-GENERAL 1.17；收口 1.18
- Git commit / dirty status：`75d660f61701da82ff554254209745c8834f6c7f` dirty
- D0/D1/H registry 与 hash：沿用 LOG-SP3-001，只校验不重抽
- exclusion registry 与 hash：同 LOG-SP3-001
- 配置 / prompt / 模型：config `20cc5dbe…`；`gpt-3.5-turbo-0125`
- Oracle level 与角色权限审计：Explorer/Critic O0；G2 teacher O1–O3 标注 `oracle_guided_offline_teacher`；injection=false；memory read=false
- D0 结果：见 LOG-SP3-003
- D1 G0 结果：n=60；replay 1.0；未分类 0；LLM 336；失败 answer_extraction 32 / budget 1 / none 27
- D1 G1 O0 Critic 结果：n=60；replay 1.0；未分类 0；LLM 410；候选 24/24 task；配对恢复 2/33。system_failure 31 为 Critic 16k 上下文 SCHEMA_ERROR，已分类可 replay
- D1 G2 offline teacher 结果：LLM 60；接受 38 / 拒绝 22；不得与 G1 合并
- G3 结果：n=60；replay 1.0；LLM 425；随机候选 57。已执行，未 skip
- holdout：n=20；replay 1.0；LLM 108；promotion=false；trigger_rate 1.0 仅为题型观察
- candidate 数量、支持任务数、失败类型覆盖：119 条；G1 24 / G2 38 / G3 57；阶段 relation_selection + continue_stop + answer_submission
- replay / schema / leakage 结果：D1/H replay 100%；schema/leakage 1.0；secret 0
- `data/`、`cope_alias/` 和 baseline 写入审计：无改动
- 有效/无效 Run：上述三个 live run 有效。首次 D0 缺 key 失败不计入
- 阶段结论：**PASS**。报告 `reports/sp3/SP3_experiment_report.md` SHA-256 `c30f54dad9d37f099c3faddac5377087400eb6287ee2c25831fec19d921bc650`
- 证据路径：对应 runs 与 candidate/feedback JSONL
- 备注：不进入 SP4；不报 EM/F1；不是 V2-5

## 13. 计划变更记录

| 日期 | 版本 | 修改内容 | 原因 | 对可比性的影响 |
|---|---|---|---|---|
| 2026-08-22 | 1.0 | 新增 SP3 候选经验发现计划；明确 D0/D1/H discovery 数据、Explorer/Critic/Oracle/Verifier 权限、G0-G3 对照、候选经验结构、隔离规则和验收门槛 | SP2-B 已 PASS 并收口，需要在正式 memory 使用前先独立生成和审计候选经验 | 不改变 SP0-SP2-B 历史结果；不使用 WebQSP 20/150 或 CWQ 50 生成经验；不提前进行 SP4 promotion |

