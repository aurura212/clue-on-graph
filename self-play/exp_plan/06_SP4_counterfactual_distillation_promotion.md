# SP4：候选经验反事实验证、蒸馏与 Promotion

> 文档编号：SP4-PLAN  
> 版本：1.0  
> 制定日期：2026-08-23  
> 状态：历史版本，已被 SP4-PLAN 2.0 替代，不作为当前执行计划  
> 当前阶段：SP4 候选经验验证与受控晋升  
> 实验根目录：`clue_on_graph/self-play/`  
> 上位约束：`00_experiment_overall_requirements.md` v1.19  
> 前置计划：`01_SP0_protocol_workspace_and_data_contract.md`、`02_SP1_pog_adapter_and_environment_binding.md`、`03_SP2A_live_kg_environment_validation.md`、`03A_SP2A_supplement_tail_and_dynamic_multihop.md`、`04_SP2B_llm_kg_baseline_rollout.md`、`05_SP3_candidate_experience_discovery.md`  
> 前置报告：`reports/sp3/SP3_experiment_report.md`  
> 后续阶段：SP5 冻结 memory 的正式效果评测

> **版本说明：** 当前执行计划已切换为 `06_SP4_precondition_counterfactual_distillation_promotion_v2.md`。本文件保留 v1 计划和原始登记记录，不应单独据此启动实验。

## 1. 阶段定位

SP4 是从“候选经验只写不读”进入“经验是否值得被正式读取”的验证阶段。SP3 已生成 119 条候选经验，但这些候选仍然可能只是记录格式正确、能够重放，未必能改善 PoG 的动作选择。因此，SP4 不以候选数量为成功标准，也不直接把全部候选写入 Memory，而是逐条检验候选经验在相同推理状态下是否真正带来收益，并在通过审计和门槛后形成冻结的 `promoted_memory`。

本阶段仍以 `self-play/` 下现有原 PoG 代码为推理基础。Self-Play 经验只通过受控的候选读取、规则匹配和动作建议接口影响原 PoG 的决策；不得替换原 PoG 的 KG 查询、动作合法性检查、Verifier 或题内工作记忆。

SP4 的逻辑链为：

~~~text
SP3 candidate experience
    -> 候选重新审计
    -> 同状态反事实比较
    -> 独立 validation
    -> 经验蒸馏
    -> promotion 判定
    -> 冻结 promoted_memory
~~~

SP4 完成后，只有通过 promotion 的规则才能作为 SP5 的正式 memory 输入。SP4 本身不是最终 WebQSP/CWQ benchmark 效果实验，不得据 SP4 结果宣称 EM/F1 已提升。

## 2. 研究问题与具体目标

### 2.1 研究问题

1. **RQ-SP4-1：候选经验是否具有真实局部价值？**  
   在 question、VisibleState、frontier、历史摘要和剩余预算均相同的情况下，候选经验建议的动作是否比原始动作或随机合法替代动作更好？

2. **RQ-SP4-2：候选经验是否会造成负迁移？**  
   候选经验是否会选择非法关系、提前停止、增加无效 KG 查询、破坏答案提交，或在适用条件不满足时误导 Explorer？

3. **RQ-SP4-3：候选经验能否去实体化并跨任务复用？**  
   通过验证的候选能否被蒸馏为不包含题目实体、答案实体、实体 ID、witness、gold path、future state 或 O4 信息的抽象规则，并在独立 validation 任务上触发和发挥作用？

4. **RQ-SP4-4：不同来源的候选质量是否不同？**  
   `o0_critic`、`oracle_guided_offline_teacher` 和 `random_critic` 必须分层统计。G2 是 Oracle 引导的离线 teacher 对照，不能解释为 O0 Self-Play；G3 是随机干预负对照，不能与真实 Critic 候选合并。

