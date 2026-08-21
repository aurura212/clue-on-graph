# PoG Self-Play 经验记忆：独立实验方案

> 版本：SP-V2.0
>
> 制定日期：2026-08-21
>
> 适用项目：SRP_2/clue_on_graph/PoG
>
> 当前状态：方案冻结前；尚未实现、尚未运行
>
> 方案性质：独立研究路线，不依赖其他记忆实验的结果或门槛

## 0. 方案重置声明

从本版本开始，Self-Play 被视为一条独立实验路线。此前其他记忆方案的实验结果不作为本方案的：

- 启动条件；
- 方法设计依据；
- 超参数来源；
- 对照组；
- 开发集选择依据；
- 停止或继续依据；
- 论文效果结论。

旧实验产物只保留为项目档案，不进入 Self-Play memory 构建、检索、prompt、阈值选择和结果汇总。本方案从原始 PoG 推理流程出发，独立回答以下问题：

> 从受限 KG 环境中的 Self-Play 轨迹提取“状态-动作-结果”经验，能否改善 PoG 的关系探索、继续/停止和回溯决策？

本方案不预设某个干预点有效，也不预先排除 relation selection。三个决策阶段必须分别验证，联合方法只有在单阶段机制成立后才能运行。

## 1. 核心思想

Self-Play 不再生成“某类实体通常拥有哪些关系”的静态结构统计，而是生成可验证的过程经验：

~~~text
问题与答案类型
+ 当前搜索状态
+ 已探索和失败的路径
+ 当前候选动作
+ 执行动作后的新增证据、成本与最终结果
= 可迁移的 procedural memory
~~~

记忆的基本语义不是“这条 KG 路径存在”，而是：

> 在相似问题和相似搜索状态下，执行某类动作相对于其他可行动作是否更可能取得进展或得到正确答案。

## 2. 研究问题与假设

### 2.1 研究问题

1. Self-Play 能否在不使用 benchmark test 标注和轨迹的条件下生成可重放的 KGQA 搜索经验？
2. 经验能否跨实体、跨问题表述和跨局部子图迁移？
3. 经验是否能改善 relation、continue/stop 或 backtrack 中至少一个阶段的决策？
4. 有效性是否来自记忆内容，而不是 prompt 变长、额外调用或普遍增加搜索？
5. 反事实验证的失败恢复经验是否优于成功轨迹缓存？

### 2.2 假设

- **SP-H1：轨迹可学习。** 不同 rollout 中存在可重复的成功动作模式和失败恢复模式。
- **SP-H2：状态条件化有效。** 同时使用问题意图、搜索状态和候选动作的经验优于只按实体类型或关系文本检索。
- **SP-H3：结果验证有效。** 经过确定性 replay 和反事实比较的经验优于未经验证的 raw trajectory。
- **SP-H4：失败恢复有效。** failure-recovery memory 对困难搜索状态的价值高于 success-only memory。
- **SP-H5：阶段可归因。** 至少一个局部决策指标的改善能够解释最终 EM/F1 的变化。
- **SP-H6：内容真实有效。** 真实记忆优于 shuffle、token-matched irrelevant 和等成本搜索对照。

## 3. 研究边界

### 3.1 第一阶段要做

- 在固定 KG snapshot 上生成合成 KGQA 任务；
- 使用受限动作接口运行 Explorer 和 Critic；
- 使用确定性 Verifier 重放轨迹和比较反事实动作；
- 将轨迹蒸馏为不含实体 ID、答案和事实文本的程序性经验；
- 在 held-out entity、held-out task 和 held-out path signature 上验证；
- 将经验分别接入 PoG 的 relation、continue/stop 和 backtrack 阶段；
- 使用原始 PoG、内容对照和轨迹形式对照进行评测。

### 3.2 第一阶段不做

