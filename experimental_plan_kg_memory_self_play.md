# PoG KG-Memory Self-Play：具体实现与实验计划

> 版本：SP-V1.0  
> 制定日期：2026-08-20  
> 适用项目：`SRP_2/clue_on_graph/PoG`  
> 上位方案：`experimental_plan_kg_memory_v2.md` 的 V2-5  
> 当前状态：设计完成，尚未实现 Self-Play，尚未运行 Self-Play 实验

## 0. 方案定位与执行门控

本方案回答“如果后续允许启动 Self-Play，应当如何实现和验证”，不改变 V2 当前的执行顺序。

推荐路线为：

```text
独立离线环境 + KG-only 合成任务
    -> Explorer / Critic / Deterministic Verifier / Distiller
    -> 轨迹重放、反事实失败恢复和 held-out validation
    -> trajectory-derived reflection experience
    -> 只注入 PoG reflection Decision A/B
```

### 0.1 正式实验门控

只有同时满足以下条件，才允许执行 SP4-SP7：

1. V2 reflection-only 已在冻结的 `final_unseen` 上完成；
2. KG-only reflection evidence 优于 shuffled 和 token-matched irrelevant 对照；
3. Decision A 或 Decision B 至少一个出现可归因的阶段级改善；
4. harmful intervention 和效率退化未超过预设阈值；
5. provenance、witness replay、数据 split 和 trace 审计全部通过。

门控通过前：

- 不运行 Self-Play memory 对 benchmark QA 的正式实验；
- 不用 Self-Play 结果选择 `final_unseen`、prompt 或阈值；
- 不晋升 Self-Play 经验为 PoG 的正式持久记忆；
- 不启动 RL、在线参数更新或测试时 Self-Play；
- `hard150_v1` 仍只是 development stress slice。

SP0-SP2 的环境、任务生成器和重放基础设施可在单独批准并记入日志后准备，但不得读取 benchmark test 的问题、答案、gold path 或 gold SPARQL。当前文件只制定方案，没有实现代码或运行实验。

## 1. 研究问题与假设

### 1.1 核心问题

> 不使用 benchmark QA 轨迹和 gold 标注，由 KG-only 合成任务驱动的离线 Self-Play，能否产生经过 held-out entity/task 验证的 reflection experience，并与独立构建的静态 KG structural evidence 互补，从而增强 PoG 的继续、停止和回溯决策？

### 1.2 假设

- **SP-H1：经验可迁移。** discovery entity 上形成的搜索经验可迁移到未参与构建的同类型实体。
- **SP-H2：失败经验有效。** 经反事实回放验证的失败修正优于只保存成功轨迹。
- **SP-H3：抽象优于缓存。** 类型泛化经验优于 raw trajectory 和 raw KG neighborhood cache。
- **SP-H4：来源互补。** KG structural evidence 与 Self-Play experience 的组合优于任一来源。
- **SP-H5：内容有效。** 真实经验优于 shuffled、irrelevant 和 token-matched controls。
- **SP-H6：干预安全。** 经验不会明显增加错误 continuation、backtracking 或 stop。

## 2. 研究边界

本方案中的 Self-Play 是“受 KG 环境约束的离线搜索经验生成”，不是模型自由对话：

- Explorer 只通过受限动作接口观察和展开 KG；
- Critic 只看 Explorer 可见的状态和轨迹；
- Verifier 在角色上下文之外确定性执行动作、判定答案和重放路线；
- Distiller 将多个验证轨迹抽象为可迁移的 reflection experience；
- memory 只在 held-out entity/task 上验证后晋升。

明确不做：

- 不恢复 first-hop relation reranking；
- 不在 `relation_search_prune()` 中注入 Self-Play experience；
- 不允许 Explorer 发任意 SPARQL 或读取 KG 后端；
- 不向 Explorer/Critic 暴露答案、oracle path、最短路径或 reward 分解；
- 不使用 benchmark test 内容和测试轨迹构建经验；
- 不把 raw trajectory 写入现有 `kg_structural_memory.py`；
- 不将单条成功轨迹直接晋升；
- 第一阶段不训练模型、不做 RL、不在线演化 memory。

## 3. 总体架构

### 3.1 新增模块

```text
PoG/self_play/
├── __init__.py
├── env.py
├── task_generator.py
├── policy.py
├── runner.py
├── reward.py
├── trajectory_schema.py
├── verifier.py
├── distiller.py
├── experience_memory.py
├── retrieval.py
└── analysis.py
```

