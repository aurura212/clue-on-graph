<!--
=============================================================================
  KG-Structural Memory for PoG -- 实验实现与评估方案
  基于 clue_on_graph 当前实现，验证由 KG 直接交互产生的结构记忆
  是否能够提高 PoG 的推理准确性和搜索效率。
=============================================================================
-->

# KG-Structural Memory for PoG：实验实现与评估方案

## 0. 方案摘要

本实验在 `clue_on_graph/PoG` 现有推理流程上新增一套**由知识图谱直接产生、具有查询证据和统计置信度的结构记忆**，并测试它能否增强 PoG。

实验不预设记忆必须分为 relation memory 和 decomposition memory。所有记忆以统一记录组织，通过 `memory_kind` 与 `applicable_stages` 在不同推理阶段按字段取用。第一阶段重点验证两个最可能有效、也最容易归因的注入点：

1. **Relation selection**：依据实体类型、关系覆盖率和已验证路径模板，对候选 relation 提供软排序与提示。
2. **Reflection/backtracking**：依据候选实体上真实存在但尚未探索的 relation/path 证据，辅助判断“是否还有有希望的路线”和“应回溯到哪些实体”。

首轮实验只使用独立 KG 采样和 SPARQL 探测产生的结构记忆，不使用 benchmark 训练集标注，不让记忆直接给出答案，也不在初始版本中硬删除 relation。这样可以优先回答一个清晰问题：**可验证的 KG 结构记忆本身是否有用？**

---

## 1. 实验目标与研究假设

### 1.1 总目标

在保持 PoG 主体推理算法、LLM、问题集合和 KG 接口不变的条件下，比较无记忆、现有训练集记忆和 KG 结构记忆三类系统，判断 KG 结构记忆是否能够：

- 提高最终问答 EM/F1；
- 减少无效 relation、实体和死路探索；
- 改善反思阶段的继续、停止与回溯选择；
- 降低 LLM calls、token、搜索深度和运行时间；
- 在不依赖 benchmark 训练标注的情况下迁移到新的问题分布。

### 1.2 研究假设

- **H1（Relation）**：类型条件化的 relation 统计与路径模板能够提高 gold-next-relation recall，并减少低价值 relation 展开。
- **H2（Reflection）**：带 SPARQL witness 的未探索连通性证据能够减少 premature stop 和无效 backtracking。
- **H3（整体效果）**：Relation 与 Reflection 记忆联合使用时，PoG 的准确率和效率优于原始 PoG。
- **H4（证据必要性）**：真实结构记忆的增益显著高于 shuffled memory 和等 token 无关提示，说明效果来自 KG 结构信息，而非额外 prompt 或随机正则化。

---

## 2. 实验边界与基本原则

### 2.1 本轮包含

- 从 Freebase/SPARQL 端点主动采样实体、类型、relation 和短路径；
- 将发现样本与验证样本分离，估计结构模式的支持度与稳定性；
- 建立统一 JSONL 记忆库及内存索引；
- 在 relation 选择和 reflection 两处注入；
- 扩展 trace 和离线分析，做准确率、效率及阶段级归因；
- 与当前 relation/decomposition memory 做公平对照。

### 2.2 本轮暂不包含

- 不重新训练或微调 LLM；
- 不把 benchmark test gold answer、gold path 或 gold SPARQL 用于记忆构建和检索；
- 不在首轮修改 `entity_search()`，它本身是确定性的 KG 查询；
- 不在首轮向 `update_memory()` 注入外部记忆，避免污染当前问题的事实摘要；
- 不在首轮向最终 `reasoning()` 注入结构记忆，以便隔离搜索阶段的贡献；
- 不立即实现复杂 self-play 策略学习。Self-play 只在结构记忆被证明有效后进入 Phase 5。

### 2.3 设计原则