- 不微调 LLM 参数；
- 不做强化学习或梯度更新；
- 不进行测试时在线 memory 写入；
- 不允许 Explorer/Critic 读取答案、gold path、gold SPARQL 或隐藏 reward；
- 不允许 Actor 发任意 SPARQL；
- 不将 benchmark test 问题或测试运行轨迹用于 memory 构建；
- 不把单条成功轨迹直接晋升为持久记忆；
- 不让记忆直接提供答案实体或事实三元组。

## 4. 系统架构

### 4.1 建议模块

~~~text
PoG/self_play/
├── __init__.py
├── env.py
├── task_generator.py
├── action_schema.py
├── state_encoder.py
├── actor.py
├── critic.py
├── runner.py
├── verifier.py
├── counterfactual.py
├── distiller.py
├── memory_store.py
├── retrieval.py
├── injection.py
└── analysis.py
~~~

| 模块 | 职责 |
|---|---|
| env.py | 固定 KG 环境、预算、状态转移和动作执行 |
| task_generator.py | 合成任务、自然语言问题、过滤与 split |
| action_schema.py | 统一 relation、continue、stop、backtrack 动作 |
| state_encoder.py | 生成可迁移且无实体 ID 的状态签名 |
| actor.py | Explorer policy、prompt 和输出解析 |
| critic.py | 识别失败原因并提出受控替代动作 |
| verifier.py | 确定性重放、答案验证、预算和泄漏检查 |
| counterfactual.py | 在同一状态和预算下比较候选动作结果 |
| distiller.py | 聚类轨迹并生成经验候选 |
| memory_store.py | 经验晋升、版本、索引与 provenance |
| retrieval.py | question-state-action 条件检索与门控 |
| injection.py | 分阶段接入 PoG，不改变其他阶段 |
| analysis.py | 阶段指标、归因、伤害和成本分析 |

### 4.2 数据流

~~~text
固定 KG snapshot
  -> 合成任务和隐藏 oracle
  -> Actor 多 seed rollout
  -> Critic 受控修正
  -> Verifier 确定性 replay
  -> Counterfactual paired comparison
  -> Distiller 生成经验候选
  -> held-out validation 与 promotion
  -> PoG 分阶段检索和干预
  -> fresh development / final evaluation
~~~

## 5. 合成任务

### 5.1 任务生成

每个任务由环境侧生成，Actor 不接触生成过程：

1. 从允许的 source entity 池采样起点；
2. 在 KG 中采样长度 1 至 4 的可执行路径；
3. 执行路径得到非空且规模受控的答案集合；
4. 可选加入时间、类型、比较或交集约束；
5. 将路径和约束转写为自然语言 information need；
6. 检查问题不包含 relation ID、答案名和显式路径；
7. 由 Verifier 保存隐藏 oracle 和 witness；
8. 对任务做去重、歧义过滤和难度分层。

### 5.2 难度分层

每个任务记录：path length、每跳 branching、CVT 数量、逆向边数量、约束类型、可行替代路径数量、relation lexical ambiguity、answer set size，以及最短路径与采样路径是否一致。

正式报告至少区分：1-hop、2-hop、3/4-hop、low/high branching、with/without constraint。

### 5.3 数据划分

~~~text
sp_discovery_v1    生成 rollout 和经验候选
sp_validation_v1   选择 promotion threshold 和检索配置
sp_test_v1         只做一次离线泛化评测
~~~

三组必须满足：

- source entity 不重叠；
- answer entity 不重叠；
- task ID 和 question paraphrase 不重叠；
- 额外报告 path-signature-held-out 子集；
- split 在 rollout 前生成并保存 hash；
- sp_test_v1 在配置冻结前不可读取结果。

### 5.4 Benchmark 隔离

建立 benchmark exclusion registry，至少包含所有已用于开发或评测的 topic entity、answer entity、question 文本及规范化哈希，以及项目中可见的 gold path/SPARQL。

这些内容不得进入 synthetic source/answer pool。经验中不得保存实体名、实体 ID、具体答案或完整问题文本。