| 模块 | 职责 |
|---|---|
| `env.py` | 受限 KG 环境、状态、动作执行和预算 |
| `task_generator.py` | KG-only 合成任务、过滤和 split |
| `policy.py` | Explorer/Critic prompt 与动作解析 |
| `runner.py` | episode 调度、角色隔离和受控分支 |
| `verifier.py` | 确定性执行、答案验证和反事实回放 |
| `distiller.py` | 多轨迹聚类与经验候选生成 |
| `experience_memory.py` | 验证、晋升、版本和 provenance |
| `retrieval.py` | reflection-stage experience 检索 |

后续只按需修改 `PoG/utils.py`、`PoG/trace_utils.py`、`PoG/main_freebase.py` 和 reflection 分析脚本。初版禁止修改 `PoG/freebase_func.py` 的 first-hop relation score。

### 3.2 数据流

```text
KG snapshot -> synthetic tasks -> Explorer rollout -> Critic proposal
    -> deterministic verification/counterfactual replay
    -> verified trajectories -> distillation -> held-out promotion
    -> Self-Play experience -> PoG reflection Decision A/B
```

## 4. Self-Play 环境

### 4.1 状态隔离

完整状态分为 actor-visible 和 oracle-only。

**Actor-visible：**合成 information need、source entity 名称/ID/类型、当前实体、frontier、已展开路径、失败路线、访问计数、剩余 depth/step/KG-call/LLM-call 预算，以及最近动作的执行结果。

**Oracle-only：**隐藏目标实体集合、任务 witness、可接受答案、离线 path distance、替代路径和 reward 分解。该部分只允许 `env.py`、`verifier.py` 和离线 `reward.py` 读取，不能进入 Explorer、Critic 或 Distiller 的 LLM prompt。Actor loader 必须物理删除 `oracle` 字段。

### 4.2 动作空间

| 动作 | 参数 | 作用 | 约束 |
|---|---|---|---|
| `INSPECT_RELATIONS` | `entity_id`, `direction` | 查看候选 relation 摘要 | 返回受 top-k/branch cap 限制 |
| `EXPAND` | `entity_id`, `relation_id`, `direction` | 沿 relation 展开邻居 | relation 必须已经观察到 |
| `BACKTRACK` | `target_entity_id` | 回到历史 frontier | 目标必须存在于可见轨迹 |
| `VERIFY_ROUTE` | `path_ref` | 验证已走路线和答案候选 | 不能探测未执行的隐藏路径 |
| `STOP` | `answer_entity_ids` | 提交答案并结束 | 答案必须来自已观察实体 |
| `ABANDON` | `reason_code` | 以 unknown/budget/dead-end 结束 | 原因必须结构化 |

动作严格使用 JSON，例如：

```json
{
  "action": "EXPAND",
  "entity_id": "m.synthetic_source",
  "relation_id": "people.person.place_of_birth",
  "direction": "out"
}
```

### 4.3 执行约束

1. 所有动作经过 schema 和白名单校验，无法解析时记为 `invalid_action`。
2. Explorer 不能拼接 SPARQL；KG 查询只能由环境模板生成。
3. 邻居稳定排序，并记录 KG snapshot、query template 和 result hash。
4. 每个 episode 固定 `max_depth/max_steps/max_kg_calls/max_llm_calls/timeout`。
5. 用规范化 state hash 识别重复状态，连续重复触发惩罚或 Critic。
6. CVT 节点在执行层保留，在 observation 中表示为规范化 relation chain。

SP0/SP2 smoke 的建议初始预算：

```yaml
max_path_hops: 3
max_steps: 12
max_kg_calls: 16
max_llm_calls: 8
max_critic_rounds: 2
relation_top_k: 30
neighbor_cap_per_expand: 50
episode_timeout_seconds: 180
```

## 5. KG-Only 合成任务

### 5.1 生成流程