5. **RQ-SP4-5：蒸馏是否减少记忆噪声？**  
   与原始候选相比，蒸馏后的规则是否在保持有效收益的同时降低触发错误、重复规则和检索成本？

### 2.2 阶段目标

1. 对 SP3 的 119 条候选进行 schema、source trace、privacy、Oracle leakage 和 replay 重新审计。
2. 建立固定的候选验证输入和独立 validation 数据集，避免用正式 benchmark 结果调参或挑选经验。
3. 实现同状态反事实比较，区分候选动作的 `win`、`tie`、`harm` 和 `invalid`。
4. 评估候选在独立任务上的触发率、适用条件、迁移收益、伤害率和搜索成本变化。
5. 按决策阶段、抽象状态、失败类型、动作模式和适用条件进行经验蒸馏，删除所有不可迁移或可能泄漏的信息。
6. 根据预注册门槛决定哪些规则进入 `promoted_memory`，哪些保留为 `validated_candidate`，哪些标记为 `rejected_harmful` 或拒绝。
7. 输出一份可重放、可审计、版本冻结的 SP4 报告，为 SP5 的正式对照实验提供只读输入。

## 3. 明确不做的事情

- 不把 SP3 的 119 条候选全部直接写入正式 Memory。
- 不在 SP4 使用 `webqsp_smoke_20.jsonl`、`webqsp_model_compare_150.jsonl` 或 `cwq_model_compare_50.jsonl` 生成候选、蒸馏规则、调 prompt、调检索阈值、调 promotion 门槛或选择最终报告中的有利结果。
- 不把 G2 的离线 Oracle teacher 结果写成普通 O0 Critic 的能力；不把 G3 随机候选当作正向证据。
- 不读取答案、gold path、witness、future state 或 O4 信息来生成在线动作建议。
- 不在 SP4 进行最终大规模 EM/F1 benchmark 对比；最终正式效果比较属于 SP5。
- 不修改 `clue_on_graph/data/`、`clue_on_graph/cope_alias/` 或 self-play 根目录下原 PoG 基线文件。新增适配、验证和 memory 代码必须位于 `self-play/`。
- 不根据 validation 结果反复改写候选、蒸馏器、prompt 或阈值而不保留版本和失败记录。
- 不让 promoted rule 覆盖原 PoG 的动作合法性检查、KG 返回、Verifier 或最终答案判定。

## 4. 输入、数据隔离与来源分层

### 4.1 SP3 输入

SP4 只读取以下已冻结的 SP3 产物，并在启动前校验路径、大小和 SHA-256：

~~~text
self-play/artifacts/candidates/sp3_candidate_experience_v1.jsonl
self-play/artifacts/feedback/sp3_o1_o2_o3_feedback_v1.jsonl
self-play/reports/sp3/SP3_experiment_report.md
self-play/reports/sp3/metrics.json
~~~

SP3 候选总数为 119 条，但数量不是质量指标。候选来源必须保持如下标签：

~~~text
o0_critic                         # G1，在线 O0 Critic
oracle_guided_offline_teacher    # G2，离线 O1-O3 teacher
random_critic                    # G3，随机合法动作负对照
~~~

### 4.2 SP4 数据集

SP4 需要在任何 validation 运行前建立并冻结以下数据：

1. `SP4-CF`：SP3 D1 中所有通过初步 replay 审计、且具有候选动作的可比较状态。它用于同状态反事实，不是独立泛化集。
2. `SP4-V1`：40 条独立 validation 任务，用于验证候选规则是否能在未参与 SP3 discovery 的任务上触发并改善局部决策。
3. `SP4-V2`：20 条独立规则组合与负迁移检查任务，用于检查多规则同时存在、冲突规则、错误适用条件和无关候选检索。

V1/V2 必须在运行前生成固定 JSONL、registry、manifest 和 SHA-256。它们必须排除：