## 6. Self-Play 环境

### 6.1 Actor 可见状态

~~~text
question
source entity name and type
current frontier
observed candidate relations
observed triples/path summaries
failed or exhausted branches
remaining depth/step/KG-call/LLM-call budget
last action and deterministic result
~~~

### 6.2 Actor 不可见状态

~~~text
answer entity IDs
gold relation path
gold SPARQL
hidden witness
shortest path
future KG neighborhood
reward decomposition
counterfactual result
~~~

### 6.3 动作空间

~~~text
EXPAND(entity, relation, direction)
SELECT_FRONTIER(entity)
BACKTRACK(entity)
CONTINUE
STOP(answer_candidates)
ABSTAIN(reason_code)
~~~

动作参数必须来自当前已观察集合。非法 relation、未观察实体和任意查询字符串一律拒绝，并记录为 protocol violation。

### 6.4 建议初始预算

~~~yaml
max_depth: 4
max_steps: 12
max_kg_calls: 16
max_llm_calls: 8
max_critic_rounds: 2
max_frontier_size: 80
max_candidates_per_relation_step: 40
~~~

预算只允许在 sp_validation_v1 上调整一次，冻结后不得根据 benchmark 结果修改。

## 7. 角色与轨迹生成

### 7.1 Explorer

Explorer 根据可见状态选择下一动作，不得到 oracle 反馈。每个 task 建议运行：

- 1 条 deterministic actor-only trajectory；
- 3 至 5 条不同 seed/temperature 的 exploratory trajectory；
- 每条失败轨迹最多 2 轮 Critic correction。

### 7.2 Critic

Critic 只在以下条件触发：STOP 后答案验证失败；预算即将耗尽且没有新证据；连续动作未增加 frontier 或可用 triple；重复进入同一状态签名；branching 激增且搜索目标未收敛。

Critic 输出 failure_type、visible evidence、proposed action type、visible target 和 confidence。Critic 不能直接写答案，也不能引用隐藏 oracle。

### 7.3 Deterministic Verifier

Verifier 不使用 LLM 判断事实，负责：

- 重放每一步动作；
- 检查候选 relation/entity 是否当时可见；
- 检查预算；
- 验证提交答案；
- 计算新增相关 triple 和最终成功；
- 检查轨迹是否依赖隐藏信息；
- 输出 replay hash。

### 7.4 反事实分支

对关键决策状态，固定此前轨迹和剩余预算，仅替换一个动作：

~~~text
same state + action_a -> outcome_a
same state + action_b -> outcome_b
~~~

比较是否最终成功、是否发现目标相关新 triple、KG/LLM calls、无效 expansion、循环、提前停止和过度继续。不能执行配对反事实的轨迹只能作为 raw evidence，不能产生高置信 action recommendation。

## 8. 状态、动作与结果表示

### 8.1 状态签名

状态签名不得包含具体实体 ID。建议字段：decision_stage、question_intent、expected_answer_type、current_entity_types、depth_bucket、budget_bucket、frontier_type_multiset、explored_relation_signatures、failed_route_signatures、candidate_action_signatures、branching_bucket 和 progress_state。

question_intent 和 expected_answer_type 必须由独立编码器从问题中得到，不能由 gold path 反推。

### 8.2 动作签名

- relation：方向、relation token/embedding、source/target type compatibility；
- continue/stop：继续、停止或 abstain；
- backtrack：候选实体类型、首次出现深度、历史进展、剩余未尝试动作摘要。

### 8.3 Outcome

每个 outcome 至少记录 success、answer_f1、new_relevant_triples、step/KG/LLM cost、loop_created、premature_stop、wasteful_continuation 和 counterfactual_uplift。

## 9. 经验记忆

### 9.1 经验类型