1. 从不含 benchmark test 实体的 KG-only pool 采样 source entity。
2. 按类型频率和 degree 分桶，避免集中于热门人物或高频实体。
3. 随机游走采样隐藏的 1-3 hop witness。
4. 规范化 CVT path，将 mediator 折叠为可解释 relation chain。
5. 拒绝循环、自环、重复 relation、非法节点和无法稳定重放的 path。
6. 拒绝预算内不可观察的极高 branching task。
7. 拒绝仅复制实体名即可回答的 trivial task。
8. 拒绝答案集合过大、答案类型不稳定或语言歧义过高的 task。
9. 根据 relation label、entity type 和 path semantics 生成自然语言需求。
10. 检查问题不包含 raw relation ID、目标实体名或 witness 序列。
11. 从 source 重新执行 witness，生成 task hash 和答案集合。

初版可用模板生成稳定问题；LLM 只负责将脱敏 relation gloss 改写为自然语言，不参与答案或 witness 的确定。改写结果必须通过 relation-ID 和答案泄漏检查。

### 5.2 难度记录

每个 task 记录 `hop_count`、`branching_bin`、`cvt_count`、`answer_cardinality_bin`、source/answer type、`path_signature`、lexicalization mode 和 alternative-path bin。任务集必须覆盖不同 hop/branching 组合，不能只保留容易成功的 1-hop task。

### 5.3 Split

| split | 用途 | 隔离要求 |
|---|---|---|
| `sp_discovery` | rollout、诊断、候选生成 | 可重复运行 |
| `sp_validation_entity` | 验证迁移到新实体 | source/answer entity 不与 discovery 重合 |
| `sp_validation_signature` | 验证未过拟合固定 path | entity 和 path signature 尽量不重合 |
| `sp_test_frozen` | Self-Play 内部最终测试 | promotion 规则冻结后只运行一次 |

实体隔离以 alias 规范化后的 ID 为准。manifest 记录 KG snapshot hash、source pool hash、过滤器版本、exclusion-list hash 和 seed。

### 5.4 Task schema

`self_play_tasks.jsonl` 每行一个任务：

```json
{
  "task_id": "spt_000001",
  "split": "sp_discovery",
  "kg_snapshot": "freebase_snapshot_hash",
  "source_entity": {
    "id": "m.source",
    "name": "Source Name",
    "types": ["people.person"]
  },
  "information_need": "Which place is associated with the person's birth?",
  "difficulty": {
    "hop_count": 1,
    "branching_bin": "medium",
    "cvt_count": 0
  },
  "oracle": {
    "answer_entity_ids": ["m.target"],
    "witness_relations": ["people.person.place_of_birth"],
    "witness_replay_hash": "sha256:..."
  },
  "path_signature": "people.person|people.person.place_of_birth|location.location",
  "generator_version": "sp-task-v1",
  "seed": 17
}
```

Actor-facing loader 输出时必须删除整个 `oracle` 字段，不能只依靠 prompt 约定忽略。

## 6. Episode 角色与上下文隔离

### 6.1 Explorer / Actor

Explorer 根据可见状态选择下一动作，输入只含 task observation、已执行轨迹、剩余预算和动作 schema，输出单个结构化动作。禁止把隐藏 chain-of-thought 作为持久数据。

### 6.2 Critic / Reflector

出现以下情况时触发 Critic：expansion 为空、连续两步没有新增信息、重复状态、Explorer 准备 `ABANDON`、预算达到 50%/80%，或准备在证据不足时 `STOP`。

Critic 只能看到当时的 observable trajectory，输出：

```json
{
  "diagnosis": "premature_stop | repeated_state | weak_route | wrong_frontier | budget_risk",
  "recommended_action": {
    "action": "BACKTRACK",
    "target_entity_id": "m.visible_frontier"
  },
  "confidence": 0.78,
  "observable_evidence_refs": ["step_3", "step_5"]
}
```

建议仍需环境校验，不能绕过动作白名单。

### 6.3 Deterministic Verifier

Verifier 不使用 LLM 判断事实，负责重放动作、检查 relation/entity、验证提交答案、确认路线连通、计算成本和失败类型、执行反事实回放，并生成 replay hash。

### 6.4 Distiller

Distiller 只读取脱敏 observable state、确定性 outcome 标签和多条相似轨迹统计。它不得读取原始 oracle witness，只能生成 candidate，不能直接 promotion。

Explorer、Critic 和 Distiller 可使用同一 LLM，但必须使用独立 system prompt、独立消息上下文、独立 role tag，并记录模型版本、temperature、seed 和 max-token。

## 7. Episode 与反事实回放

### 7.1 标准流程

```text
reset(task) -> Explorer action -> validate/execute -> update state/budget
    -> optional Critic -> accept/reject or controlled branch
    -> STOP/ABANDON/budget exhausted -> deterministic verification
    -> counterfactual replay -> immutable episode record
```

