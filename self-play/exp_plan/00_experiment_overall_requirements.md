# PoG Self-Play 经验记忆实验总体要求

> 文档编号：SP-GENERAL  
> 版本：1.15
> 制定日期：2026-08-21  
> 实验根目录：clue_on_graph/self-play/  
> 状态：生效，后续每一步实验均须遵守

## 1. 文档用途

本文件是 PoG Self-Play 经验记忆实验的长期总体约束，负责固定实验目标、研究问题、目录边界、角色权限、数据隔离、实验推进规则和结果判定原则。

任何一步实验在开始设计、实现或运行前，必须同时阅读：

1. 本总体要求文件；
2. 该步骤在“实验步骤与计划文件索引”中登记的对应计划文件。

若两个文件存在冲突，以本总体要求为上位约束。若确实需要改变总体方向，应先修改本文件、说明修改理由并升级版本，再修改步骤计划和代码。不得通过临时代码或运行参数绕过本文件的约束。

## 2. 实验空间与文件边界

### 2.1 唯一实验空间

Self-Play 经验记忆实验产生的全部新增内容必须位于：

~~~text
clue_on_graph/self-play/
~~~

包括实验计划、代码、脚本、配置、prompt、派生数据、任务划分、轨迹、Oracle/Verifier 输出、反事实结果、memory、缓存、日志、指标、图表、报告和测试产物。

self-play/ 下现有代码就是本实验使用的原 PoG 代码和推理基线。后续产生的 Self-Play 经验记忆不替换该基线，而是在原 PoG 的推理流程和决策接口上发挥作用，并通过受控的 memory 检索、提示注入或动作评分进行对照实验。新增实验代码和产物仍必须位于 self-play/ 内；不得在 PoG/、data/、cope_alias/、results/ 或项目其他目录中写入本实验的新增产物。若为了接入或调用原 PoG 需要修改外部目录代码，必须在对应步骤计划中预先列明修改范围、理由、验证方法和回退方式。

### 2.2 共享输入数据

本实验继续使用：

~~~text
clue_on_graph/data/
clue_on_graph/cope_alias/
~~~

这两个目录视为只读输入：

- 不修改原文件，也不在其中生成 split、索引、缓存或清洗结果；
- 运行时记录实际读取文件的相对路径、大小、SHA-256 和修改时间；
- 派生文件统一写入 self-play/artifacts/ 或 self-play/runs/；
- 原始数据原则上不复制；必须冻结小型子集时，复制件放入 self-play/artifacts/datasets/，并记录来源、源文件哈希和筛选规则。

### 2.3 建议目录结构

~~~text
self-play/
├── exp_plan/       # 总体要求、分步计划及计划内实验日志
├── src/            # Self-Play 实验实现
├── configs/        # 冻结配置，不存密钥
├── prompts/        # 版本化 prompt
├── scripts/        # 可复现入口脚本
├── tests/          # 单元测试、集成测试和 fixture
├── artifacts/      # split、manifest、memory 和派生数据
├── runs/           # 每次运行的独立目录
├── logs/           # 跨运行日志与审计日志
└── reports/        # 每阶段实验报告、汇总表、图和实验结论
~~~

不要求第一步创建尚未使用的空目录。self-play/ 根目录下现有代码按原 PoG 基线处理；SP0 需要登记其主要入口、依赖和可运行边界，但不将其误认为 Self-Play memory 实现。之后新增的 Self-Play 模块、适配层、配置和产物应与原 PoG 基线清晰分层，并记录二者的接口关系。

### 2.4 测试、验证与抽样协议

凡是在现有原 PoG 基础上进行模型、memory、prompt、检索策略或不同模型的测试与验证，都必须使用预先定义并冻结的评测数据。测试数据的具体抽取时机、样本规模、数据集文件、manifest、哈希校验、作废规则和后续复用方式，按照当前阶段对应计划文件执行；当前 SP0 的具体要求见 `01_SP0_protocol_workspace_and_data_contract.md`。

总体原则是：评测数据必须在正式测试前固定，后续同一用途下的模型、原 PoG 基线和 memory 对照必须使用相同题目，不能根据模型结果、题目难度、是否容易回答或历史失败情况事后挑题、换题、补题或重新抽样。不同数据集必须分别统计和报告，不得合并为无法区分数据来源的单一指标。