| 类型 | 语义 |
|---|---|
| relation_preference | 相似状态下某类 relation action 的相对效用 |
| continue_rule | 哪些状态仍值得继续 |
| stop_rule | 哪些状态继续搜索通常无益或有害 |
| backtrack_rule | 应回到哪类 frontier 状态 |
| failure_recovery | 特定失败模式下经验证有效的修正动作 |
| avoidance_rule | 容易形成循环、高成本或错误答案的动作模式 |

### 9.2 经验记录

每条记录必须包含 experience_id、memory_version、decision_stage、state_pattern、recommended/discouraged action patterns、support_tasks、support_entities、paired_counterfactual_n、success_rate、baseline_success_rate、counterfactual_uplift、mean_cost_delta、harmful_rate、confidence_lower_bound、heldout_validation、provenance_hashes 和 status。

### 9.3 晋升门槛

初始建议阈值如下，最终值只能在 sp_validation_v1 上调整一次：

- support_tasks >= 8；
- support_entities >= 5；
- paired_counterfactual_n >= 5；
- success uplift >= 0.10，或成功率持平且平均成本下降 >= 15%；
- harmful rate <= 0.15；
- confidence lower bound >= 0.55；
- discovery 与 validation 的效应方向一致；
- entity ID、答案文本和 oracle leakage 检查全部通过；
- replay 成功率为 100%。

任一条件不满足则保留为 candidate 或 rejected，不进入在线检索。

## 10. 检索与干预

### 10.1 检索键

检索必须同时使用 decision_stage、question intent、expected answer type、current state signature、candidate action compatibility 和 budget/progress state。不得只按实体类型、relation 名称或全局成功率检索。

### 10.2 检索分数

~~~text
retrieval_score
  = 0.30 * question_similarity
  + 0.25 * state_similarity
  + 0.20 * action_compatibility
  + 0.15 * confidence_lower_bound
  + 0.10 * normalized_uplift
  - conflict_penalty
~~~

所有分量写入 trace。若高分经验互相冲突，或最高分低于阈值，则回退原始 PoG，不注入内容。

### 10.3 三个独立干预点

1. **Relation memory**：只对当前 KG 已返回的 relation 候选提供经验分或简短经验摘要，不增加不存在的 relation。
2. **Continue/stop memory**：在是否继续搜索前提供相似状态的结果统计，不直接强制输出继续或停止。
3. **Backtrack memory**：对当前可见 frontier 候选提供历史动作效用，不加入不可见实体。

初始实验必须分别运行，禁止一开始将三种干预合并。

### 10.4 Prompt 约束

Prompt 只允许出现抽象经验统计，不得出现具体答案、benchmark 问题、实体 ID、完整训练轨迹或“必须选择某动作”的命令。

## 11. 实验组

### 11.1 主实验

| ID | 配置 | 目的 |
|---|---|---|
| P0 | 原始 PoG，无 Self-Play memory | 主基线 |
| PR | relation experience only | 测 relation 决策 |
| PC | continue/stop experience only | 测搜索终止决策 |
| PB | backtrack experience only | 测回溯决策 |
| PALL | 已通过门槛的单阶段模块组合 | 测互补性 |

PALL 只有在至少两个单阶段组分别通过阶段级机制门槛后运行。

### 11.2 内容对照

| ID | 配置 | 排除因素 |
|---|---|---|
| PSHUFFLE | 打乱 state 与 experience 配对 | 检验真实匹配内容 |
| PIRRELEVANT | 等 token、等格式的无关经验 | 检验 prompt 长度和格式 |
| PRANDOM | 等概率随机动作建议 | 检验一般探索变化 |
| PCOST | 无记忆，但匹配 calls/token/搜索预算 | 检验额外计算量 |

### 11.3 记忆形式对照

| ID | 配置 | 目的 |
|---|---|---|
| PRAW | 检索 raw successful trajectory | 检验蒸馏是否必要 |
| PSUCCESS | 只用成功经验 | 检验失败恢复价值 |
| PNOCF | 不做 counterfactual validation | 检验反事实验证价值 |
| PSTATELESS | 去掉搜索状态，只按问题检索 | 检验状态条件化价值 |

