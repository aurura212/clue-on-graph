# SP4：经验资格验证、前置能力补齐、蒸馏与 Promotion

> 文档编号：SP4-PLAN  
> 版本：2.1  
> 制定日期：2026-08-23  
> 状态：已运行，CONDITIONAL PASS 并收口  
> 当前阶段：SP4 已完成收口；不得进入 SP5  
> 实验根目录：`clue_on_graph/self-play/`  
> 上位约束：`00_experiment_overall_requirements.md` v1.20  
> 前置计划：`01_SP0_protocol_workspace_and_data_contract.md`、`02_SP1_pog_adapter_and_environment_binding.md`、`03_SP2A_live_kg_environment_validation.md`、`03A_SP2A_supplement_tail_and_dynamic_multihop.md`、`04_SP2B_llm_kg_baseline_rollout.md`、`05_SP3_candidate_experience_discovery.md`  
> 前置报告：`reports/sp3/SP3_experiment_report.md`  
> 历史版本：`06_SP4_counterfactual_distillation_promotion.md` v1.0  
> 后续阶段：SP5 PoG 单阶段 Memory 接入与固定冒烟验证

## 1. 阶段定位

初始方案中的“反事实验证、经验蒸馏与 promotion”，以及进入该环节前必须具备的合成任务、严格 held-out 和 Critic 证据，当前尚未完整完成。因此，SP4 v2 不只验证已有候选，还要先补齐候选经验能够被可信验证所必需的前置能力。

本阶段仍以 `self-play/` 下现有原 PoG 代码为推理基线。Self-Play 经验不替换原 PoG 的 KG 查询、动作合法性校验、Verifier 或题内工作记忆，只能通过受控的候选读取、匹配、提示注入或动作评分影响原 PoG 决策接口。

```text
SP3 候选经验
  -> 前置能力审计
  -> 固定 KG snapshot 与合成任务补充
  -> 严格 discovery / validation split
  -> 多轨迹与 Critic 补充证据
  -> 候选重新审计
  -> 同状态配对反事实验证
  -> 跨任务 held-out validation
  -> 去实体、去答案的经验蒸馏
  -> promotion 判定
  -> 冻结 promoted_memory
```

SP4 不是正式 WebQSP/CWQ 效果评测。SP4 完成前不得运行 SP5，也不得声称 Self-Play Memory 已提升 PoG 的 EM/F1。

## 2. 研究问题与目标

### 2.1 研究问题

1. 能否在固定 KG snapshot 上生成答案可判定、问题不泄漏路径和答案的合成 KGQA 任务？
2. Explorer 和在线 Critic 能否在不读取 Oracle 的情况下形成可重放的多轨迹、失败和恢复证据？
3. 候选经验建议的动作在相同状态和相同预算下是否优于原始动作或随机合法替代动作？
4. 经验能否跨实体、跨任务、跨问题表述和跨 path signature 迁移？
5. 反事实验证和蒸馏是否能降低 raw trajectory、成功轨迹缓存和随机干预带来的噪声？
6. `o0_critic`、`oracle_guided_offline_teacher` 和 `random_critic` 三类来源的质量是否不同？
7. 是否至少有一个决策阶段具备足够证据进入 PoG 单阶段接入阶段？

### 2.2 必须达成的目标

1. 补齐或审计固定 KG snapshot、合成任务生成器和严格 split。
2. 补充 SP3 中 Critic 上下文过长、多轨迹覆盖不足和恢复证据不足的问题。
3. 对 SP3 的 119 条候选进行 schema、来源、隐私、Oracle 泄漏和 replay 审计。
4. 实现候选检索、受控注入、同状态反事实、蒸馏和 promotion 判定功能。
5. 在独立的 `SP4-CF`、`SP4-V1` 和 `SP4-V2` 数据上完成验证。
6. 按决策阶段和候选来源报告正收益、负迁移、无效动作和额外成本。
7. 生成带 manifest 和 SHA-256 的只读 `promoted_memory`；没有规则通过时也必须保留完整失败证据。

## 3. 当前缺口与处理原则