- SP3 D0、D1、holdout 中已经曝光的 task/question；
- `webqsp_smoke_20.jsonl`、`webqsp_model_compare_150.jsonl` 和 `cwq_model_compare_50.jsonl`；
- 任何由 SP3 候选选择、反事实结果或 validation 结果反向挑选的任务。

V1/V2 只用于 SP4 验证和 promotion，不用于 SP5 最终 benchmark 报告。若实际可用任务不足，必须在计划日志中记录缺口，不得静默补题或改变样本规模。

### 4.3 验证条件

同一候选、同一状态至少比较以下条件：

~~~text
CF0  原始 Explorer 动作：不读取候选经验
CF1  候选经验推荐动作：读取当前候选，但不读取答案或 Oracle
CF2  随机合法替代动作：在同一状态随机选择合法候选动作
CF3  无关/打乱候选：读取格式相同但语义不适用的候选
~~~

V1/V2 至少比较以下条件：

~~~text
V0  原 PoG 无 Self-Play Experience Memory 基线
V1  读取通过初步审计的原始候选规则
V2  读取蒸馏规则草案
V3  读取 G3 随机来源规则的成本匹配对照
V4  sham 对照：关闭候选检索，但保留相同的 prompt/token/预算占位
~~~

所有条件必须固定模型、模型版本、temperature、prompt 版本、KG endpoint、动作预算、超时和随机种子。每条结果必须记录候选来源，不能只记录合并后的结果。

## 5. 实现要求

### 5.1 候选审计器

实现 SP4 专用审计入口，至少支持：

- 读取并校验 SP3 candidate JSONL，每行失败不应导致整批静默丢失；
- 校验 schema、source trace、discovery task、decision stage、state signature、action pattern 和 applicability condition；
- 检查答案、实体 ID、witness、gold path、future state、O4 字段和 secret 泄漏；
- 检查候选动作在对应 VisibleState 中是否存在、合法、可执行；
- 使用 recorded KG I/O 和 LLM cache 做确定性 replay；
- 输出 `eligible`、`rejected_schema`、`rejected_leakage`、`rejected_unreplayable`、`deferred` 五类结果，并保留拒绝原因。

审计器必须保留 G1/G2/G3 的来源，不得通过一个统一的“候选质量分数”掩盖来源差异。

### 5.2 候选读取与动作干预器

实现只位于 `self-play/` 内的候选读取层，并满足：

1. 读取器只能接收 `public_task_view`、当前状态签名、候选动作摘要和冻结的候选库；
2. 读取器不得访问答案、gold path、witness、future state、Verifier 标签或 O4 字段；
3. 读取器只能返回候选建议及其 provenance，不得直接执行 KG 查询或绕过原 PoG action validator；
4. 候选建议不适用时必须返回空结果或显式 `no_match`，不能强制动作；
5. 每次注入都记录 candidate id、source group、match reason、injection point、retrieval score、token cost 和最终动作；
6. 关闭候选读取时，原 PoG 的输出、预算和动作接口必须保持可比。

SP4 仅验证候选动作建议，不允许在推理中在线更新 promoted memory。所有写入均进入临时验证产物，promotion 后才生成只读 memory manifest。

### 5.3 同状态反事实执行器

反事实执行器必须从同一条可重放的 SP3 状态快照分叉，固定 question、VisibleState、frontier、历史摘要、剩余预算、模型、KG 和 replay 输入。除候选干预条件外，CF0–CF3 不得共享会改变结果的随机状态。

每个分支至少记录：

- 动作是否合法、是否被 validator 接受；
- KG 请求、返回摘要、KG calls 和失败类型；
- Verifier 判定、是否完成当前局部目标、是否到达终止；
- steps、LLM calls、tokens、wall time 和剩余预算；
- 与其他分支相比的 `win`、`tie`、`harm` 或 `invalid`；
- recorded replay 是否与 live 结果一致。

SP3 中 31 条 G1 Critic 上下文超长的 `SCHEMA_ERROR` 必须单独保留为系统失败，不能被反事实结果改写成“候选有效”。