1. **记忆描述 KG，不模仿旧 LLM 决策**：主体内容是关系分布、路径模式、覆盖率、分支度和 witness。
2. **正证据优先**：记忆可以说“在抽样和验证中观察到该路线”，不能把“未观察到”表述为“KG 中不存在”。
3. **可追溯**：每条记忆保存查询模板、采样配置、证据实体、构建版本和 KG endpoint 标识。
4. **软使用优先**：首轮只 rerank 或 prompt guidance，不硬过滤候选 relation。
5. **发现与验证分离**：同一实体不能同时贡献 pattern discovery 与 held-out validation。
6. **以实体支持度为主**：避免把某个高出度实体的大量边误当作广泛规律。
7. **阶段可归因**：每次检索、注入和下游结果都进入 `pog_trace.jsonl`。

---

## 3. 当前 PoG 基线与改动位置

| 推理环节 | 当前入口 | 本实验处理 |
|---|---|---|
| 参数、数据循环、主流程 | `PoG/main_freebase.py` | 加载结构记忆、传递索引、记录配置 |
| Relation 搜索与剪枝 | `PoG/freebase_func.py::relation_search_prune()` | 检索类型统计和路径模板，软排序/提示 |
| Entity search | `PoG/freebase_func.py::entity_search()` | 首轮不改 |
| Entity condition prune | `PoG/freebase_func.py::entity_condition_prune()` | 首轮只记录诊断，不注入 |
| 当前状态记忆更新 | `PoG/freebase_func.py::update_memory()` | 不注入外部记忆 |
| 中间充分性判断 | `PoG/freebase_func.py::reasoning()` | 首轮不改 |
| Reflection/backtracking | `PoG/utils.py::if_finish_list()` | 注入可验证的 coverage/connectivity evidence |
| Trace 结构 | `PoG/trace_utils.py` | 增加 memory retrieval、intervention、outcome 字段 |
| 输出与运行元数据 | `PoG/output_paths.py` | 保存 memory 配置、hash、构建信息 |
| 最终评测 | `PoG/eval_run.py` | 保留现有指标；阶段指标由新分析器补充 |

当前 `relation_memory.py` 和 `decomposition_memory.py` 不删除，继续作为 supervised-memory baseline。新结构记忆独立实现，避免改变已有实验含义。

---

## 4. 与 KG 直接交互的方案

整体采用三层交互架构，但按可行性逐层实施。

### 4.1 Protocol 1：系统性 Schema Survey（首轮必须）

目的：获得不依赖 benchmark 问题的 KG 局部结构先验。

执行过程：

1. 从 KG 全局采样实体类型；类型来源按当前 Freebase 接口可用性，从 `type.object.type`、notable type 或 relation domain/range 中选择，并由 adapter 统一。
2. 对每个 source type 独立抽取 discovery entities 和 validation entities。
3. 查询各实体的 incoming/outgoing relations。
4. 聚合每个 `(source_type, direction, relation)` 的：
   - entity support；
   - coverage；
   - 平均/中位 branching factor；
   - endpoint type 分布；
   - CVT/mediator 比例；
   - discovery/validation 一致性。
5. 只保存超过最低 entity support 且在验证样本上复现的模式；其余记录为低置信或不入主索引。

建议第一版参数：每种 type 发现集 50 个实体、验证集 30 个实体；先覆盖 100 至 300 个高频 type。参数必须写入 manifest，后续通过 pilot 调整。

### 4.2 Protocol 2：短路径主动探测（首轮必须）

目的：获得比单 relation 统计更有用的 1-hop/2-hop 类型连通模式。

执行过程：

1. 从 Protocol 1 的高支持 relation 出发，枚举或定向采样 1-hop/2-hop path。
2. 记录 `source_type -> relation_path -> target_type`、方向、CVT 位置、每跳分支度。
3. 在 discovery entities 上发现路径，在独立 validation entities 上执行 ASK/COUNT 类验证。
4. 由 relation 名称、schema 描述和 endpoint types 生成检索用语义标签；LLM 只负责压缩/标注意义，不得修改结构事实。
5. 保存正向 witness 和验证统计。未命中路径只标为 `unknown_or_low_support`，不作为“不存在”的反例。

为控制规模，首轮限制到 2-hop。3-hop 只针对 CVT 展开后仍等价于一个语义关系的情况保留，并在记录中标记 `contains_cvt=true`。

### 4.3 Protocol 3：任务驱动 Self-Play（后续可选）