| 缺口 | 当前情况 | SP4 处理 |
|---|---|---|
| 合成 KGQA 任务 | SP3 discovery 主要来自排除后的 WebQSP | 生成合成补充集，至少用于独立 validation；严格路线下用于重建候选 |
| 严格 held-out | D0/D1/H 不是完整的初始 split | 固定 source、answer、task/paraphrase、path-signature split |
| Critic 稳定性 | G1 有 31/60 条 system/protocol failure | 压缩上下文、补充 schema fallback，并重跑预注册子集 |
| Critic 恢复证据 | G0→G1 配对恢复率较低 | 只保留 Verifier 判定的恢复作为有效证据 |
| Backtrack | 当前仍 unsupported | SP4 完成协议门禁；SP5 的 PB 前必须通过 backtrack 预检 |
| 候选读取 | SP3 只写不读 | 实现 controlled injection 和同状态验证 |

若合成任务生成器无法在 SP4 内完成，SP4 不得宣称完全复现初始方案，应在报告中标记为 `CONDITIONAL PASS` 或 `FAIL`，并明确研究范围缩减。

## 4. 数据与隔离

### 4.1 SP4 禁止使用的 benchmark 数据

SP4 不得使用以下数据生成候选、蒸馏规则、调 prompt、调检索阈值、调 promotion 门槛或挑选有利结果：

```text
artifacts/datasets/webqsp_smoke_20.jsonl
artifacts/datasets/webqsp_model_compare_150.jsonl
artifacts/datasets/cwq_model_compare_50.jsonl
```

### 4.2 SP4 必须冻结的数据

```text
SP4-SYN：合成任务 discovery 补充集
SP4-CF：同状态反事实验证状态集
SP4-V1：原始候选的独立 validation 集
SP4-V2：蒸馏规则草案的独立 validation 集
```

每个集合必须记录 KG snapshot 或 recorded I/O 的 hash、构建配置、seed、题目 hash、source/answer entity hash、path signature、exposure registry 和文件 SHA-256。

各 split 必须满足：source entity、answer entity、task ID、问题/paraphrase 和 path signature 不交叉；已暴露 benchmark 的题目、答案、topic entity 和 gold 信息不得进入合成任务池；SP4-V1/V2 不得参与候选生成。

## 5. SP4 前置功能

这些功能在反事实实验前必须实现并通过 SP4.0 preflight。

### 5.1 合成任务生成器

至少支持：

- 固定 KG snapshot 身份校验；
- source entity 采样和 1–4 跳路径采样；
- 路径执行、答案集合计算和约束加入；
- 多种自然语言 verbalizer；
- 禁止 relation ID、答案名和显式路径泄漏；
- 任务去重、歧义过滤、难度分层；
- 隐藏 Oracle/witness 与 ActorView 的物理分离；
- discovery/validation/holdout manifest 和 exposure registry。

如果当前环境不能生成自然语言问题，必须记录降级方案，不得将结构化模板任务写成完整自然语言任务。

### 5.2 多轨迹与 Critic 补充运行器

至少支持：

- 每个 task 一条 actor-only trajectory；
- 每个预注册 task 至少 3 条不同 seed/temperature 的探索轨迹；
- 在 STOP 失败、无新 frontier、重复状态、预算临界和 branching 激增时触发 Critic；
- 每条失败轨迹最多 2 轮 Critic correction；
- Critic 只读 O0 public state；
- 结构化输出 `failure_type`、可见证据、候选动作和 confidence；
- 确定性分类上下文过长、schema 错误、timeout、retry 和协议失败；
- 保存成功、失败、恢复、预算耗尽和协议失败轨迹。

G2 必须标记为 offline teacher，G3 必须标记为 random negative control，二者不得并入 O0 Self-Play 结论。

### 5.3 动作协议与 Backtrack 门禁

验证以下动作均可解析、校验、执行和 replay：

```text
relation selection / continue / stop / backtrack
```

Backtrack 必须只能回到当前可见 frontier 或已观察状态，拒绝不可见目标，记录前后 state hash、成本、结果和 violation code。SP4 结束时仍不支持 backtrack，则后续只能标记 PB 为 unsupported。

### 5.4 Candidate injection 审计

实现候选读取器并保证：

- `memory_read=false` 时完全不读取候选；
- 只读取 eligible 候选；
- 检索键包含 decision stage、question intent、answer type、state signature、动作兼容性和 budget/progress；
- 空匹配、冲突匹配和低置信匹配回退原 PoG；
- 不增加原 PoG 不允许的 relation、entity、答案或事实；
- 记录 retrieved IDs、匹配分数、候选动作、注入内容和最终动作；
- 注入前后执行 Oracle、实体、答案、gold path 和 future-state secret scan。

## 6. 实验步骤

### SP4.0：启动前能力门禁