如果某一后续阶段需要新的 validation 或 test 数据，必须在该阶段计划文件中预先定义并冻结，不能只通过口头约定或运行参数临时生成。

## 2.5 当前实验阶段与阶段更新规则

当前总体阶段为：**SP2-B 启动准备：无 Memory 的 LLM+KG 端到端基线（计划已登记，尚未运行）**。SP0 已于 2026-08-22 验收 PASS。SP1 已于 2026-08-22 验收 PASS。SP2-A 主实验已于 2026-08-22 完成基础 PASS。SP2-A 补充实验已于 2026-08-22 验收 PASS 并完成重新收口。

- 对应计划文件：04_SP2B_llm_kg_baseline_rollout.md（已登记，尚未运行）
- 主实验状态：SP2-A 基础 live KG 环境验证 PASS；补充实验已补齐真实非空 TAIL 正向语义和由第一跳真实返回驱动的动态两跳证据
- 当前状态：SP2-A 含补充已 PASS 并收口；SP2-B 已完成启动登记但尚未运行。SP2-B 只建立无 Self-Play Experience Memory 的 LLM+KG 在线基线，不生成或注入 Self-Play 经验；原 PoG 题内 `pog_working_memory` 允许存在，但必须题内隔离、写入当前 Run 的 scratch 路径且不得跨题或跨 Run 复用；SP2-B 不运行 WebQSP 150/CWQ 50 正式效果对比
- 当前基线：self-play/ 下现有原 PoG 代码 + 冻结协议 `sp-protocol-v1` + 已验证的 PoG adapter + 已验证的 live KG Environment binding（含 TAIL-positive 与动态两跳）
- 当前允许工作：执行 SP2-B 计划规定的启动前检查、代码实现和无 memory rollout；正式运行前必须冻结模型、prompt、预算、任务 registry、配置和审计规则
- 冻结评测集：`artifacts/datasets/webqsp_smoke_20.jsonl`（20，仅冒烟）、`webqsp_model_compare_150.jsonl`（150）、`cwq_model_compare_50.jsonl`（50）；后续只校验哈希，不得重抽
- SP2-A 有效 run：`sp2a-20260822T082704Z-28a5bc97`
- SP2-A 补充有效 run：`sp2a-supp-20260822T111116Z-79aa8ea8`
- SP2-A 报告：`reports/sp2a/SP2A_experiment_report.md` SHA-256 `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`

每个阶段的实验工作结束后，必须完成阶段收口。阶段收口的强制顺序为：

1. 完成当前阶段计划文件中的实验日志、指标、异常、有效运行和验收结论；
2. 无论结论为 PASS、CONDITIONAL PASS 还是 FAIL，都生成该阶段实验报告并保存到 `self-play/reports/<stage-id-lower>/`；默认主报告命名为 `<STAGE-ID>_experiment_report.md`；
3. 报告至少包含研究目标、计划与协议版本、代码/配置/数据哈希、有效和无效 Run ID、实验设置、指标与结果、异常与失败分类、验收结论、未解决风险和产物索引；
4. 在本文件的步骤索引、阶段历史和变更记录中登记阶段结论、报告路径及收口日期；
5. 若之后进入新阶段，再更新“当前总体阶段”、新阶段状态、允许开始的工作、前置依赖和未解决风险。

生成下一阶段实验计划不属于当前阶段的结束产物，也不是当前阶段 PASS、FAIL、报告生成或阶段收口的前置条件。若后续决定启动新阶段，必须在该阶段的启动准备期间制定和登记对应计划，但不要求在上一阶段实验结束时提前生成。

阶段历史必须保留，不得只把当前阶段改成新名称而删除旧阶段记录。阶段历史统一维护在下表中：