同一 task 初版建议产生：1 条 actor-only trajectory、2-4 条不同 seed 的 Explorer trajectory、失败轨迹最多 2 轮 Critic correction，以及 1 次确定性 replay audit。

### 7.2 Critic 受控分支

Critic 建议不能直接覆盖 Explorer：

1. Runner 保存建议和触发状态；
2. 环境校验建议动作；
3. 合法建议交给 Explorer 接受或拒绝；
4. 另运行受控分支比较“接受建议”和“保持原动作”；
5. 只有可重放的净改善才计入 critic value。

### 7.3 反事实失败回放

对失败 episode，Verifier 检查：失败前是否存在已观察但未尝试的 relation/frontier；同等剩余预算内能否由该处到达答案；替代路线是否只使用 actor 当时已知信息；动作是否能由当前接口执行；该修正能否跨 seed 或 held-out entity 重复。

只有通过这些检查，才可生成 `reflection_continue`、`reflection_backtrack` 或 `failure_pattern` 候选。使用 actor 当时不可见 relation 得到的修正仅作为 oracle upper bound，不写入 memory。

## 8. Reward 与轨迹排序

Reward 初版只用于比较轨迹、筛选 episode、评估 Critic 和排序候选，不用于训练模型，不反馈给 Actor，也不出现在 prompt 中。

建议初始公式：

```text
R = 1.00 * verified_success
  + 0.20 * verified_partial_progress
  + 0.15 * efficient_alternative_path
  + 0.10 * correct_unknown
  - 0.02 * kg_call_count
  - 0.03 * extra_depth
  - 0.08 * repeated_state_count
  - 0.25 * invalid_or_hallucinated_action
  - 0.15 * avoidable_dead_end
  - 0.20 * false_stop_or_false_answer
```

`verified_success` 由答案集合和 KG replay 决定；partial progress 可用隐藏 witness 离线计算，但不能提供给 Actor/Critic。权重只允许在 `sp_discovery` 上调整一次，validation 前冻结。主报告必须同时给出未加权指标。

## 9. 轨迹与经验 Schema

### 9.1 文件隔离

```text
<self_play_artifact_dir>/
├── self_play_tasks.jsonl
├── self_play_trajectories.jsonl
├── self_play_candidates.jsonl
├── self_play_memory.jsonl
├── self_play_manifest.json
├── replay_failures.jsonl
└── promotion_report.json
```

任务、原始轨迹、候选和已晋升经验分开保存。`self_play_memory.jsonl` 不保存完整 episode、具体答案或 benchmark 内容。

### 9.2 Step schema

```json
{
  "step_id": "step_004",
  "turn": 4,
  "role": "explorer",
  "state_hash": "sha256:...",
  "visible_state": {
    "current_entity_types": ["people.person"],
    "frontier_type_multiset": ["location.location"],
    "explored_relation_families": ["people.person.profession"],
    "failed_route_signatures": ["people.person.profession"],
    "depth": 1,
    "remaining_steps": 8,
    "branching_bin": "medium"
  },
  "action": {
    "action": "BACKTRACK",
    "target_entity_id": "m.visible_frontier"
  },
  "execution": {
    "valid": true,
    "new_entity_count": 1,
    "error_code": null
  },
  "critic_intervention_id": null
}
```

### 9.3 Episode schema

`self_play_trajectories.jsonl` 每行保存一个不可变 episode：

```json
{
  "episode_id": "spe_000001_seed17",
  "task_id": "spt_000001",
  "split": "sp_discovery",
  "roles": {
    "explorer_model": "model-version",
    "critic_model": "model-version",
    "prompt_version": "sp-prompt-v1"
  },
  "budget": {
    "max_steps": 12,
    "max_kg_calls": 16,
    "max_llm_calls": 8
  },
  "steps": ["<embedded step records>"],
  "outcome": {
    "verified_success": true,
    "kg_calls": 7,
    "llm_calls": 5,
    "invalid_actions": 0,
    "repeated_states": 1,
    "critic_helped": true,
    "reward": 0.91
  },
  "verification": {
    "replayable": true,
    "replay_hash": "sha256:...",
    "counterfactual_available": false,
    "verifier_version": "sp-verifier-v1"
  },
  "seed": 17,
  "created_at": "2026-08-20T00:00:00Z"
}
```