### 5.4 蒸馏器

蒸馏器按以下字段聚类和归并候选：

- `decision_stage`；
- 抽象状态签名和可见 frontier 特征；
- `failure_class`；
- action pattern、relation direction 和 stop/continue 模式；
- applicability condition；
- negative constraint；
- counterfactual evidence、support、harm rate 和 provenance。

蒸馏后的规则必须删除或泛化：题目实体、答案实体、实体 ID、witness、gold path、future state、具体答案字符串、O4 结果和只对单题成立的常量。蒸馏器必须输出规则的版本、来源候选列表、支持 task 数、正负证据和适用边界。

## 6. 实验步骤

### SP4.0 启动前检查与冻结

1. 校验 SP3 候选、feedback、报告、metrics、配置和 registry 的 hash。
2. 重新执行 schema、source trace、privacy、Oracle leakage、replay 和 baseline write-boundary 审计。
3. 冻结 SP4 配置、prompt、模型、temperature、调用预算、检索阈值、随机种子和报告字段。
4. 构建并冻结 `SP4-CF`、`SP4-V1`、`SP4-V2` 的数据、registry 和 manifest。
5. 完成候选读取器、动作干预器、反事实执行器和蒸馏器的单元测试。
6. 在没有真实 LLM/KG 运行的情况下完成 candidate injection audit、secret scan 和 write boundary audit。

SP4.0 未通过前，不得运行候选反事实或 validation。

### SP4.1 候选重新审计与分层统计

对 119 条候选逐条审计并生成分类产物。统计至少包括：总数、各来源数量、各 decision stage、各 failure class、可重放率、合法动作率、泄漏拒绝率和拒绝原因。不得把候选数量或 G3 数量较多解释为经验质量更高。

### SP4.2 同状态反事实验证

对所有 eligible 候选执行 CF0–CF3。优先使用 recorded replay 完成全量审计，再对预注册的 live subset 做必要复核。候选只有在相同状态下相对 CF0/CF2 有明确收益、且没有明显 harm/invalid 时，才可进入下一步 validation。

SP4.2 的核心输出不是“候选是否能触发”，而是“候选建议的动作是否在同一状态下带来更好的可验证结果”。至少报告：

- `win_rate`、`tie_rate`、`harm_rate`、`invalid_rate`；
- 相对 CF0 和 CF2 的成功/恢复变化；
- steps、LLM/KG calls、tokens 和时间成本变化；
- G1、G2、G3 分层结果；
- 按 decision stage 和 failure class 的分层结果。

### SP4.3 独立 validation

在 V1/V2 冻结后，比较 V0–V4。V1 用通过初步审计的原始候选，V2 用蒸馏规则草案。每条任务记录是否触发规则、触发的规则版本、局部动作结果、最终答案判定、搜索成本和负迁移类型。

SP4.3 只用于验证跨任务可迁移性和 promotion，不用于不断调参。若规则在 V1/V2 上表现不稳定，应标记为 `deferred` 或 `rejected_harmful`，不得通过删除失败任务改善统计。

### SP4.4 经验蒸馏

仅对通过 schema/privacy/leakage/replay 审计且具有反事实证据的候选进行蒸馏。蒸馏结果至少包括：规则正文、适用条件、禁止条件、来源候选、支持 task 数、CF 统计、validation 统计、版本和哈希。

原始候选不因蒸馏成功而自动 promotion；蒸馏规则仍需接受 V1/V2、负迁移和来源对照检查。

### SP4.5 Promotion 判定

Promotion 采用以下预注册门槛，所有条件均需满足：