| 阶段 | 进入日期 | 计划文件 | 状态 | 阶段结论/切换依据 |
|---|---|---|---|---|
| SP0 | 2026-08-21 | 01_SP0_protocol_workspace_and_data_contract.md | 已完成 PASS（2026-08-22） | E0.1-E0.7 全部通过；协议 `sp-protocol-v1` 与 WebQSP/CWQ 固定评测集已冻结。报告：`reports/sp0/SP0_experiment_report.md` |
| SP1 | 2026-08-22 | 02_SP1_pog_adapter_and_environment_binding.md | 已完成 PASS（2026-08-22） | E1.1-E1.12 全部通过；有效 run `sp1-20260822T030044Z-8cb155e0`。报告：`reports/sp1/SP1_experiment_report.md` SHA-256 `5eebd53383e417a65a85dac8a42c1b2db494226bb7ab269bea7f878e51b9d333`。已完成阶段收口 |
| SP2-A | 2026-08-22 | 03_SP2A_live_kg_environment_validation.md | 主实验已完成 PASS（2026-08-22） | E2A.1-E2A.7 全部通过；有效 run `sp2a-20260822T082704Z-28a5bc97`。报告：`reports/sp2a/SP2A_experiment_report.md` SHA-256 `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`。基础实验已收口；TAIL/动态两跳由补充实验补齐 |
| SP2-A-SUPPLEMENT | 2026-08-22 | 03A_SP2A_supplement_tail_and_dynamic_multihop.md | 已完成 PASS（2026-08-22） | S2A-S.1–S.4 与 replay 通过；有效 run `sp2a-supp-20260822T111116Z-79aa8ea8`。报告：`reports/sp2a/SP2A_experiment_report.md` SHA-256 `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`（第 13 节）。已完成重新收口；不启动 SP2-B |
| SP2-B | 2026-08-22 | 04_SP2B_llm_kg_baseline_rollout.md | 已登记，启动准备中，尚未运行 | 首次正式使用 LLM+live KG；不读取或写入 Self-Play Experience Memory，但允许原 PoG 题内工作记忆；先执行 B0 少量人工核查，再执行 B1 独立开发任务，稳定后才允许 B2 WebQSP smoke 20 |

## 3. 实验核心思想

本实验不缓存具体 KG 答案或路径，而是从受控搜索中提取可迁移的程序性经验：

~~~text
问题意图
+ 当前可见搜索状态
+ 合法候选动作
+ 动作造成的进展、失败、成本和最终结果
= 状态条件化的搜索经验
~~~

经验在 PoG 做关键决策前按“问题—状态—动作”条件检索，以软提示或可审计的动作评分影响：

- relation selection；
- continue/stop；
- backtrack/failure recovery。

经验不得直接给出答案实体、具体训练题事实、完整 gold path 或不可见动作。模型仍须在当前可见且合法的动作集合中搜索，最终结果由独立 Verifier 判定。实验增强的是现有原 PoG 的推理管线：原 PoG 保持为基线，Self-Play memory 作为外接的状态条件化经验模块影响其决策。初期不进行微调、强化学习或梯度更新。

## 4. 研究问题

- **RQ1 可构建性：** 能否产生合法、可重放、无泄漏的 KGQA Self-Play 轨迹？
- **RQ2 经验有效性：** 经验能否提高 EM/F1，或在准确率不下降时显著降低搜索成本？
- **RQ3 局部机制：** 经验对 relation、continue/stop、backtrack 中哪些决策有效，局部改善能否解释最终指标？
- **RQ4 失败恢复：** 经 Critic、replay 和配对反事实验证的失败恢复经验是否优于成功轨迹缓存或未经验证的总结？
- **RQ5 泛化：** 去实体和答案信息的经验能否跨实体、问题表述、局部子图和 path signature 迁移？
- **RQ6 增益来源：** 提升是否来自经验内容，而非 prompt 变长、更多调用、搜索更久或测试泄漏？
- **RQ7 Oracle 辅助：** Oracle 派生的离线监督能否加速经验发现，并在正式推理不读取 Oracle 时保持增益？

## 5. 角色、Oracle 与信息权限

### 5.1 角色定义