### 9.4 经验类型

| 类型 | 含义 | 主要来源 |
|---|---|---|
| `exploration_strategy` | 某类 state 中优先检查/展开的 relation family | 多条成功轨迹 |
| `reflection_continue` | 存在验证过的未探索路线时避免过早停止 | 成功续探或反事实回放 |
| `reflection_backtrack` | 停滞时回到何种 frontier type/state | Critic 修正和受控分支 |
| `failure_pattern` | 重复状态、错误 frontier 或 branching 陷阱 | 多条可重现失败轨迹 |

持久经验必须以实体类型替换具体 entity ID，但可以保留 relation/path signature、方向、状态条件和负面条件。

### 9.5 Candidate schema

```json
{
  "candidate_id": "spc_000031",
  "experience_type": "reflection_backtrack",
  "applicable_stage": "reflection_b",
  "state_signature": {
    "question_intent_family": "place_association",
    "current_entity_types": ["people.person"],
    "frontier_target_types": ["location.location"],
    "explored_relation_families": ["people.person.profession"],
    "failure_code": "no_new_entities",
    "branching_bin": "medium",
    "depth_bin": "1-2",
    "budget_bin": "mid"
  },
  "recommended_action": {
    "action_type": "backtrack",
    "target_frontier_type": "location.location",
    "preferred_relation_families": ["people.person.place_of_birth"]
  },
  "discovery_support": {
    "task_count": 6,
    "unique_source_entity_count": 6,
    "success_lift": 0.18
  },
  "provenance": {
    "episode_ids": ["spe_..."],
    "task_generator_version": "sp-task-v1",
    "verifier_version": "sp-verifier-v1"
  },
  "status": "candidate"
}
```

### 9.6 Promoted memory schema

```json
{
  "memory_id": "spm_v1_000009",
  "experience_type": "reflection_backtrack",
  "applicable_stage": "reflection_b",
  "state_signature": "<normalized candidate state signature>",
  "recommended_action": "<normalized candidate action>",
  "validation": {
    "entity_held_out_task_count": 4,
    "signature_held_out_task_count": 3,
    "success_lift_vs_no_memory": 0.12,
    "success_lift_vs_raw_cache": 0.08,
    "harmful_intervention_rate": 0.02,
    "replay_valid_rate": 1.0
  },
  "confidence": 0.84,
  "memory_version": "self-play-memory-v1",
  "source_hash": "sha256:...",
  "status": "promoted"
}
```

禁止存储没有适用条件的历史结论，例如“上次继续成功，所以都应继续”。每条 continue/stop/backtrack 建议必须绑定 state signature、支持数、验证结果和禁止条件。

## 10. 经验蒸馏与晋升

### 10.1 流程

```text
replayable episodes -> normalized state clustering
    -> compare success/failure/controlled branches
    -> generate candidates -> discovery support filter
    -> entity-held-out validation -> signature-held-out validation
    -> promote / quarantine / reject
```

Distiller 可提出文字摘要，但 support、unique entity count、success lift、harmful intervention、replay validity、split 和 provenance 必须由规则与统计程序生成。

### 10.2 Pilot 晋升门槛

阈值可在 SP3 前根据成本调整一次并冻结：

- 来源 episode replay valid rate = 100%；
- discovery task support >= 5；
- discovery unique source entity support >= 5；
- entity-held-out validation support >= 3；
- 至少一个 validation split 相对 no-memory success lift >= 5 个百分点；
- 相对 raw trajectory/cache 对照不为负；
- harmful intervention <= 5%，且不高于 no-memory 超过 2 个百分点；
- 不含具体 source/answer entity ID、答案字符串或 benchmark 内容；
- manifest、source hash、verifier version 和 memory version 完整。

支持不足但方向稳定时标记 `quarantined`，retrieval 不加载。单条 trajectory 无论得分多高都不能晋升。

### 10.3 生命周期

```text
candidate -> validated -> promoted -> retired
         -> quarantined
         -> rejected
```

memory 不原位覆盖。每次 promotion 生成新版本和 manifest；retired 记录保留，但默认不加载。

## 11. 接入 PoG Reflection

### 11.1 唯一初始接入点

```text
PoG/utils.py::if_finish_list()
    Decision A：是否继续探索/引入历史 frontier
    Decision B：回溯到哪些实体或 frontier
```