1. schema、privacy、leakage 和 replay 通过率为 100%；
2. 至少来自 3 个独立 discovery task；
3. CF 至少有 5 个有效可比较状态；
4. 相对预先指定基线满足 `win_rate - harm_rate >= 0.20`；
5. `harm_rate <= 0.10`，且 `invalid_rate` 不得高于基线；
6. 在 V1 上至少触发 5 个任务；
7. V1 成功率不低于 V0，或成功率基本持平时平均搜索成本至少下降 10%；
8. 收益不能由单一题目、单一实体或单一异常样本主导；
9. G3 随机来源规则和 sham 对照不能产生同等或更高的收益；
10. 规则不依赖 O4、答案、gold path、witness 或 future state；
11. promotion 配置、检索阈值、注入位置、规则版本和 memory manifest 已冻结。

未达到 promotion 但具备基础证据的规则标记为 `validated_candidate`；出现稳定负迁移的规则标记为 `rejected_harmful`；证据不足的规则标记为 `deferred`。任何规则均不得静默丢弃。

### SP4.6 阶段收口

1. 完成 SP4 计划末尾实验日志、指标、异常、有效/无效 Run 和验收结论。
2. 生成 `self-play/reports/sp4/SP4_experiment_report.md` 和相应 `metrics.json`。
3. 固定候选验证、反事实结果、蒸馏规则、promotion decisions 和 memory manifest 的 SHA-256。
4. 只有 SP4 PASS 且 `promoted_memory` 已冻结时，才允许登记并启动 SP5；SP4 FAIL 或 CONDITIONAL PASS 时不得进行正式 benchmark memory 对比。

## 7. 验收门槛

SP4 不能仅因代码能够运行而 PASS。至少满足以下条件：

| 编号 | 验收要求 | 门槛 |
|---|---|---|
| S4.1 | SP4 配置、prompt、数据、registry 和 manifest 冻结 | 全部可追溯且 hash 可核验 |
| S4.2 | 候选重新审计 | 每条候选有明确状态和原因 |
| S4.3 | 反事实可重放 | 有效 CF 状态 replay 率 100% |
| S4.4 | 反事实结果完整 | 每个候选至少有合法性、Verifier、成本和 win/tie/harm/invalid |
| S4.5 | 来源分层 | G1/G2/G3 单独统计，G2 标记 offline teacher，G3 作为负对照 |
| S4.6 | 独立 validation | V1=40、V2=20 固定且无 SP3/正式 benchmark 重叠 |
| S4.7 | 隐私与 Oracle 隔离 | promoted rule 不含答案、实体 ID、witness、gold path、future state 或 O4 信息 |
| S4.8 | Promotion 选择性 | 只有满足全部 promotion 门槛的规则进入 promoted_memory |
| S4.9 | 负迁移报告 | 报告 harm、invalid、无触发和成本退化案例 |
| S4.10 | 阶段收口 | 计划日志、SP4 报告、metrics、manifest 和哈希齐全 |

若 S4.1–S4.7 或 S4.10 不满足，阶段不得 PASS。S4.8 不要求必须有大量 promoted rule；没有规则通过也可以得出“当前候选不足以晋升”的有效结论，但此时只能进入 FAIL/CONDITIONAL PASS 复盘，不得启动 SP5 的 memory 增益实验。

## 8. 预期代码功能与产物

### 8.1 代码功能

新增或补齐的实现必须位于 `self-play/`，推荐按现有代码结构复用本地 helper。至少需要：

- candidate audit / privacy / leakage / source trace checker；
- candidate retrieval 与 injection boundary；
- same-state counterfactual branch runner；
- legal random action 和 sham candidate 对照；
- candidate distiller；
- promotion evaluator 与 immutable memory manifest writer；
- replay、hash、run metadata、secret scan 和 write-boundary audit；
- 针对 119 条候选、冲突规则、空匹配、非法动作和 O4 泄漏的单元测试。

不得通过修改原 PoG 基线文件来隐藏 memory 逻辑；adapter 只负责把候选建议交给原有决策接口，并记录可审计的干预信息。

### 8.2 预期产物