- **Explorer/Actor：** 根据问题和当前可见状态选择合法动作。
- **Critic：** 诊断停滞或失败并提出受限纠错动作；建议本身不是真值。
- **Environment：** 执行合法 KG 动作，返回确定性可见结果并维护预算。
- **Oracle：** 保存由固定数据/KG 与逻辑查询确定的隐藏答案、约束和 witness，不是参与搜索的普通 Agent。
- **Verifier：** 读取必要 Oracle 字段，检查动作合法性、重放一致性、答案正确性、预算和泄漏。
- **Counterfactual evaluator：** 在同一状态和剩余预算下替换一个动作并比较结果。
- **Distiller/Promoter：** 将多条证据蒸馏为抽象经验，并依据 held-out 结果决定是否进入正式 memory。

### 5.2 Oracle 信息等级

| 等级 | 可提供的信息 | 允许用途 |
|---|---|---|
| O0 | 无 Oracle 派生反馈 | 正式推理与最终评测中的 Explorer/Critic |
| O1 | 最终成功/失败 | discovery 阶段弱监督和分析 |
| O2 | 不含答案的局部进展或失败类型 | discovery 阶段过程监督 |
| O3 | 同状态候选动作的结果比较 | 离线反事实标注和经验筛选 |
| O4 | 答案、witness、gold path 或逻辑查询 | 任务生成、Verifier、调试和上界分析 |

约束：

- O4 不得进入正式 Explorer/Critic 的运行上下文；
- O1-O3 若用于经验发现，必须在 trace 中记录来源，蒸馏后删除答案、实体 ID、完整路径和未来状态；
- 正式验证和测试时，Explorer/Critic 必须处于 O0，Oracle 只在后台由 Verifier 判分；
- 可设置 Oracle-guided teacher 生成标签，但必须与 O0 student/online Critic 分离，方法名称和对照组要准确反映这一点。

## 6. 经验生成与使用总流程

~~~text
固定数据/KG 快照与任务协议
  -> 生成问题、隐藏答案和 witness
  -> Explorer 多轨迹搜索
  -> Critic 对失败或停滞进行受限纠错
  -> Verifier 确定性重放与判定
  -> 同状态配对反事实比较
  -> 去实体化、去答案化的经验蒸馏
  -> held-out validation 与 promotion
  -> 冻结 memory
  -> 在 O0 条件下接入 PoG 的单一决策点
  -> 对照、消融、最终评测和迁移评测
~~~

任意单条成功或 Critic 自我评价均不足以形成正式经验。经验必须保留来源、支持任务数、反事实证据、成本变化、伤害率、验证结果和版本信息。

## 7. 全局实验原则

### 7.1 数据与测试隔离

- discovery、validation、test 和 benchmark final 必须预先划分并保存 manifest/hash；
- final 结果在代码、memory、prompt、阈值和分析脚本冻结前不可用于调参；
- benchmark test 题目、答案和测试轨迹不得参与经验生成、蒸馏或 promotion；WebQSP/CWQ 的冻结验证和模型对比样本也不得反向进入 memory 构建；
- 测试期间只读冻结 memory，不允许根据测试失败修订经验；
- 已暴露题目不得宣称为 unseen，应进入 exposure registry；WebQSP/CWQ 的各用途样本必须分别记录 exposure 和 sample manifest。

### 7.2 可复现性

每次正式运行至少记录 run ID、时间、步骤和计划版本、git commit/dirty status、输入文件与哈希、配置、随机种子、模型标识、temperature、prompt/memory 版本、预算、实际成本、输出、错误、逐题 trace 和聚合指标。

### 7.3 公平比较

- 必须包含原始 PoG 基线；
- 对照组尽量匹配 LLM/KG 调用、搜索预算和 prompt token；
- 视步骤加入 shuffled、irrelevant、random、equal-cost、raw、success-only、no-counterfactual、stateless 对照；
- relation、continue/stop、backtrack 先单独接入，通过后再组合；
- 报告平均值、离散程度、配对差异、置信区间和失败案例，不只报告最佳运行。

### 7.4 失败分类

任务失败必须区分 invalid_task、action_space_failure、budget_insufficient、explorer_failure、critic_recovery_failure、answer_extraction_failure 和 system_failure。不得通过无限增加 Critic 轮数或预算掩盖失败。

## 8. 实验步骤与计划文件索引