不接入 `freebase_func.py::relation_search_prune()`，不改变 first-hop relation 候选、排序或 semantic filtering。

### 11.2 两类记忆的职责

```text
KG structural evidence:
    当前 frontier 是否存在经独立 KG 验证的可行路线？

Self-Play experience:
    相似搜索状态、失败模式和预算下，什么动作曾稳定有效？
```

两类 evidence 必须用分开的 prompt block，并在 trace 中分别记录命中、得分和被引用情况，不能先融合成不可归因的总分。

### 11.3 Retrieval key 与 fallback

检索使用 stage、question intent family、current/frontier entity type、explored relation family、failure signature、depth、remaining budget、branching bin 和未尝试路线摘要。初版 `top_k=3`。

若没有同时满足 stage、type 和 failure condition 的经验，必须返回空 evidence，并保持原始 PoG 行为。

### 11.4 Prompt block 示例

```text
[KG STRUCTURAL EVIDENCE]
- A replayable untried route exists from a location frontier; confidence=0.81.

[SELF-PLAY EXPERIENCE]
- In 4 held-out cases with the same no-new-entity pattern, backtracking to a
  location frontier improved verified success by 12%; harmful rate=2%.

[CONSTRAINT]
- Evidence is advisory. Do not introduce entities or relations absent from the
  current candidate set.
```

### 11.5 Reflection trace

```json
{
  "stage": "reflection_a",
  "memory_mode": "none | kg | self_play | kg+self_play | raw | control",
  "kg_evidence_ids": ["kgm_..."],
  "self_play_memory_ids": ["spm_..."],
  "self_play_match": {
    "state_similarity": 0.0,
    "condition_match": true,
    "confidence": 0.0
  },
  "decision_without_hidden_oracle": true,
  "llm_decision": "continue | stop | backtrack",
  "selected_frontier": "...",
  "evidence_cited_by_decision": ["spm_..."],
  "post_decision_new_triples": 0,
  "post_decision_found_answer": false,
  "harmful_intervention": false
}
```

## 12. 实验组与公平对照

### 12.1 主矩阵

| 组 | KG evidence | Self-Play experience | 目的 |
|---|---:|---:|---|
| `R0` | 否 | 否 | 原始 reflection 基线 |
| `RK` | 是 | 否 | 静态 KG evidence 单独效果 |
| `RT` | 否 | 是 | Self-Play experience 单独效果 |
| `RKT` | 是 | 是 | 两类记忆互补性 |
| `RRaw` | 否 | raw successful trajectories | 抽象与原始轨迹对照 |
| `RCache` | raw KG neighborhood | 否 | 记忆与邻域缓存对照 |
| `RShuffle` | 同目标组 | shuffled experience | 内容随机对照 |
| `RIrrelevant` | 同目标组 | token-matched irrelevant | token/格式对照 |

另设分析型 `ROracle`，允许 Verifier 使用隐藏最优路线，仅作为 upper bound，不是可部署方法，也不能参与 promotion。

### 12.2 公平性

- 所有组使用相同问题顺序、seed、timeout、retry、max-token 和搜索预算；
- controls 与真实 experience 保持相同条数、长度、字段和 prompt 位置；
- `RRaw` 来自同一 discovery pool，且删除答案和目标实体；
- 正式对比前冻结 generator、memory、retrieval threshold 和 prompt；
- 所有组保留 memory-off behavior equivalence checker；
- final unseen 只运行一次，不根据结果回调阈值。

## 13. 分阶段实施计划

### SP0：环境与动作协议

实现 state/action schema、动作白名单、预算、错误码、state hash、受限 KG query template、oracle 隔离 loader，以及 1-hop/2-hop/CVT/unknown 单元测试。

验收：无任意 SPARQL；非法动作稳定拒绝；同 snapshot/seed 的 replay hash 一致；actor input 不含 oracle。

### SP1：合成任务生成

建立 KG-only pool 和 benchmark exclusion list；实现 1-3 hop witness、CVT normalization、degree/cycle/triviality/ambiguity/leakage 过滤；生成 discovery/validation/test manifest；每个难度桶人工抽检至少 20 条。

验收：任务全部可重放，split 无 alias 泄漏，问题不含答案名或 raw relation ID，难度分布可报告。

### SP2：Rollout 与验证

实现 Explorer runner、Critic trigger/controlled branch、episode replay、answer verification、counterfactual replay 和 immutable trajectory；在 smoke task 比较 actor-only 与 actor+critic。