1. 校验 SP3 候选、报告、metrics、配置、prompt、registry 和代码 hash。
2. 完成合成任务、split builder、Critic context compressor、backtrack validator 和 candidate injector 的单元测试。
3. 执行 write-boundary、secret scan、Oracle projection、schema round-trip 和 deterministic replay 检查。
4. 冻结 SP4-SYN、SP4-CF、SP4-V1 和 SP4-V2 数据及 manifest。
5. 运行无 LLM/KG 的 preflight，确认失败能结构化退出。

通过条件：无 Oracle 泄漏、无越界写入、无 split 污染、backtrack 协议可判定、所有 hash 已冻结。

### SP4.1：合成任务与严格 split

1. 在固定 snapshot 上生成预注册数量的 1–4 跳合成任务。
2. 按 hop、branching、CVT、方向、约束和答案规模分层。
3. 生成 discovery、validation 和 holdout 子集。
4. 执行答案可判定性、问题不泄漏和确定性 replay 检查。
5. 报告 executable rate、dedup rate、ambiguity rate、leakage rate 和 split contamination rate。

### SP4.2：多轨迹与 Critic 补充

1. 在 SP4-SYN discovery 子集运行 actor-only、多 seed Explorer 和 O0 Critic。
2. 对失败或停滞轨迹执行最多两轮 Critic correction。
3. 分别报告 G0 Explorer-only、G1 O0 Critic、G2 offline teacher、G3 random critic 的完整率、replay 率、恢复率、成本和失败分类。
4. 重点复核 SP3 的 G1 system/protocol failure 和 G0→G1 恢复不足问题。
5. 只有 Verifier 判定的成功恢复才能成为候选证据。

### SP4.3：候选审计与同状态反事实

1. 逐条审计 SP3 的 119 条候选，记录来源、决策阶段、失败类型和拒绝原因。
2. 固定同一状态、候选动作、原始动作、随机合法动作和剩余预算。
3. 执行 CF0 原动作、CF1 候选动作、CF2 随机动作，必要时执行 sham/irrelevant 对照。
4. 比较成功、局部进展、新增相关 triple、无效 expansion、循环、提前停止、过度继续和调用成本。
5. 按 G1/G2/G3、decision stage 和 failure class 分层统计。

### SP4.4：独立 held-out validation

1. 在冻结的 SP4-V1 上验证通过初步审计的原始候选。
2. 在冻结的 SP4-V2 上验证蒸馏规则草案。
3. 统计 entity/task/paraphrase/path-signature held-out retention。
4. 记录触发率、适用条件、成功率、成本、harm、invalid、conflict 和 fallback。
5. 不得删除失败题目或反复使用同一 validation 结果调参。

### SP4.5：经验蒸馏

仅对通过 schema、privacy、leakage、replay 且拥有反事实证据的候选蒸馏。蒸馏结果必须包含：decision stage、抽象状态、推荐/禁止动作、适用条件、负向约束、支持 task/entity 数、CF/validation 统计、来源 hash、规则版本和状态。

必须删除或泛化题目实体、答案实体、实体 ID、完整问题、witness、gold path、future state、O4 字段和单题常量。

### SP4.6：Promotion

一条规则必须同时满足以下门槛才能进入 `promoted_memory`：

1. schema、privacy、leakage 和 replay 通过率 100%；
2. 至少来自 3 个独立 discovery task；
3. 至少 5 个有效可比较的反事实状态；
4. `win_rate - harm_rate >= 0.20`；
5. `harm_rate <= 0.10` 且 `invalid_rate` 不高于基线；
6. V1 至少触发 5 个 task；
7. V1 成功率不低于基线，或成功率持平且平均搜索成本下降至少 10%；
8. 收益不能由单一实体、题型或异常样本主导；
9. G3/sham 对照不能达到同等或更高收益；
10. 不依赖答案、gold path、witness、future state 或 O4；
11. memory、prompt、检索阈值和 promotion 配置已冻结。

未满足但有部分证据的规则标记为 `validated_candidate`；稳定有害的标记为 `rejected_harmful`；证据不足的标记为 `deferred`，不得静默删除。

### SP4.7：阶段收口

1. 完成计划日志、指标、失败分类、有效/无效 Run 和验收结论。
2. 生成 `reports/sp4/SP4_experiment_report.md` 和 `reports/sp4/metrics.json`。
3. 固定代码、配置、prompt、数据、registry、反事实、蒸馏、promotion 和 memory manifest 的 SHA-256。
4. 只有前置能力门禁、反事实、held-out validation、泄漏审计和报告收口均通过时才可 PASS。
5. 没有规则通过 promotion 可以形成有效 FAIL/CONDITIONAL PASS，但不得启动 SP5 Memory 增益实验。