| 步骤 | 名称 | 必读计划文件 | 当前状态 |
|---|---|---|---|
| SP0 | 实验空间、协议与数据契约冻结 | 01_SP0_protocol_workspace_and_data_contract.md | 已完成 PASS（2026-08-22） |
| SP1 | 原 PoG 决策点适配与最小环境绑定 | 02_SP1_pog_adapter_and_environment_binding.md | 已完成 PASS（2026-08-22） |
| SP2-A | 真实 KG 环境验证 | 03_SP2A_live_kg_environment_validation.md | 主实验已完成 PASS（2026-08-22） |
| SP2-A-SUPPLEMENT | SP2-A 补充：TAIL 正向语义与动态多跳验证 | 03A_SP2A_supplement_tail_and_dynamic_multihop.md | 已完成 PASS（2026-08-22） |
| SP2-B | 无 Self-Play Experience Memory 的 LLM+KG 端到端基线 Rollout | 04_SP2B_llm_kg_baseline_rollout.md | 已登记，启动准备中，尚未运行 |

后续步骤不得只根据口头讨论直接实施。决定实际启动新阶段时，应先在本文件中明确阶段名称、状态、前置依赖和允许工作，并在该阶段的启动准备期间生成和登记对应计划文件。上一阶段的完成和报告生成不以该计划文件已经存在为条件。SP2-A 主实验与补充实验均已 PASS 并收口；SP2-B 计划现已登记，但在完成启动前检查、配置和任务 registry 冻结前，不得进行正式 rollout。SP2-B 运行期间不得注入 memory，也不得运行 WebQSP 150/CWQ 50 正式效果评测。

## 9. 预期实验顺序与 LLM/KG 使用边界

后续实验必须遵循“先验证协议与环境，再建立无 memory 在线基线，然后生成和验证经验，最后进行冻结 memory 的正式评测”的顺序。不得为了提前观察效果而跨过接口、隔离、重放或 promotion 阶段。以下阶段名称和顺序是总体安排；每个阶段的具体任务、模型、prompt、预算、数据和验收门槛必须在实际运行前形成可追溯记录。独立计划文件应在决定启动该阶段后的启动准备期间制定和登记，但不属于上一阶段结束时必须生成的产物。

| 阶段 | LLM 使用 | KG 使用 | memory 状态 | 主要实验目的 | 允许使用的评测数据 |
|---|---|---|---|---|---|
| SP0 | 不调用 | 不要求 live KG | 不生成、不使用 | 冻结协议、空间、数据契约和固定评测集 | 仅构建并校验固定集，不运行模型 |
| SP1 | 不调用真实 LLM；可用预制输出检查离线解析 | fixture 或 recorded I/O；live Freebase 不作为验收门槛 | 不生成、不使用 | 验证 PoG 状态投影、动作语义、最小 Environment binding、预算和 O0 隔离 | 只做结构、哈希和泄漏检查，不运行效果评测 |
| SP2-A | 不调用 | 首次接入 live KG | 不生成、不使用 | 用预制合法动作验证真实 KG 查询、HEAD/TAIL 方向、异常分类、状态更新和计数 | 使用非评测 fixture 或预注册开发任务，不使用 20/150/50 生成轨迹 |
| SP2-B | 首次正式调用 | 调用 live KG | 不生成、不读取 Self-Play Experience Memory；允许原 PoG 题内工作记忆 | 建立无 Self-Play Experience Memory 的 LLM+KG 端到端 Explorer/原 PoG 在线基线，验证动作合法性、终止、轨迹保存和重放 | 先使用独立开发任务；流程稳定后只允许用 WebQSP smoke 20 做冒烟检查 |
| SP3 | 调用 | 调用 | 只生成候选经验，不得注入正式推理 | 在独立 discovery 任务上运行 Explorer、Critic、Oracle 和 Verifier，生成可审计、可重放的候选 Self-Play 经验 | 不得使用冻结的 WebQSP 20/150 或 CWQ 50；必须通过 exclusion registry 排除重叠 |
| SP4 | 按预注册验证流程调用 | 按反事实和重放需要调用 | 候选经验经过反事实验证、蒸馏和 promotion，形成冻结 memory | 判断经验是否真实有效、可泛化、无 Oracle 依赖，并冻结 memory、检索配置和阈值 | 只使用独立 validation 数据，不得根据 20/150/50 结果修改 memory |
| SP5 | 调用并冻结模型与 prompt 配置 | 调用并冻结搜索预算 | 只读冻结 memory | 比较原 PoG/无 memory 基线与 PoG+Self-Play memory，判断是否增强推理 | WebQSP model-compare 150 与 CWQ model-compare 50 分别正式评测；WebQSP smoke 20 仅作运行前冒烟检查 |