主报告至少包含 P0、最佳单阶段组、对应 PSHUFFLE、PIRRELEVANT、PCOST、PRAW 和 PNOCF。

## 12. 新评测划分

### 12.1 Development

创建新的 sp_benchmark_dev_v1：

- 从 benchmark train/dev 中预注册抽样；
- 建议 200 题；
- 按 hop、约束和基线难度分层；
- 不使用任何旧实验切片的题级结果选题；
- 用于选择 retrieval top-k、threshold 和 prompt 压缩方式。

### 12.2 Final

创建新的 sp_benchmark_final_v1：

- 必须来自未参与本方案开发和调参的题目池；
- 建议至少 300 题，预算允许时使用完整正式测试集；
- 在方法、代码、prompt、memory、阈值和分析脚本冻结后一次性运行；
- 运行前登记题目文件 hash、seed、模型版本和计划比较组；
- 如果历史运行已暴露某题结果，则该题不能被称为 final unseen，应替换或单列为 exposed evaluation。

### 12.3 Transfer

主实验完成后，冻结同一份 memory，在另一个 KGQA 数据集上评测。迁移实验不得重新蒸馏或使用目标测试集做 promotion。

## 13. 分阶段执行

### SP0：协议与数据冻结

固定 KG snapshot；定义动作、状态和 outcome schema；建立 benchmark exclusion registry；生成 synthetic split 和 benchmark split；保存全部 hash；完成 oracle 隔离单元测试。

验收：split 无交叉污染；非法动作被拒绝；Actor 输入零 oracle 字段。

### SP1：环境与任务生成器

实现受限 KG 环境；生成分层 synthetic tasks；验证问题自然语言质量、答案可判定性；完成 100 题无 LLM 的 deterministic replay smoke。

验收：任务可执行率 >= 95%，replay 一致率 100%。

### SP2：Rollout 与 Critic

在 sp_discovery_v1 运行 actor-only 和多 seed rollout；对失败/停滞轨迹触发 Critic；保存可见状态、动作、成本和结果；检查角色上下文隔离。

验收：形成足量成功、失败、恢复三类轨迹；protocol violation 率为 0。

### SP3：反事实与经验蒸馏

选择关键状态生成 paired branches；运行 deterministic counterfactual replay；聚类 state-action-outcome；生成 candidate memory；在 sp_validation_v1 完成 promotion；冻结 sp-memory-v1。

验收：至少一个决策阶段存在跨实体可复现的正 uplift；否则停止，不接入 benchmark PoG。

### SP4：PoG 单阶段接入

实现 PR、PC、PB 三种互斥模式；memory off 时与 P0 输入和行为等价；每次检索、匹配、分数、选择和 outcome 全部进入 trace；运行每组 20 题 smoke。

验收：阶段隔离成立，无候选集合污染，无答案泄漏，无 timeout 系统差异。

### SP5：Development 机制实验

在 sp_benchmark_dev_v1 依次运行 P0、PR/PC/PB、对应内容对照，以及 raw、success-only、no-counterfactual、stateless ablation。

只有阶段级机制和最终指标均满足门槛的模块才可进入最终评测。开发集只允许一次阈值选择轮和一次冻结确认轮。

### SP6：Final Evaluation

冻结 code hash、memory hash、prompt、模型版本、temperature、seed、retrieval 配置、实验组和分析脚本后，一次性运行 sp_benchmark_final_v1。

Final 不允许中途改配置后重跑并替换结果。任何修订必须升级方案版本并使用新的 final 集。

### SP7：组合与迁移

只有至少两个单阶段模块在 Final 中保持正向机制证据时，才能运行 PALL。随后再进行跨数据集迁移和可选 policy learning；Self-Play 经验检索未通过时不启动 RL。

## 14. 指标

### 14.1 Self-Play 生成质量