目的：让模型主动提出信息需求，在 KG 上执行、验证并沉淀高价值探索 trace。

可能流程：生成结构化目标 -> 选择探测动作 -> 执行 SPARQL -> 根据返回结果修正 -> 在新实体上复验 -> 压缩为结构记忆。

该协议创新性较强，但同时引入目标生成偏差、LLM 自我确认和高查询成本。因此本实验将其置于 Phase 5，仅在 Protocol 1/2 已证明有增益后实现。Self-play 产生的记录必须和纯 SPARQL 记忆分库或带明确 `source_protocol` 标签，不能混淆归因。

### 4.4 Inference-time 按需验证（第二轮可选）

当离线记忆返回低置信候选，或 reflection 需要判断某个具体实体是否仍有路线时，可以发起受预算约束的 SPARQL probe：

- 验证候选实体上 relation 是否真实存在；
- 验证某个 1/2-hop 模板是否至少有 witness；
- 将结果写入当前问题的 ephemeral cache，而不是立即写入全局记忆；
- 只有跨问题、跨实体复验后才晋升为持久记忆。

该模式需要单独报告额外 KG query 数和延迟，避免把在线暴力搜索误计为“记忆效率”。

---

## 5. 记忆保存形式

### 5.1 统一记录而非固定 relation/decomposition 二分

持久记忆使用 JSONL；每一行是一条经过验证的结构模式。推荐 schema：

~~~json
{
  "memory_id": "kgm_...",
  "memory_kind": "schema_profile | path_template | coverage | connectivity",
  "source_protocol": "schema_survey | path_probe | self_play",
  "applicable_stages": ["relation", "reflection_judge", "reflection_select"],
  "key": {
    "source_type": "...",
    "direction": "outgoing",
    "relation_path": ["..."],
    "target_type": "..."
  },
  "semantic": {
    "relation_labels": ["..."],
    "capability_text": "...",
    "info_need_tags": ["..."]
  },
  "statistics": {
    "discovery_entity_support": 0,
    "validation_entity_support": 0,
    "validation_coverage": 0.0,
    "median_branching": 0.0,
    "direction_consistency": 0.0,
    "cvt_ratio": 0.0,
    "confidence": 0.0
  },
  "evidence": {
    "positive_entity_ids": ["..."],
    "witness_paths": [["e0", "r1", "e1"]],
    "query_template_id": "...",
    "query_hash": "..."
  },
  "provenance": {
    "kg": "freebase",
    "endpoint_id": "...",
    "builder_version": "...",
    "build_config_hash": "...",
    "built_at": "YYYY-MM-DDThh:mm:ssZ"
  },
  "status": "validated | low_support | deprecated"
}
~~~

### 5.2 四类首轮记忆

| `memory_kind` | 保存内容 | 主要用途 |
|---|---|---|
| `schema_profile` | source type 下 relation 的 coverage、direction、branching、endpoint type | relation rerank |
| `path_template` | source type 到 target type 的已验证 1/2-hop relation path | relation prompt/rerank；reflection 路线证据 |
| `coverage` | 某类实体通过某组 path 可到达何类信息，及验证覆盖率 | reflection 是否值得继续 |
| `connectivity` | 具体候选实体或类型上尚可执行的 relation/path 及 witness | reflection 选择回溯实体 |

其中 `coverage` 不是“KG 能否回答某个自然语言问题”的主观判断，而是**从指定类型出发，已验证路径能否到达匹配目标语义/类型的结构证据**。

### 5.3 索引与运行时视图

构建时同时输出：

- `kg_structural_memory.jsonl`：可审计的源记录；
- `kg_structural_memory.index.json` 或本地序列化索引：按 source type、first relation、target type、memory kind 检索；
- 可选 embedding index：仅索引 `capability_text` 和 relation labels；
- `build_manifest.json`：采样、endpoint、查询预算、随机种子、hash 和统计摘要。

运行时检索结果被转换为阶段视图，而不是把整条 JSON 直接塞入 prompt。例如 relation 阶段只看 relation/path 与支持统计，reflection 阶段只看尚未探索路线、witness 和置信度。

---