## 7. 验收指标

| 类别 | 指标 |
|---|---|
| 任务质量 | executable rate、答案可判定率、歧义率、去重率、Oracle leakage rate |
| Split 隔离 | source/answer/task/paraphrase/path-signature 交叉数、exposure contamination |
| Rollout | trajectory completeness、deterministic replay rate、成功/失败/恢复数量 |
| Critic | recovery rate、failure classification、protocol error、timeout、平均调用成本 |
| 反事实 | win、tie、harm、invalid、uplift、新增相关 triple、无效 expansion |
| 泛化 | entity/task/paraphrase/path-signature held-out retention |
| Memory | 支持任务数、支持实体数、触发率、冲突率、fallback rate、规则数量 |
| 审计 | Oracle、答案、实体 ID、gold path、future state 泄漏，越界写入和 secret scan |

## 8. 预期代码功能与产物

新增或修改的实验代码必须位于 `self-play/`。至少实现或补齐：

```text
synthetic task generator / split builder
critic context compressor / recovery runner
backtrack action validator / replay support
candidate audit / privacy / leakage / source-trace checker
candidate retrieval / controlled injection
same-state counterfactual runner
distiller
promotion evaluator / immutable memory manifest writer
replay / hash / run metadata / write-boundary audit
```

预期产物：

```text
self-play/configs/sp4_precondition_and_promotion_v2.json
self-play/prompts/sp4_explorer_v2.*
self-play/prompts/sp4_critic_o0_v2.*
self-play/prompts/sp4_distiller_v2.*
self-play/prompts/sp4_code_generation_v2.md
self-play/artifacts/datasets/sp4_synthetic_discovery_v1.jsonl
self-play/artifacts/datasets/sp4_counterfactual_v1.jsonl
self-play/artifacts/datasets/sp4_validation_v1.jsonl
self-play/artifacts/datasets/sp4_validation_v2.jsonl
self-play/artifacts/datasets/sp4_synthetic_manifest_v1.json
self-play/artifacts/registries/sp4_validation_registry_v1.json
self-play/artifacts/registries/sp4_exposure_registry_v1.json
self-play/artifacts/counterfactual/sp4_counterfactual_results_v2.jsonl
self-play/artifacts/candidates/sp4_validated_candidates_v2.jsonl
self-play/artifacts/candidates/sp4_rejected_candidates_v2.jsonl
self-play/artifacts/memory/promoted_memory_v2.jsonl
self-play/artifacts/memory/promotion_decisions_v2.jsonl
self-play/artifacts/memory/memory_manifest_v2.json
self-play/artifacts/protocol/sp4_check_result.json
self-play/reports/sp4/SP4_experiment_report.md
self-play/reports/sp4/metrics.json
```

如果某产物因实验失败未生成，必须记录 `NOT_GENERATED` 及原因，不得创建虚假的空成功文件。

## 9. 代码生成 Prompt（可直接提供给代码大模型）

本节专门定义“让代码大模型实现 SP4 代码”的 prompt。它不是新的实验步骤，也不授权代码大模型运行正式实验；它的作用是把本计划中的功能边界、数据契约、审计要求和验收标准转换成一份可执行的代码生成说明。建议将本节代码块同步保存为 `self-play/prompts/sp4_code_generation_v2.md`，并在运行日志中记录该文件的 SHA-256。

这份 prompt 预期生成的是一个**可测试、可重放、默认安全失败的 SP4 实验代码增量**，而不是一套绕过原 PoG 的新推理系统。代码大模型应先阅读仓库中的现有实现和上位计划，再复用已有接口；不能因为接口不明确就虚构 KG schema、动作协议、模型 API 或实验结果。