- task executable rate；
- actor success rate；
- critic recovery rate；
- deterministic replay rate；
- paired counterfactual coverage；
- oracle leakage/protocol violation；
- 每成功经验的 KG/LLM 成本。

### 14.2 经验质量

- support tasks/entities；
- held-out success uplift；
- harmful rate；
- confidence lower bound；
- entity-held-out 和 signature-held-out retention；
- retrieval coverage、precision 和 conflict rate。

### 14.3 Relation 阶段

- gold relation candidate recall；
- gold selected recall；
- MRR、top-1/top-k；
- helpful/harmful reorder；
- LLM 实际选择是否响应经验排序；
- relation 干预后发现目标相关 triple 的比例。

### 14.4 Continue/Stop 阶段

- premature stop rate；
- wasteful continuation rate；
- continue 后新增相关 triple 的比例；
- continue 后最终成功率；
- stop 决策 precision；
- 剩余预算利用率。

### 14.5 Backtrack 阶段

- effective backtrack rate；
- 选中候选的后续成功率；
- 回到重复/死路状态的比例；
- backtrack 后新增相关 triple；
- 每次有效恢复的额外成本。

### 14.6 最终指标

- EM、F1；
- paired question-level win/loss；
- LLM calls、KG calls、tokens、latency；
- 搜索深度、expanded entities、无效 expansion；
- timeout、parse failure 和 fallback rate。

统计报告使用 paired bootstrap confidence interval；二元正确性可补充 McNemar 检验。开发阶段至少报告三次独立 seed，最终阶段采用预注册 seed 或多 seed 均值，不能按最好 seed 报告。

## 15. 阶段门槛

### 15.1 单阶段通过条件

PR、PC 或 PB 必须同时满足：

1. 对应阶段的主要局部指标改善；
2. 最终 EM/F1 不下降，或在准确率持平时成本显著下降；
3. 真实记忆优于对应 shuffle 和 irrelevant control；
4. 真实记忆优于或明显更稳定于 raw trajectory；
5. harmful intervention 未超过冻结阈值；
6. 改善题中多数实际触发了该阶段的可见 memory；
7. 效果在至少两个难度子组中方向一致。

### 15.2 进入 Final 的条件

- 单阶段通过全部条件；
- 配置冻结；
- final 文件未被读取；
- 泄漏、trace 和 run checker 全部通过；
- 对照 token、预算和模型配置可比；
- 预注册主要指标和停止规则。

### 15.3 停止条件

出现以下任一情况，停止对应模块：

- 真实 memory 不优于 shuffle/irrelevant；
- held-out entity 上效应消失或反向；
- 经验分数与 LLM 实际动作无关；
- 改善只来自更多 calls、tokens 或普遍扩大搜索；
- harmful rate 超阈值；
- raw trajectory 已完全解释效果；
- replay 不稳定或发生 oracle 泄漏；
- 只有最终 EM 波动，没有阶段级机制证据。

停止某个模块不影响其他单阶段模块继续验证。只有各自独立通过后才考虑组合。

## 16. Trace、产物与复现

### 16.1 每次干预必须记录

每条 intervention trace 至少包含 question_id、decision_stage、state_signature_hash、retrieved_experience_ids、retrieval_components、prompt_visible、candidate_actions_before/after、llm_action、fallback_reason、post_action_new_triples、post_action_success 和 cost_delta。

### 16.2 Run 产物

~~~text
<run_dir>/
├── run_meta.json
├── split_manifest.json
├── prompt_manifest.json
├── memory_manifest.json
├── self_play_tasks_used.jsonl
├── self_play_trajectories.jsonl
├── counterfactual_pairs.jsonl
├── replay_report.json
├── promotion_report.json
├── intervention_trace.jsonl
├── stage_metrics.json
├── final_metrics.json
├── failure_cases.jsonl
├── leakage_report.json
└── cost_report.json
~~~

### 16.3 Manifest