## 6. 记忆在哪些推理环节使用

### 6.1 Relation selection：首轮主实验

输入：当前 question/sub-question、topic/current entity、实体类型、KG 返回的候选 relations、当前 depth 和已探索 relations。

处理：

1. 按实体类型检索 `schema_profile` 和 `path_template`。
2. 将候选 relation 与记忆中的 first-hop relation 取交集；记忆不能增加当前 KG 查询中不存在的 relation。
3. 结合语义相关度、validation coverage、entity support、branching penalty 和是否已探索，生成 memory score。
4. 第一版采用两种可切换策略：
   - `prompt`：把 Top-K 结构证据加入 relation prune prompt；
   - `rerank`：与原 PoG 分数线性融合，只调整次序。
5. 默认不做 hard prune。只有后续消融证明高置信阈值不会降低 relation recall，才增加 `hard_filter` 实验模式。

Relation 记忆的有效性不能只看最终答案，还要看候选覆盖、gold-next-relation recall、扩展数量和错误降权事件。

### 6.2 Reflection Decision A：是否仍有值得探索的路线

当前反思记忆不能仅保存“以前某次应该继续/停止”，因为这种结论本身可能错误。这里改为提供**反事实结构证据**：

- 当前未满足的信息需求对应哪些 target type/关系语义；
- 当前候选池中的实体类型是否存在已验证 path template；
- 这些模板的 first hop 是否尚未探索；
- 在当前具体实体上是否已有可执行 witness（若启用在线验证）；
- 路径的 validation coverage、support 和预期 branching。

记忆不直接输出 `continue=true`，而是给 `if_finish_list()` 的判断 prompt 一组带置信度的证据。LLM 仍结合当前 chain、已获事实和未满足目标做决定。

预期作用：降低明明存在未探索高支持路线却提前停止的概率，也降低没有正证据时盲目继续的概率。

### 6.3 Reflection Decision B：选择回溯实体

对每个候选实体构建摘要：

- entity type；
- 仍未探索且与 info need 匹配的 relation/path；
- memory support、coverage、branching；
- 是否存在具体 witness；
- 该实体此前探索过哪些 relation。

将候选按“有验证结构证据且探索成本适中”排序，再交给现有 reflection 逻辑选择。记录原排序与 memory 排序，便于判断记忆是否真的把有用实体提前。

### 6.4 暂不注入或仅做诊断的阶段

- **Decomposition**：结构记忆与自然语言问题分解的对应关系较间接，首轮注入会混淆实验归因。后续可尝试用 path capability 提醒“哪些子目标可由 KG 支持”。
- **Entity search**：确定性 SPARQL 执行，记忆不能替代真实查询。
- **Entity prune**：可能受 connectivity memory 帮助，但风险是把答案实体按常见类型偏置掉；首轮只记录候选类型和后验诊断。
- **Memory update**：只总结本题当前事实，禁止外部模式混入事实链。
- **Final reasoning**：首轮不注入，以确认增益来自搜索而非增加答案提示。

---

## 7. 具体代码设计

### 7.1 新增模块

在 `clue_on_graph/PoG/` 下新增：

| 文件 | 职责 |
|---|---|
| `kg_probe.py` | SPARQL 探测 adapter、类型/关系/路径查询、限流、重试、cache |
| `kg_structural_memory.py` | 统一 record schema、校验、JSONL I/O、manifest/hash |
| `kg_memory_retrieval.py` | 按类型/语义/阶段检索、打分、Top-K 与 prompt 压缩 |
| `reflection_structural_memory.py` | 构造 Decision A/B 所需证据与候选实体摘要 |
| `build_kg_structural_memory.py` | Protocol 1/2 构建 CLI、断点续跑、发现/验证切分 |
| `analyze_kg_memory_run.py` | relation/reflection 阶段指标、配对比较和错误案例导出 |
| `run_build_kg_memory.sh` | 固定构建配置示例 |
| `run_PoG_kg_memory_test.sh` | baseline/ablation 运行示例 |

若当前环境以 Windows 为主，可同步提供等价 `.ps1`；shell 文件主要保持和仓库现有实验脚本风格一致。