```text
你是本项目的代码实现工程师。请在当前仓库中实现 SP4：经验资格验证、前置能力补齐、同状态反事实验证、经验蒸馏和 promotion。实验根目录是 clue_on_graph/self-play/。先阅读并遵守：
1. clue_on_graph/self-play/exp_plan/00_experiment_overall_requirements.md
2. clue_on_graph/self-play/exp_plan/01_SP0_protocol_workspace_and_data_contract.md
3. clue_on_graph/self-play/exp_plan/02_SP1_pog_adapter_and_environment_binding.md
4. clue_on_graph/self-play/exp_plan/03_SP2A_live_kg_environment_validation.md
5. clue_on_graph/self-play/exp_plan/04_SP2B_llm_kg_baseline_rollout.md
6. clue_on_graph/self-play/exp_plan/05_SP3_candidate_experience_discovery.md
7. clue_on_graph/self-play/exp_plan/06_SP4_precondition_counterfactual_distillation_promotion_v2.md

你的任务是实现代码，不是改写实验结论，也不是直接运行正式 WebQSP/CWQ 评测。开始编码前必须检查现有目录、入口脚本、配置格式、数据 schema、动作协议、Verifier、KG adapter、日志和测试；优先复用现有接口，只有确实缺失时才新增最小兼容层。所有新增或修改的实验代码必须位于 clue_on_graph/self-play/ 下，并保持现有原 PoG 行为不变。

【总体功能】
实现一个由以下阶段组成的、可单独运行和可重放的 SP4 pipeline：
A. preflight：检查代码/配置/prompt/数据/registry 的 hash，检查目录写边界、schema round-trip、Oracle projection、secret scan 和 deterministic replay；
B. synthetic task generator：在固定 KG snapshot 上采样 source entity 和 1–4 跳路径，执行路径得到可判定答案，生成不泄漏 relation ID、答案名、显式路径和 future state 的任务，并建立 discovery/validation/holdout split、manifest 和 exposure registry；
C. multi-trajectory/Critic runner：对 discovery task 运行 actor-only 和多 seed 探索轨迹，在 STOP 失败、无新 frontier、重复状态、预算临界和 branching 激增时触发 O0 Critic，最多进行两轮 correction，并保存成功、失败、恢复、预算耗尽和协议失败证据；
D. action/backtrack validator：解析、校验、执行和 replay relation selection、continue、stop、backtrack，backtrack 只能指向当前可见 frontier 或已观察状态；
E. candidate audit/injection：审计 SP3 候选的 schema、来源、隐私、Oracle 泄漏和 replay；在 memory_read=false 时完全不读候选，在开启时只检索 eligible 候选，并对注入前后做 secret scan；
F. same-state counterfactual runner：固定同一初始状态、相同预算和相同环境，比较原始动作、候选动作、随机合法动作，必要时支持 sham/irrelevant 对照，输出可比较的 win/tie/harm/invalid 和成本证据；
G. held-out validator：在冻结的 SP4-V1 验证原始候选，在冻结的 SP4-V2 验证蒸馏规则，统计 entity/task/paraphrase/path-signature held-out 泛化；
H. distiller：只对通过 schema、privacy、leakage、replay 且具有反事实证据的候选生成实体无关、答案无关、路径无关的抽象规则；
I. promotion evaluator：根据 SP4 计划中的固定门槛判定 promoted_memory、validated_candidate、rejected_harmful 或 deferred，并生成只读 memory manifest 和 SHA-256；
J. report/metrics/replay：为每个 run 保存配置、输入、状态、动作、输出、错误、seed、预算、模型、endpoint 和 hash，输出 JSONL/JSON/Markdown 产物，失败时结构化退出。

【必须实现的模块职责】
请按现有项目风格拆分模块；可以使用不同文件名，但最终必须有清晰对应关系：
- `synthetic_tasks`：固定 snapshot 校验、实体/路径采样、路径执行、答案计算、verbalizer、去重、歧义过滤、难度分层、split 和 exposure registry；
- `critic_runner`：上下文压缩、O0 public-state 投影、结构化 Critic 调用、schema fallback、timeout/retry、failure classification、correction budget；
- `action_protocol`：动作 schema、合法性校验、backtrack 可见性校验、state hash、replay；
- `candidate_audit`：候选 schema/source/privacy/leakage/replay 检查、拒绝原因和审计报告；
- `candidate_retrieval`：按 decision stage、question intent、answer type、state signature、动作兼容性和 budget/progress 检索，处理空匹配、冲突匹配和低置信 fallback；
- `counterfactual_runner`：状态克隆或等价恢复、CF0/CF1/CF2 配对执行、随机合法动作采样、sham 对照、结果归因和成本统计；
- `distiller`：从多条候选/轨迹/反事实证据抽取规则，删除或泛化实体、答案、ID、完整问题、witness、gold path、future state、O4 和单题常量；
- `promotion`：固定阈值判定、分层统计、conflict/harm 检查、状态分类、不可变输出和 manifest；
- `audit_and_io`：write-boundary、secret scan、JSONL 原子写入、文件 hash、run metadata、NOT_GENERATED 记录。

【不可违反的实验约束】
1. 不得读取或使用以下 benchmark 数据来生成候选、蒸馏规则、调 prompt、调阈值、调 promotion 门槛或挑选结果：
   - artifacts/datasets/webqsp_smoke_20.jsonl
   - artifacts/datasets/webqsp_model_compare_150.jsonl
   - artifacts/datasets/cwq_model_compare_50.jsonl
2. SP4-SYN、SP4-CF、SP4-V1、SP4-V2 必须有固定 manifest、seed、题目 hash、source/answer entity hash、path signature、exposure registry 和 SHA-256；V1/V2 不得参与候选生成。
3. ActorView 与隐藏 Oracle/witness 必须物理分离。O0 Critic 只能读 public state；G2 必须标为 offline teacher，G3 必须标为 random negative control，不能把它们并入 O0 Self-Play 结论。
4. `memory_read=false` 时不得读取、解析或缓存候选；candidate injection 不得新增原 PoG 不允许的 relation、entity、答案或事实。
5. 不得把答案、relation ID、显式 gold path、witness、future state、O4 字段、secret 或完整题目常量写入候选规则。
6. 不得删除失败样本、静默覆盖旧产物、伪造空成功文件或在失败后继续执行依赖该产物的阶段；缺失产物必须记录 `NOT_GENERATED` 和原因。
7. 默认 fail-closed：配置缺失、schema 错误、hash 不匹配、split 污染、越界写入、secret scan 命中或 replay 不一致时，返回结构化错误并停止相关阶段。
8. 不得改变原 PoG 的 KG 查询、动作合法性校验、Verifier 或题内工作记忆；Self-Play 只能通过受控读取、匹配、提示注入或动作评分影响决策接口。

【关键数据契约】
所有 JSON/JSONL 记录必须带有版本字段和最小可追溯字段。至少定义并校验：
- `task`：task_id、split、snapshot_id/hash、source_entity_hash、answer_entity_hash、path_signature、question_hash、difficulty、oracle_level；
- `trajectory`：run_id、task_id、seed、temperature、stage、initial/final_state_hash、actions、failure_type、critic_source、recovery_status、budget、replay_status；
- `candidate`：candidate_id、source_trace_hash、decision_stage、abstract_state、recommended_action 或 forbidden_action、preconditions、negative_constraints、evidence_refs、privacy/leakage/replay status；
- `counterfactual`：pair_id、state_hash、budget、CF0/CF1/CF2 action/result、outcome、cost、invalid_reason、new_relevant_triples、control_type；
- `memory_rule`：rule_id、rule_version、decision_stage、abstract_state、action policy、applicability、support counts、CF/validation statistics、source hashes、status。
字段名如与仓库既有 schema 冲突，必须写 adapter 或 schema version mapping，不能悄悄改变旧数据含义。

【反事实和 promotion 的固定逻辑】
- CF0 是原始动作，CF1 是候选动作，CF2 是随机合法动作；三者必须在同状态、同预算、同环境条件下比较。
- 至少记录 success/local progress/new relevant triples/invalid expansion/loop/early stop/over-continue/cost，并能区分 win、tie、harm、invalid。
- promotion 至少检查：审计通过率 100%；来自至少 3 个独立 discovery task；至少 5 个有效反事实状态；`win_rate - harm_rate >= 0.20`；`harm_rate <= 0.10`；invalid 不高于基线；V1 至少触发 5 个 task；V1 成功率不低于基线，或持平且平均搜索成本下降至少 10%；收益不由单一实体/题型/异常样本主导；G3/sham 不得达到同等或更高收益；memory、prompt、检索阈值和 promotion 配置已冻结。
- 未通过但证据部分充分的规则输出 `validated_candidate`；稳定有害输出 `rejected_harmful`；证据不足输出 `deferred`；所有状态都必须保留原因和证据引用。

【实现与测试要求】
1. 先提交实现计划和仓库审计结果，再修改代码；列出每个新增/修改文件及其职责。
2. 先实现纯函数和 schema，再实现 runner、adapter 和 CLI；避免把 LLM/KG 调用写死在业务逻辑中，使用可注入接口和 fake backend。
3. 为每个模块提供单元测试；至少覆盖：路径/任务去重、split 污染、答案/路径泄漏、Oracle projection、Critic schema fallback、backtrack 越界、memory_read=false、候选冲突 fallback、同状态 replay、随机动作合法性、蒸馏去实体去答案、promotion 边界、hash/原子写入和失败结构化退出。
4. 提供无 LLM、无真实 KG 的 deterministic smoke test，能够在干净环境下验证端到端数据流；真实 API、真实 KG 和正式 benchmark 运行必须通过配置显式开启。
5. 为 CLI 提供 `preflight`、`generate-synthetic`、`run-critic`、`audit-candidates`、`run-counterfactual`、`distill`、`validate`、`promote`、`report` 或等价子命令，并支持 `--dry-run`、`--seed`、`--config`、`--run-id` 和输出目录参数。
6. 不要在代码中硬编码本机绝对路径、密钥、模型响应或实验结果；配置、prompt、数据和运行参数必须可追溯。
7. 完成后输出：实现摘要、文件清单、数据契约、CLI 用法、测试命令及结果、已知限制、未实现项。没有实际运行的部分必须明确写 `NOT RUN`，不得声称通过。

【交付验收】
代码大模型生成的实现只有在以下条件全部满足时才算完成：
- 可在无 LLM/KG 条件下通过 deterministic preflight 和 smoke test；
- 所有写入均位于允许的 self-play 输出边界，旧产物不被覆盖；
- schema round-trip、secret scan、Oracle projection、replay 和 hash 检查有测试证据；
- 能生成或明确记录 `NOT_GENERATED` 的 SP4 预期产物；
- 能输出 promotion decision、失败原因和 memory manifest；
- 不声称运行了真实实验，不声称提升了 WebQSP/CWQ EM/F1；
- 若仓库现状阻碍实现，先报告具体阻塞接口和最小补丁，不得用伪实现掩盖问题。
```