### 9.1 首次 live KG 实验

SP2-A 主实验已完成基础 live KG 验证，补充实验已补齐 TAIL 正向语义和真实返回驱动的动态两跳证据。SP2-A 现已具备 SP2-B 的环境前置条件，SP2-B 计划 `04_SP2B_llm_kg_baseline_rollout.md` 已生成并登记。SP2-A 及其补充实验只使用预制或计划内合法动作直接调用 Environment，不让 LLM 决定关系或答案；SP2-B 才首次允许 LLM 参与关系和答案相关决策。

SP1 中使用的人工 fixture 或已有 recorded I/O 只用于验证接口形状和确定性，不算作正式 live KG 实验，也不得作为后续 memory 的有效经验来源。

### 9.2 首次 LLM+KG 联合实验

首次正式的 LLM+KG 联合 rollout 应发生在 SP2-A PASS 之后的 SP2-B。该阶段必须保持 memory 关闭，先建立可复现的无 memory 基线，并按“少量手工核查任务 -> 独立开发任务 -> WebQSP smoke 20”的顺序扩大规模。Actor/Explorer 只能读取 O0 信息；评测标签只能由 Verifier 读取。

SP2-B 的主要结论是完整推理链能否合法运行、终止、保存和重放，而不是 Self-Play Experience Memory 是否提升 EM/F1。原 PoG 题内工作记忆可以作为当前题目的临时状态，但不得跨题或跨 Run 复用。发现接口错误、答案泄漏、不可重放或未分类系统异常时，不得进入经验生成阶段。

### 9.3 Self-Play 经验生成和使用顺序

候选经验只能在无 memory 的在线基线稳定后，于 SP3 的独立 discovery 数据上生成。SP3 产物必须先作为 candidate experience 保存，不能在同一阶段直接作为正式 memory 注入 Explorer。Oracle 信息只能按照第 5 节权限用于任务构造、验证或离线监督，不得进入 Actor/Critic 的 O0 视图。

SP4 必须先完成反事实比较、确定性重放、held-out validation、伤害率统计、蒸馏和 promotion，之后才能冻结 memory。未通过 promotion 的经验不得进入 SP5。SP5 测试期间 memory、prompt、模型、检索配置、阈值和预算均须只读冻结，不得根据 20/150/50 的结果回改。

### 9.4 阶段编号与启动登记规则

SP2-A 与 SP2-B 可以在后续设计时登记为两个独立步骤，也可以作为同一个 SP2 工作阶段中的两个具有独立验收门槛的子阶段；采用何种形式应在实际启动 SP2 相关工作前明确并登记，不要求在 SP1 实验结束或报告生成时决定。无论采用何种编号，均不得跳过上述先后依赖。后续研究证据要求调整阶段边界时，应先升级本总体文件并记录理由、证据和对可比性的影响。

## 10. 阶段收口与后续启动流程

当前阶段实验工作结束时，必须先完成阶段收口；之后是否以及何时启动新阶段，由后续研究安排另行决定。具体流程如下：

1. 在当前步骤计划文件的实验日志区补全运行、异常、指标、证据路径和结论；
2. 明确结论为 PASS、CONDITIONAL PASS 或 FAIL；
3. 生成当前阶段实验报告，保存到 `self-play/reports/<stage-id-lower>/`，并冻结报告所引用的关键配置、数据、运行和产物哈希；
4. 在本文件步骤索引和阶段历史中登记当前阶段结论、报告路径、收口日期及未解决风险；
5. 当前阶段至此完成收口，不要求生成下一阶段实验计划；
6. 后续决定启动新阶段时，再更新本文件的当前阶段、启动条件和允许工作；若改变研究问题、Oracle 权限、数据边界或判定原则，先升级本文件版本；
7. 完成新阶段启动登记后，方可开始该阶段的代码实现或运行。