### 7.2 修改现有模块

#### `main_freebase.py`

新增参数并在问题循环外只加载一次索引：

~~~text
--kg_memory_mode none|relation|reflection|full
--kg_memory_path PATH
--kg_memory_stages relation,reflection_judge,reflection_select
--kg_memory_top_k 6
--kg_memory_strategy prompt|rerank
--kg_memory_min_confidence 0.6
--kg_memory_prompt_token_budget 600
--kg_memory_online_verify 0|1
--kg_memory_online_query_budget 0
--kg_memory_ablation none|shuffle|irrelevant
~~~

把 loader/index 挂到 `args.kg_memory_bank` 或显式 runtime context，并将当前 question 的检索缓存限定在 question 生命周期内。

#### `freebase_func.py`

在 `relation_search_prune()` 中：

1. 获取当前 entity type；
2. 调用 `retrieve_relation_evidence(...)`；
3. 生成原始候选、memory score、最终顺序；
4. 按策略注入 prompt 或 rerank；
5. 返回/记录本轮 memory intervention，不改变 `entity_search()` 的事实执行。

#### `utils.py`

在 `if_finish_list()` 内把反思拆成可记录的两个子决策：

- Decision A 前调用 `build_route_existence_evidence(...)`；
- Decision B 前调用 `rank_backtrack_entities(...)`；
- 保存 evidence、模型选择、后续是否找到新 triples、最终是否答对。

#### `trace_utils.py`

为每个 depth 增加：

~~~json
{
  "kg_memory": {
    "relation": {},
    "reflection_judge": {},
    "reflection_select": {}
  }
}
~~~

每个阶段至少记录 query key、retrieved IDs、分数、注入文本 token 数、候选前后顺序、是否采取建议和后续 outcome。

#### `output_paths.py`

在 `run_meta.json` 保存：memory mode/path/hash、builder version、检索阈值、策略、online query budget、ablation、随机种子。恢复运行时必须校验 memory hash，避免同一 run 混用不同记忆版本。

#### `eval_run.py`

保留现有 EM/F1/precision/recall、calls、tokens、runtime 评估。阶段级指标放入 `analyze_kg_memory_run.py`，分析结果写到同一 run 目录的 `kg_memory_analysis.json` 和 `kg_memory_cases.jsonl`。

---

## 8. 分阶段实施计划

### Phase 0：基线冻结与 trace 补全

- 固定当前代码 commit、LLM、temperature、prompts、KG endpoint 和数据切片；
- 运行 B0/B1/B2 小规模基线；
- 确认 `results.jsonl`、`pog_trace.jsonl`、`run_meta.json` 可复现；
- 补齐 relation 候选前后、reflection A/B 和实体探索统计；
- 不引入任何新记忆，先验证 instrumentation 不改变结果。

**验收**：相同配置下，补 trace 前后答案一致；所有问题有完整 stage trace。

### Phase 1：Schema profile 构建

- 实现 `kg_probe.py`、schema 与 manifest；
- 全局采样 source types 和 discovery/validation entities；
- 构建 `schema_profile`；
- 检查 coverage、support、branching、方向和 CVT 统计；
- 随机抽查 witness 是否可由 SPARQL 重放。

**验收**：构建可断点续跑；同配置 hash 相同；抽查记录可重放；无 benchmark gold 字段。

### Phase 2：Path template 构建

- 从高支持 relation 枚举/采样 1/2-hop path；
- 独立 validation entity 验证；
- 生成语义索引文本；
- 输出 `path_template`、`coverage` 和必要的 `connectivity` 记录。

**验收**：每条 active template 有验证支持和 provenance；未观察模式不写成负事实。

### Phase 3：Relation memory 注入

- 实现 relation retrieval、prompt/rerank 两种策略；
- 先跑 M1/M2/M3；
- 做 shuffled 与 irrelevant prompt controls；
- 分析 gold relation recall、搜索规模与最终正确率。

**验收**：记忆只重排真实候选；关闭开关时行为与 B0 一致；trace 可解释每次排序变化。

### Phase 4：Reflection memory 注入