代码大模型的输出应当是“实现增量 + 测试 + 运行说明 + 限制说明”。它不应生成绕过 PoG 的第二套 agent、不应把 Oracle 变成 Actor 可见输入、不应直接生成已经 promotion 的 memory，也不应把一次成功 smoke test 写成正式实验结论。
## 10. 与后续阶段的边界

SP4 完成的是“经验是否具有进入 PoG 的资格”，不是“经验接入后是否提升最终 KGQA”。后续阶段仍需完成：

- SP5：PR、PC、PB 单阶段接入和 WebQSP smoke 20；
- SP6：Development 机制实验、内容/成本/轨迹形式/状态条件化消融；
- SP7：冻结后的 WebQSP 150 与 CWQ 50 正式对比；
- SP8：满足条件后的 PALL 组合和跨数据集迁移。

## 11. 实验日志

以下区域只追加，不覆盖已有记录。每次运行前记录计划版本、overall 版本、代码/配置/prompt/数据 hash、模型、KG endpoint、seed、预算、memory 状态和实验目的；每次运行后记录 Run ID、退出状态、有效性、关键指标、异常、证据路径和下一步判断。

### LOG-SP4-001：计划重制定登记

- 日期：2026-08-23
- 计划版本：SP4-PLAN 2.0
- overall 版本：SP-GENERAL 1.20
- 状态：计划已重制定，尚未启动程序
- 前置候选：`artifacts/candidates/sp3_candidate_experience_v1.jsonl`，119 条；待 SP4.0 校验
- 前置报告：`reports/sp3/SP3_experiment_report.md`，SHA-256 `c30f54dad9d37f099c3faddac5377087400eb6287ee2c25831fec19d921bc650`
- 新增前置工作：合成任务与严格 split、多轨迹 Critic 补充、backtrack 协议门禁、candidate injection 审计
- 当前允许：SP4.0 preflight、代码实现、数据冻结和审计准备
- 当前禁止：候选直接注入、SP5、正式 WebQSP/CWQ 效果评测和 promotion 前生成正式 memory