验收：轨迹可 100% 重放；oracle 不进入 LLM 请求日志；Critic 净效果可通过受控分支计算。

### SP3：蒸馏与 held-out promotion

规范化 state signature，聚类成功/失败/counterfactual trajectory，生成四类 candidate，在 entity/signature validation 上验证，输出 versioned memory、manifest 和 promotion report。

验收：promoted memory 全部满足 support、replay、held-out lift、harm 和 provenance 门槛。

### SP4：Reflection-only 接入

启动条件：V2 reflection-only 在 `final_unseen` 上通过门控。只在 `if_finish_list()` 接入，分离 KG/Self-Play block，增加各实验模式、fallback 和 trace checker，不改 first-hop score。

验收：memory-off 等价；empty retrieval 完全 fallback；每次干预可对齐 memory ID 和 outcome。

### SP5：hard150 development pilot

只在 SP4 通过后执行：

```text
R0/RK/RT/RKT/RRaw/RCache/RShuffle/RIrrelevant smoke n=20
    -> 修复协议问题
    -> hard150 development stress n=150
```

hard150 只用于选择 retrieval top-k、confidence gate 和 evidence 压缩方式，不能作为最终主结果。

### SP6：冻结 final unseen

冻结代码、prompt、两类 memory、threshold、seed、预算和分析脚本后，在未参与调试的 `final_unseen` 上一次性运行主矩阵。只有 SP6 可支持“Self-Play experience 增强 PoG reflection”的最终结论。

### SP7：迁移与可选演化

若 SP6 成功，冻结同一个 Self-Play memory 迁移到 WebQSP/CWQ/GrailQA，比较跨数据集覆盖与来源差异。最后才评估 reward-based policy training 或在线演化；静态经验检索未通过 SP6 时不启动 RL。

## 14. 指标体系

### 14.1 Self-Play 环境

- synthetic task success rate；
- actor-only 与 actor+critic success lift；
- path length、steps、KG/LLM calls；
- invalid/hallucinated action rate；
- repeated-state 和 dead-end rate；
- replay valid rate；
- counterfactual recovery rate；
- correct unknown rate；
- 每个成功 task 的 tokens、时间和成本。

### 14.2 经验质量

- candidate/promoted count 与 candidate-to-promoted ratio；
- task/entity support；
- entity/signature-held-out success lift；
- 相对 raw trajectory/cache 的差异；
- harmful intervention rate；
- memory coverage、top-k 和 confidence calibration；
- memory build calls、tokens、wall time 和存储大小。

### 14.3 PoG Reflection

- Decision A continue/stop accuracy；
- premature stop 和 wasteful continuation；
- Decision B effective backtracking；
- 回溯后新增 triple/entity 数和找到答案比例；
- evidence intervention/citation rate；
- KG-only、Self-Play-only、combined 的阶段级增益；
- final EM、F1、calls、tokens、latency、timeout；
- harmful intervention：原本正确而因 memory 变错或显著变慢。

结果至少按 hop、branching、CVT、entity type、seen/unseen path signature、Decision A/B、failure pattern、memory hit/miss，以及两类 evidence 一致/冲突分层。

## 15. 门槛、停止与回滚

### 15.1 进入 final unseen

在 hard150 development pilot 上，RT 或 RKT 至少满足一项：premature stop/wasteful continuation 下降；effective backtracking 提升；EM/F1 提升且可由 Decision A/B 解释；或正确率持平但 calls/无效 expansion/latency 明显下降。

同时必须满足：

- 真实 memory 明显优于 shuffled/irrelevant；
- RKT 不明显差于 RK；
- harmful intervention 不超过冻结阈值；
- RRaw/RCache 不能完全解释增益；
- oracle 隔离检查零违规。

### 15.2 停止条件

出现以下任一情况，停止扩大：

- Self-Play memory 不优于 shuffled、irrelevant 或 raw trajectory；
- held-out entity 上效果消失；
- 只在 seen path signature 上有效；
- 增益只来自更长 prompt 或更多 calls；
- Actor/Critic 获得 oracle、目标答案或 reward 泄漏；
- promotion 后 harmful intervention 持续超阈值；
- RKT 明显差于 RK 且冲突处理无法解释；
- replay 不稳定或 KG snapshot 无法固定；
- 构建成本远高于收益且无跨数据集复用迹象。