- 实现 Decision A 路线存在证据；
- 实现 Decision B 候选实体结构排序；
- 分别跑 M4/M5，再跑 M6；
- 分析 premature stop、wasteful continuation 和有效回溯。

**验收**：反思证据均可追溯；没有把 memory conclusion 当作事实答案；能统计 intervention 后果。

### Phase 5：Self-play 与在线晋升机制（条件阶段）

只有在 M3 或 M6 相对 B0 显示稳定正增益后启动：

- 生成 KG 内生探索目标；
- 执行、验证并压缩 self-play trace；
- 与纯 SPARQL memory 分开消融；
- 测试 ephemeral memory 跨实体复验后晋升为 persistent memory。

---

## 9. 实验组与消融矩阵

| ID | 配置 | 目的 |
|---|---|---|
| `B0` | 原始 PoG，无 reference、无 memory | 主基线 |
| `B1` | 当前 supervised relation memory | 与现有训练集记忆比较 |
| `B2` | 当前 supervised decomposition + relation memory | 当前完整记忆基线 |
| `M1` | schema profile -> relation | 检验单跳类型统计 |
| `M2` | path template -> relation | 检验多跳结构模板 |
| `M3` | M1 + M2 -> relation | relation 完整结构记忆 |
| `M4` | coverage -> reflection Decision A | 检验继续/停止判断 |
| `M5` | connectivity -> reflection Decision B | 检验回溯实体选择 |
| `M6` | M3 + M4 + M5 | 完整结构记忆系统 |
| `C1` | shuffled memory | 排除任意额外文本的影响 |
| `C2` | token-matched irrelevant structural prompt | 排除 prompt 长度/格式影响 |

附加消融按资源选择：

- `prompt` vs `rerank`；
- 仅 discovery 统计 vs held-out validation 后记忆；
- 去掉 confidence/support；
- 去掉 endpoint type；
- 去掉 witness；
- 1-hop vs 1+2-hop；
- offline-only vs inference-time verify；
- global type sampling vs `topic_only` type sampling。

M1-M6 的 memory prompt token budget 必须一致；C2 与对应实验组 token 数尽量匹配。

---

## 10. 数据划分与泄漏控制

### 10.1 干净主实验

- 结构记忆只根据 KG 全局采样构建；
- 不读取 benchmark train/dev/test 的 question、answer、gold path、gold SPARQL；
- discovery 和 validation 按 entity ID 严格去重；
- benchmark gold relation 仅在实验结束后用于诊断，不参与检索、阈值选择或 early stop；
- 超参数只在开发切片选择，最终 test 配置一次冻结运行。

### 10.2 可选 transductive 效率实验

允许读取 evaluation topic entity IDs，仅用于确定应优先 survey 哪些 source types，不读取问题文本和标签。该模式必须命名为 `topic_only`，并明确标注为 transductive/efficiency-oriented，不能和干净主实验混报。

### 10.3 与 supervised memory 的公平比较

B1/B2 可使用现有训练集构建的记忆，但报告中必须同时列出：

- 是否使用 benchmark 训练标注；
- memory 构建问题数/实体数；
- memory 文件大小；
- 构建 LLM calls、SPARQL queries、tokens 和时间；
- 测试时额外 prompt tokens 和检索延迟。

---

## 11. 评估指标

### 11.1 最终效果

沿用 `eval_run.py`：

- Exact Match；
- F1、Precision、Recall；
- 按 question type、推理 hop、topic entity type 分组；
- Answerable/Unanswerable（若数据可判定）分组。

### 11.2 推理效率

- 每题 LLM calls、input/output/total tokens；
- wall-clock runtime；
- final stop depth；
- 展开的 relation 数、实体数、三元组数；
- 重复实体比例、dead-end 数；
- backtrack 次数；
- memory retrieval 时间与注入 token；
- offline memory build cost；
- online KG probe 数、延迟和 cache hit rate。

离线构建成本与单题推理成本分开报告，并给出按不同复用题量摊销后的成本。

### 11.3 Relation 阶段诊断