### LOG-SP4-002：代码实现与离线单测

- 日期：2026-08-23
- Run ID：无（仅代码与 `tests/test_sp4_offline.py`）
- 计划版本：SP4-PLAN 2.1
- overall 版本：SP-GENERAL 1.20
- Git commit / dirty status：`492348b5aef5b04ca2d77cb41a1a9da8049e6b79` dirty
- 配置、prompt、数据和 registry SHA-256：实现时配置尚未含 synthetic manifest hash
- 模型、temperature、KG endpoint、seed、预算：无 LLM/KG
- Oracle level、memory read、candidate injection：O0；memory_read=false
- 输入规模与数据来源：fixture snapshot 生成器与 SP3 候选只读审计
- 结果和指标：SP4 离线测试 14 通过；全库 103 通过 / 0 失败
- 失败分类：无
- 有效/无效 Run 判断：代码与测试有效，不是正式评测
- 证据路径：`self-play/src/sp_memory/sp4_*.py`、`tests/test_sp4_offline.py`
- 对下一步的判断：CONTINUE_PHASE，运行 preflight 并冻结 SP4-SYN/CF/V1/V2

### LOG-SP4-003：SP4.0–SP4.7 无 LLM/KG 正式运行

- 日期：2026-08-23
- Run ID：`sp4-20260823T050956Z-69e15a34`
- 计划版本：SP4-PLAN 2.1
- overall 版本：SP-GENERAL 1.20
- Git commit / dirty status：`492348b5aef5b04ca2d77cb41a1a9da8049e6b79` dirty
- 配置、prompt、数据和 registry SHA-256：运行时配置 `58e9a544…`；冻结后配置（含 manifest hash）`2017dd81a4f6071a5b82cf5170a72072ab22ec6c48672de6fb1a8a06c7cfd977`；synthetic manifest `b22c08bd…`；snapshot file `09178a44…`
- 模型、temperature、KG endpoint、seed、预算：未调用 LLM/KG；seed=20260823；snapshot 预算 depth 4 / steps 16
- Oracle level、memory read、candidate injection：Actor/Critic O0；memory_read=false；injection=false
- 输入规模与数据来源：fixture snapshot；discovery 12 / V1 8 / V2 8 / holdout 8；SP3 候选 119 条只审计
- 结果和指标：split 污染 0；泄漏率 0。G0/G1/G2/G3 replay 100%，G1 system_failure 0。SP3 审计 113/119 通过。CF n=12，win=0，tie=0.583，invalid=0.417，harm=0。蒸馏 4 条，promoted 0，deferred 4
- 失败分类：无未分类异常。降级：模板 verbalizer、启发式 Critic、fixture snapshot
- 有效/无效 Run 判断：有效 CONDITIONAL PASS；不得进入 SP5
- 证据路径：`runs/sp4-20260823T050956Z-69e15a34/`；`reports/sp4/SP4_experiment_report.md` SHA-256 `65a4d7da846ecb4c79207ecd48777f55be12e5d306c1ea8d704e342faed2de43`
- 对下一步的判断：ACCEPT 本阶段 CONDITIONAL PASS 并收口。禁止 SP5、禁止把结果写成 EM/F1 或 V2-5