~~~text
self-play/configs/sp4_counterfactual_promotion_v1.json
self-play/prompts/sp4_candidate_intervention_v1.*
self-play/prompts/sp4_distiller_v1.*
self-play/artifacts/datasets/sp4_validation_v1_40.jsonl
self-play/artifacts/datasets/sp4_validation_v2_20.jsonl
self-play/artifacts/registries/sp4_validation_registry_v1.json
self-play/artifacts/registries/sp4_exclusion_registry_v1.json
self-play/artifacts/counterfactual/sp4_counterfactual_results_v1.jsonl
self-play/artifacts/candidates/sp4_validated_candidate_v1.jsonl
self-play/artifacts/candidates/sp4_rejected_candidate_v1.jsonl
self-play/artifacts/memory/promoted_memory_v1.jsonl
self-play/artifacts/memory/promotion_decisions_v1.jsonl
self-play/artifacts/memory/memory_manifest_v1.json
self-play/artifacts/protocol/sp4_check_result.json
self-play/reports/sp4/SP4_experiment_report.md
self-play/reports/sp4/metrics.json
~~~

若某一产物因实验失败未生成，必须在日志和报告中明确标记 `NOT_GENERATED` 及原因，不得伪造空成功文件。

## 9. 实验日志

以下区域只追加，不覆盖已有记录。每次运行前登记计划版本、overall 版本、代码/配置/prompt/数据哈希、模型、KG endpoint、seed、预算、memory 读取状态和预期步骤；每次运行后登记 Run ID、退出状态、有效性、关键指标、异常分类、证据路径和下一步判断。

### LOG-SP4-001 — 启动登记

- 日期：2026-08-23
- 计划版本：SP4-PLAN 1.0
- overall 版本：SP-GENERAL 1.19
- 状态：计划已登记；尚未启动程序；尚未生成 SP4 配置、validation 数据或 promoted memory
- 前置 SP3 候选：`artifacts/candidates/sp3_candidate_experience_v1.jsonl`，119 条；hash 待 SP4.0 校验
- 前置 SP3 报告：`reports/sp3/SP3_experiment_report.md`，SHA-256 `c30f54dad9d37f099c3faddac5377087400eb6287ee2c25831fec19d921bc650`
- 当前允许：SP4.0 preflight、候选重新审计、反事实和 validation 的代码实现与冻结准备
- 当前禁止：SP5、正式 WebQSP/CWQ 效果评测、候选直接注入、未冻结配置下运行、promotion 前生成正式 memory
- 备注：G1/G2/G3 必须分层；G2 为 `oracle_guided_offline_teacher`，G3 为 `random_critic` 负对照

### 后续日志模板

#### LOG-SP4-XXX — [阶段步骤/运行名称]

- 日期：
- Run ID：
- 计划版本：
- overall 版本：
- Git commit / dirty status：
- 配置 / prompt / 数据 / registry SHA-256：
- 模型、temperature、KG endpoint、seed 与预算：
- memory 读取 / candidate injection / Oracle level：
- 输入规模与来源分层：
- 结果：
- win / tie / harm / invalid：
- validation / promotion 判定：
- 失败分类：
- 证据路径：
- 是否有效 Run：
- 对下一步的判断：

## 10. 计划变更记录

| 日期 | 版本 | 修改内容 | 原因 | 对可比性的影响 |
|---|---|---|---|---|
| 2026-08-23 | 1.0 | 新增 SP4 候选经验反事实验证、独立 validation、蒸馏与 promotion 计划；明确 G1/G2/G3 分层、候选只在通过门槛后进入 promoted_memory、正式 benchmark 延后至 SP5 | SP3 已 PASS 并生成 119 条未注入候选，需要先证明经验的局部因果价值、跨任务可迁移性和负迁移边界 | 不改写 SP3 结果；不使用 WebQSP 20/150 或 CWQ 50 调参或 promotion；为 SP5 提供冻结且可审计的 memory 输入 |