- gold-next-relation recall@K（仅后验）；
- memory retrieval coverage：多少 relation 决策获得有效记录；
- retrieved template 是否包含 gold next relation；
- 候选 relation 数和排序变化；
- helpful intervention：B0 错/M 组对且记忆提升关键 relation；
- harmful intervention：B0 对/M 组错且记忆压低关键 relation；
- memory confidence 与实际帮助概率的校准曲线。

### 11.4 Reflection 阶段诊断

- Decision A 选择继续后是否找到新 triples；
- 选择继续后最终是否答对；
- Decision B 选中实体是否产生与未满足目标相关的新路径；
- useful selected-entity rate；
- premature stop rate；
- wasteful continuation rate；
- supported-evidence hit rate；
- misleading-memory rate；
- 反思带来的额外 calls/tokens 与挽救正确题数。

### 11.5 统计检验

- 所有系统使用相同问题顺序做 paired comparison；
- EM 使用 McNemar test；
- F1、calls、tokens、runtime 使用 paired bootstrap 置信区间；
- 若 LLM 存在不可控随机性，至少运行 3 个 seed/重复，并同时报告均值、标准差和逐题多数结果；
- 除显著性外报告 effect size 和 accuracy-efficiency Pareto frontier。

---

## 12. 实验规模与运行顺序

### 12.1 Smoke test

- 10 至 20 题；
- B0、M1、M4；
- 验证参数、加载、trace、断点续跑和关闭开关一致性；
- 不用于结论。

### 12.2 Pilot

- 100 至 200 题，覆盖不同 question type/hop；
- B0、M1、M2、M3、M4、M5、M6、C1；
- 选择 Top-K、confidence threshold、token budget 和 prompt/rerank；
- 所有选择只基于 pilot/development 数据。

### 12.3 Development ablation

- 使用固定开发集；
- 完成核心矩阵和关键消融；
- 冻结 memory build config 与 inference config。

### 12.4 Final test

- 完整测试集一次性运行 B0、B1、B2、M3、M6、C1、C2；
- 相同模型、并发、temperature、max depth 和 KG endpoint；
- 输出逐题 paired report，禁止看 test 结果后调阈值。

---

## 13. Trace 与可解释性要求

每次 memory retrieval 至少保存：

- `stage`、question ID、depth、entity IDs/types；
- 检索 query/semantic key；
- memory IDs、kind、score、confidence、support；
- witness/query hash；
- 原始候选和 memory 后候选；
- 实际注入的压缩文本及 token 数；
- 模型是否采纳建议；
- 下一步新增 relation/entity/triples；
- 最终 correctness；
- 对应 B0 paired outcome（由离线分析补充）。

不得只记录最终 prompt。必须保留结构化字段，否则无法区分“没有检索到记忆”“检索到但没注入”“注入但模型未采纳”和“采纳后造成错误”。

---

## 14. 工程保障

1. **查询缓存**：SPARQL query hash -> response，构建断点续跑，避免重复打 endpoint。
2. **限流和重试**：指数退避、超时、最大重试、失败原因统计。
3. **配置 hash**：memory 文件、采样参数和代码版本共同生成 hash。
4. **Schema validation**：加载时拒绝字段缺失、非法 confidence 和 unknown version。
5. **Prompt budget**：按 confidence 和 stage utility 压缩，严格限制额外 token。
6. **故障降级**：memory 加载/检索失败时回退 B0，trace 标记，不让整题崩溃。
7. **可复现采样**：固定 seed，保存 discovery/validation entity IDs。
8. **去重**：相同 source type/path/target type 合并统计，不重复注入同义证据。
9. **版本隔离**：persistent memory、question-local ephemeral cache 和 self-play memory 分开保存。
10. **单元测试**：schema round-trip、entity split、score 边界、候选不增不减、shuffle control、memory-off equivalence。

---

## 15. 风险与缓解