### 后续日志模板

#### LOG-SP4-XXX：[步骤/运行名称]

- 日期：
- Run ID：
- 计划版本：
- overall 版本：
- Git commit / dirty status：
- 配置、prompt、数据和 registry SHA-256：
- 模型、temperature、KG endpoint、seed、预算：
- Oracle level、memory read、candidate injection：
- 输入规模与数据来源：
- 结果和指标：
- 失败分类：
- 有效/无效 Run 判断：
- 证据路径：
- 对下一步的判断：

## 12. 计划变更记录

| 日期 | 版本 | 修改内容 | 修改原因 | 对可比性的影响 |
|---|---|---|---|---|
| 2026-08-23 | 1.0 | 登记候选反事实、validation、蒸馏和 promotion 计划 | SP3 生成 119 条候选但尚未验证 | 不改变 SP3 结果 |
| 2026-08-23 | 2.0 | 将合成任务、严格 held-out split、多轨迹 Critic 补充、backtrack 门禁、candidate injection 审计纳入 SP4 前置；重新定义 SP4 验收门槛和后续边界 | 初始方案要求尚未全部落地，原 SP4 范围不足以支撑正式 Memory 评测 | 不重写 SP3；SP4 产物和 memory 版本升级为 v2；禁止使用 WebQSP/CWQ 固定 benchmark 集调参或 promotion |
| 2026-08-23 | 2.1 | 新增独立的代码生成 Prompt，明确模块职责、数据契约、实验边界、测试要求和交付验收 | 让代码大模型能够按 SP4 计划生成可测试、可重放且默认安全失败的实现，也便于人工审阅预期代码范围 | 不改变实验数据和统计门槛；新增 prompt 文件需纳入 hash 和日志追踪 |
| 2026-08-23 | 2.1 收口 | 状态改为 CONDITIONAL PASS 并收口；overall 收口后升级为 1.21 | 实验已完成：0 条 promotion；模板 verbalizer 与非 live LLM/KG 为登记降级 | 不改写运行产物；不启动 SP5 |