必须记录代码 hash、KG snapshot、split hash、exclusion registry hash、模型版本、prompt 版本、memory 版本、temperature、seed、timeout、retry、预算、阈值、依赖版本和全部产物 SHA-256。

## 17. 必需测试

### 17.1 环境与动作

- 同一 snapshot 和动作得到一致结果；
- 任意 SPARQL 和未观察动作被拒绝；
- BACKTRACK 不能选择未观察实体；
- STOP 不能提交未观察候选；
- 预算耗尽后不再调用 KG/LLM；
- CVT 和方向归一化后仍可 replay。

### 17.2 数据与泄漏

- synthetic split 的 source/answer entity 不交叉；
- benchmark exclusion registry 生效；
- Actor/Critic 输入不含 oracle 字段；
- memory 不含实体 ID、答案和完整问题；
- validation/test 轨迹不参与 candidate distillation；
- final 结果在冻结前不可访问。

### 17.3 Memory 与 PoG

- promotion 不满足阈值时失败；
- 单实体重复 rollout 只计一个 entity support；
- memory off 与 P0 等价；
- empty/conflicting retrieval 正确 fallback；
- PR、PC、PB 不改变非目标阶段；
- control 的 token、候选数和预算匹配；
- 每次 intervention 可对齐后续 outcome。

## 18. 主要风险与应对

| 风险 | 应对 |
|---|---|
| 合成问题过于模板化 | 多种 verbalizer、人工抽检、paraphrase-held-out |
| Actor 从措辞猜出 relation | 隐藏 relation ID，检查 lexical shortcut |
| 经验只记住实体或路径 | 去实体化、entity-held-out、signature-held-out |
| 成功率来自更多搜索 | PCOST 等成本对照和无效 expansion 指标 |
| Critic 间接获得 oracle | 物理隔离上下文、请求日志和字段审计 |
| 反事实不可比较 | 固定状态、预算、KG snapshot 和随机种子 |
| Prompt 本身鼓励动作 | shuffle/irrelevant/random controls |
| 多阶段联合无法归因 | 先单阶段，再组合；trace 对齐每次干预 |
| 最终集被历史暴露 | exposure registry，暴露题不标为 unseen |
| 记忆冲突 | conflict detection，低置信时回退 P0 |

## 19. 创新点与主张边界

本方案的核心方法主张应限定为：

> 在受限 KG 环境中通过多轨迹 Self-Play、确定性重放和配对反事实验证，构建 question-state-action-conditioned procedural memory，并将其分阶段用于 PoG 的搜索决策。

需要由实验共同支持的创新点包括：

1. 隐藏 oracle 与 Actor/Critic 的严格隔离；
2. 从轨迹中学习动作效用，而不是缓存具体答案或 KG 事实；
3. 使用失败恢复和配对反事实估计 action uplift；
4. entity-held-out、paraphrase-held-out 和 path-signature-held-out 验证；
5. relation、continue/stop、backtrack 的分阶段因果归因；
6. raw、success-only、no-counterfactual、stateless 和内容对照。

在完成文献检索前，不声称“首个 KGQA Self-Play”或“首个 KGQA 经验记忆”。如果只有单阶段有效，应只主张该阶段；如果只降低成本而不提高正确率，应表述为搜索效率改进。

## 20. 当前执行顺序

本方案可从 SP0 独立启动：

~~~text
SP0 协议与数据冻结
  -> SP1 环境与合成任务
  -> SP2 多轨迹 rollout 与 Critic
  -> SP3 反事实验证、蒸馏与 promotion
  -> SP4 PR/PC/PB 单阶段接入
  -> SP5 fresh development 机制实验
  -> SP6 frozen final evaluation
  -> SP7 通过模块的组合与迁移
~~~

第一项实际工作应是完成 SP0，而不是运行 benchmark：先冻结动作协议、数据隔离、split、trace schema 和验收脚本，再开始 Self-Play 轨迹生成。