下一阶段计划不是上一阶段实验报告的组成部分，也不是上一阶段验收或收口条件。决定启动新阶段后，必须在该阶段启动准备期间制定对应计划，并在代码实现或实验运行前完成登记。

CONDITIONAL PASS 只能用于不影响核心有效性、隔离性和复现性的次要问题。Oracle 泄漏、split 污染、不可重放、源数据被修改、关键指标缺失或配置不可复现时不得条件通过。

## 11. 计划、日志和实验报告维护规则

- 每个步骤只设一个主计划文件，重大变化通过版本升级记录；
- 计划前半部分是预注册内容，实验开始后不得无记录地修改假设、指标或门槛；
- 每次实现或运行后，立即向对应计划文件末尾追加记录；
- 日志只追加，不覆盖失败记录；错误运行标记 INVALID 并说明原因；
- 计划变更记录时间、前后内容、原因、证据和可比性影响；
- 每个阶段实验结束后必须生成一份主实验报告，默认路径为 `reports/<stage-id-lower>/<STAGE-ID>_experiment_report.md`；
- 阶段报告必须基于已记录日志和冻结产物汇总，不得用报告覆盖、改写或删除原始运行证据；
- 阶段报告必须同时报告有效结果、无效运行、失败分类、未解决风险和最终验收结论；
- 报告路径和文件 SHA-256 必须写入本文件的阶段历史或对应阶段收口记录；
- 结论必须由已记录产物和指标支持。

## 12. 总体成功标准

只有同时满足下列条件，才可声称 Self-Play 经验记忆增强了 PoG 推理：

1. 轨迹、动作和答案判定合法、可重放且无 Oracle/test 泄漏；
2. 冻结经验在未参与经验生成的任务上有效；
3. 至少一个局部决策阶段出现可解释、可重复的改善；
4. EM/F1 提升，或准确率基本持平且搜索成本显著下降；
5. 真实经验优于内容与成本匹配的对照；
6. 效果不由单个实体、单种问题或少量异常样本主导；
7. 负面影响、失败边界和未解决问题被完整报告。

## 13. 总体文件变更记录