若 SP4-SP6 失败，关闭 Self-Play retrieval，保留任务、轨迹和 verifier 作为档案，不影响已验证的 KG structural reflection，也不返回 first-hop reranking 或直接启动 RL。

## 16. 配置、产物与复现

### 16.1 建议配置

```yaml
self_play:
  enabled: false
  phase: SP0
  task_split: sp_discovery
  task_generator_version: sp-task-v1
  environment_version: sp-env-v1
  verifier_version: sp-verifier-v1
  explorer_prompt_version: sp-explorer-v1
  critic_prompt_version: sp-critic-v1
  memory_version: null
  seed: 17
  max_steps: 12
  max_kg_calls: 16
  max_llm_calls: 8
  max_critic_rounds: 2
  retrieval_top_k: 3
  confidence_threshold: 0.70
  allow_arbitrary_sparql: false
  expose_oracle_to_actor: false
```

### 16.2 Manifest

必须记录 code/file hash、KG snapshot、task/memory/prompt/verifier version、split hash、benchmark exclusion-list hash、model endpoint/version、temperature、seed、timeout、retry、max-token、reward 权重、promotion threshold 和所有产物 SHA-256。

### 16.3 Run 产物

```text
<run_dir>/
├── run_meta.json
├── self_play_tasks_used.jsonl
├── self_play_trajectories.jsonl
├── replay_report.json
├── critic_intervention_cases.jsonl
├── oracle_leakage_report.json
├── self_play_metrics.json
├── failure_cases.jsonl
└── cost_report.json
```

## 17. 单元测试与审计

### 17.1 环境

- 同 snapshot 的同一动作结果一致；
- 未观察 relation 和任意 SPARQL 被拒绝；
- BACKTRACK 不能跳到未出现实体；
- STOP 不能提交未观察答案；
- 预算耗尽后不调用 KG/LLM；
- CVT normalization 前后可重放。

### 17.2 泄漏

- actor 序列化输入不含 `oracle/answer_entity_ids/witness_relations`；
- Critic/Distiller 请求日志不含答案名；
- question 不含 raw relation ID；
- benchmark exclusion 实体不进入 source/answer pool；
- validation/test task 不进入 candidate distillation。

### 17.3 Promotion 与 PoG

- support 不足、replay 失败、entity ID 未清除或 hash 缺失时不能晋升；
- 单一实体的重复 episode 只计一个 entity support；
- `self_play.enabled=false` 与原始 PoG 等价；
- empty retrieval 正确 fallback；
- Self-Play evidence 不改变 relation score；
- KG 与 Self-Play evidence 分块 trace；
- controls token/evidence 数量匹配；
- outcome 可对齐每次 intervention。

## 18. 创新点与主张边界

不应声称：首个 KGQA Self-Play、首个 KGQA experience memory、首个类型泛化 path memory，或首个 trajectory-based reranking。

更稳健的定位是：

> 面向 PoG reflection 的 KG-only synthetic Self-Play：通过受限 KG 环境、确定性轨迹重放、entity-held-out 验证和反事实失败恢复，将 trajectory-derived experience 与独立构建的 statistical KG structural evidence 分离并融合。

真正需要验证的组合创新是：

1. Actor 与隐藏 oracle 的物理隔离；
2. 失败轨迹的反事实恢复，而非只缓存成功轨迹；
3. entity-held-out 和 signature-held-out promotion；
4. 静态 KG evidence 与经验 evidence 的来源分离和阶段级归因；
5. raw trajectory、raw cache、shuffle 和 token-matched controls；
6. 只干预 reflection，不重复已关闭的 first-hop reranking。

## 19. 当前执行顺序

当前项目仍处于 `V2-0 / PLAN_REVISION`：

```text
完成 V2-0 协议审计
    -> 完成 KG-only reflection R0-R3 与 controls
    -> 冻结并运行 final_unseen
    -> 判断 V2 reflection 门控
    -> 若通过，再批准 SP0-SP3 实现和经验构建
    -> SP4 reflection-only 接入
    -> SP5 hard150 development pilot
    -> SP6 final_unseen
    -> 可选 SP7 transfer / policy learning
```

本文件使 Self-Play 从“没有实现方式”变为具有模块、数据、门控、验证和对照定义的可执行方案；它仍是后续分支，不替代当前 V2-0 的协议审计任务。