| 风险 | 影响 | 缓解方式 |
|---|---|---|
| KG 不完整导致“假阴性” | 错误停止或剪枝 | 未观察一律为 unknown；首轮不硬过滤 |
| 热门实体支配统计 | 记忆偏向高出度区域 | 使用 entity support、中位 branching、每实体封顶 |
| 类型过粗或多类型冲突 | 检索噪声 | 保存多类型，按验证覆盖和具体候选 relation 约束 |
| CVT 路径膨胀 | 模板难理解、分支大 | 显式标记 CVT，语义压缩但保留原始 path |
| LLM 语义标签幻觉 | 检索错配 | 标签只用于召回；结构事实必须来自 SPARQL |
| Prompt 变长造成表面增益/退化 | 归因不清 | C2 等 token 对照，报告额外 tokens |
| Reflection 自我确认 | 错误路线被反复强化 | 只给 witness/statistics，不保存“应继续”的结论 |
| 在线验证变成额外搜索 | 效率比较不公平 | 单独 query budget，offline-only 主结果 |
| Benchmark 泄漏 | 结论失真 | 全局采样主实验；gold 只做 post-hoc |
| 记忆覆盖率低 | 总体增益不明显 | 同时报告 covered subset 与全体，但不只挑覆盖题下结论 |

---

## 16. 成功标准与停止条件

### 16.1 进入完整实验的门槛

Pilot 中 M3 或 M6 至少满足以下之一，并且没有显著 recall 损失：

- EM/F1 相对 B0 有稳定正提升；
- 准确率持平但 calls/tokens/展开实体数明显下降；
- 在 memory-covered subset 上有清晰提升，且 harmful intervention 可被 confidence 过滤控制。

### 16.2 进入 Self-Play 的门槛

- Protocol 1/2 的真实 memory 优于 C1/C2；
- 至少一个注入阶段有可归因的正效果；
- provenance、复验和阶段 trace 完整。

### 16.3 暂停或回退条件

- relation recall 因 rerank 持续下降；
- shuffled/irrelevant control 与真实 memory 效果相当；
- 增益只来自额外 token，效率显著恶化；
- memory evidence 无法重放或 discovery/validation 泄漏；
- reflection 的 misleading-memory rate 高于挽救率。

若出现上述情况，先回退到只做诊断的 memory retrieval，分析错误类型，再决定改检索、改置信度，或放弃该注入阶段，而不是继续扩大 self-play。

---

## 17. 预期产物

~~~text
clue_on_graph/PoG/
├── kg_probe.py
├── kg_structural_memory.py
├── kg_memory_retrieval.py
├── reflection_structural_memory.py
├── build_kg_structural_memory.py
├── analyze_kg_memory_run.py
├── run_build_kg_memory.sh
└── run_PoG_kg_memory_test.sh

clue_on_graph/PoG/kg_memory/
└── <memory_build_id>/
    ├── kg_structural_memory.jsonl
    ├── kg_structural_memory.index.json
    ├── build_manifest.json
    ├── discovery_entities.jsonl
    ├── validation_entities.jsonl
    └── probe_cache/

<run_dir>/
├── results.jsonl
├── pog_trace.jsonl
├── run_meta.json
├── kg_memory_analysis.json
└── kg_memory_cases.jsonl
~~~

---

## 18. 推荐的最小首轮实验

为了最快判断方向是否成立，建议先实现以下最小闭环：

1. Phase 0：补齐 relation 与 reflection trace，复跑 B0。
2. 只实现 Protocol 1，并为 100 至 300 个 source types 构建 validated `schema_profile`。
3. 在 `relation_search_prune()` 中实现 `rerank`，不做硬过滤。
4. 运行 100 至 200 题的 B0、M1、C1、C2。
5. 同时检查 final EM/F1、gold relation recall、展开 relation/entity 数、calls 和 tokens。
6. 若 M1 优于 controls，再实现 Protocol 2/path template，扩展到 M2/M3。
7. Relation 记忆确认有效后，再把已验证 path template 转成 reflection Decision A/B 的结构证据，运行 M4-M6。
8. 只有 M3/M6 形成稳定正增益，才实现 Self-Play 和持久记忆晋升。

该顺序将每一步的代码量、查询成本和实验变量控制在较小范围内，同时能依次回答：

- KG 结构统计是否能改善 relation 选择？
- 多跳模板是否提供额外价值？
- 同一批可验证结构证据能否改善 reflection？
- 增益是否真正来自记忆内容？
- 在此基础上，是否值得投入更复杂的自主交互与 self-play？