| 日期 | 版本 | 修改内容 | 修改原因 | 影响步骤 |
|---|---|---|---|---|
| 2026-08-21 | 1.0 | 建立总体要求并登记 SP0 | 为 self-play/ 独立实验空间建立统一治理和推进规则 | SP0 及后续步骤 |
| 2026-08-21 | 1.1 | 明确 self-play/ 下现有代码为原 PoG 基线；增加当前阶段和阶段切换更新要求 | 与实际代码组织和实验推进方式一致，避免把 memory 实验误写成重新接入原 PoG | SP0 及后续步骤 |
| 2026-08-21 | 1.2 | 增加 WebQSP/CWQ 的随机抽样、冒烟测试和模型对比测试规模约束 | 固定不同用途的评测样本协议，保证后续模型、原 PoG 基线和 memory 对照使用可复现且一致的题目清单 | SP0 及后续步骤 |
| 2026-08-21 | 1.3 | 明确 20 条 WebQSP 仅用于冒烟测试，150 条 WebQSP 与 50 条 CWQ 用于不同模型对比测试，并要求三类清单分别冻结和报告 | 根据补充说明消除不同测试用途之间的样本量歧义 | SP0 及后续步骤 |
| 2026-08-21 | 1.4 | 将随机抽样明确限定为 SP0 阶段的一次性固定数据集构建；规定后续运行只读取冻结数据集和 manifest，不得每次测试重新抽样或自动补题 | 澄清实验执行方式，保证不同运行、模型和 memory 对照使用完全相同的测试题目 | SP0 及后续步骤 |
| 2026-08-21 | 1.5 | 将测试数据的具体形成要求下沉到 SP0 计划文件；总体文件仅保留评测数据必须预先冻结、同用途对照必须使用相同题目和分别报告的上位原则 | 避免总体要求与阶段计划重复规定具体样本量、文件名和构建步骤，明确阶段计划是测试数据实施细则的承载位置 | SP0 及后续步骤 |
| 2026-08-22 | 1.6 | SP0 验收 PASS；登记 SP1 计划文件并切换当前阶段 | 完成协议与固定评测集冻结后，按当时第 9 节（现第 10 节）强制流程进入下一步计划，但暂不实施 SP1 代码 | SP0、SP1 |
| 2026-08-22 | 1.7 | 增加预期实验顺序与 LLM/KG 使用边界，规定 SP2-A 首次接入 live KG、SP2-B 首次进行无 memory 的 LLM+KG rollout、SP3 生成候选经验、SP4 验证蒸馏与 promotion、SP5 使用冻结 memory 正式评测 | 防止接口验证、在线基线、经验生成和效果评测混在同一阶段，并明确 WebQSP 20/150 与 CWQ 50 的允许使用时机 | SP1 及后续步骤 |
| 2026-08-22 | 1.8 | 要求每个阶段实验结束后在 `reports/<stage-id-lower>/` 生成主实验报告并登记路径/hash；取消把生成下一阶段计划作为上一阶段验收、报告或收口条件 | 将阶段结果沉淀与后续研究规划解耦，保证每阶段先形成可审计报告，同时避免在尚未决定启动下一阶段时被迫提前制定计划 | SP1 及后续步骤 |
| 2026-08-22 | 1.9 | SP1 验收 PASS 并完成阶段收口；当前阶段改为阶段间等待，不启动 SP2-A | E1.1–E1.12 通过；报告 `reports/sp1/SP1_experiment_report.md` | SP1 |
| 2026-08-22 | 1.10 | 登记 SP2-A 计划 `03_SP2A_live_kg_environment_validation.md`，将当前总体阶段切换为 SP2-A，并修正 SP1 报告实际 SHA-256 | SP1 已完成 fixture 级协议验证；下一步需要真实 KG 环境证据。SP2-A 仍不调用 LLM、不使用 memory、不进行正式 KGQA 效果评测 | SP2-A |
| 2026-08-22 | 1.11 | SP2-A 验收 PASS 并完成阶段收口；当前阶段改为阶段间等待，不启动 SP2-B | E2A.1–E2A.7 通过；有效 run `sp2a-20260822T082704Z-28a5bc97`；报告 `reports/sp2a/SP2A_experiment_report.md` | SP2-A |
| 2026-08-22 | 1.12 | 将当前阶段切换为 SP2-A 补充实验，登记 `03A_SP2A_supplement_tail_and_dynamic_multihop.md`，暂缓 SP2-B | 主 SP2-A 已验证基础 live KG 环境，但 TAIL 正向查询和真实返回驱动的动态两跳证据不足，且报告 SHA-256 仍为 `PENDING` | SP2-A-SUPPLEMENT |
| 2026-08-22 | 1.13 | SP2-A 补充实验验收 PASS 并完成重新收口；当前阶段改为阶段间等待，不启动 SP2-B | S2A-S.1–S.4 与 replay 通过；有效 run `sp2a-supp-20260822T111116Z-79aa8ea8`；报告 `reports/sp2a/SP2A_experiment_report.md` SHA-256 `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc` | SP2-A-SUPPLEMENT |
| 2026-08-22 | 1.14 | 登记 SP2-B 无 Memory 的 LLM+KG 端到端基线计划，并将当前阶段切换为 SP2-B 启动准备 | SP2-A 主实验与补充实验均已 PASS 并收口；后续必须先建立可审计的无 memory 在线基线，再进入 SP3 经验生成 | SP2-B |
| 2026-08-22 | 1.15 | 将 SP2-B 细化为启动检查、B0 人工核查、B1 独立开发任务、B2 WebQSP smoke 20 和阶段收口五步；区分原 PoG 题内工作记忆与 Self-Play Experience Memory | `main_freebase.py` 使用题内 `mem` 文件完成当前问题的工作状态，不能将其与待研究的跨题经验记忆混同；需要在保持原 PoG 行为的同时建立可审计的无 Self-Play Experience Memory 基线 | SP2-B |
