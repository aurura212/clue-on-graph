# KG-Structural Memory for PoG：V2 实验计划

> 版本：V2.0  
> 制定日期：2026-08-20  
> 适用项目：`SRP_2/clue_on_graph/PoG`  
> 前一版本：`experimental_plan_kg_memory_from_gpt56.md`  
> 变更依据：`experiment_log_kg_memory.md` 的 LOG-047 至 LOG-049

## 0. 版本定位

本文件是基于已有方案和 hard150 实验结果形成的修订版方案。旧方案保留为历史档案，不删除、不覆盖；后续若继续开展 KG-memory 实验，以本文件为执行依据。

本次修订不是否定 KG structural memory 的总方向，而是关闭已经完成诊断、但没有形成可归因增益的具体分支：

```text
schema_profile / path_template
    -> first-hop relation rerank
    -> 已停止扩大
```

新的主实验分支为：

```text
validated KG structural evidence
    -> reflection-only frontier decision
    -> Decision A：是否继续探索
    -> Decision B：回溯到哪个实体
```

V2 不再把“relation memory 先通过”作为启动 reflection 实验的必要条件，因为 relation selection 和 reflection 是两个不同的干预机制，应该独立验证。

## 1. 当前阶段的实验结论

### 1.1 已关闭的分支

在 `PoG/eval_slices/hard150_v1.json` 上，schema memory 和 path template 用于 first-hop relation rerank 没有形成可归因增益：

- M1 的 EM 相对 B0 有小幅差异，但 M1 的 gold first-hop selected recall 低于 B0、C1 和 C2；
- M1 与 C1 的 EM 翻盘题中，depth1 top1 完全相同，EM 差异不能归因于 first-hop rerank；
- M1 的额外 token 和时间开销高于 B0；
- M2 会制造高置信错误 first-hop，关闭 `tail_sem` 后只回到 B0 水平，仍没有超过真实对照；
- 当前 path template 不再用于 first-hop rerank，也不再启动 M3。

因此 V2 明确关闭：

1. M3：M1 + M2 的 relation 联合注入；
2. 继续调 first-hop fusion、`tail_sem`、结构分权重或同类 prompt 变体；
3. 将 first-hop EM 的小幅差异作为论文主结果；
4. 在没有新证据的情况下启动 self-play。

### 1.2 尚未被检验的问题

当前实验尚未回答：

1. 经过验证的 KG 结构证据能否减少 premature stop；
2. 结构证据能否帮助 PoG 在多个 frontier entity 中选择更有希望的回溯点；
3. 结构记忆是否更适合作为 reflection evidence，而不是 relation prior；
4. KG-only structural memory 与 trajectory-derived memory 的作用是否互补；
5. 一次构建的 KG memory 能否跨数据集复用。

这些问题构成 V2 的研究范围。

## 2. V2 的研究问题与假设

### 2.1 核心研究问题

> 不依赖 benchmark QA 轨迹和 gold 标注、通过独立 KG 探测及 held-out entity validation 构建的结构证据，能否校准 LLM-KGQA 的 frontier reflection 决策，并减少无效继续搜索和错误回溯？

### 2.2 假设

- **H1：继续/停止判断**。当当前 frontier 存在已验证但尚未探索的路线时，结构证据可以降低 premature stop；当没有可支持路线时，可以减少无效 continuation。
- **H2：回溯选择**。基于 witness、coverage、branching 和当前搜索状态的结构证据，可以提高有效回溯率，减少回溯到死路实体。
- **H3：阶段级归因**。如果最终 EM/F1 出现提升，该提升应能同时在 reflection decision、搜索深度、实体展开数或死路比例上观察到机制证据。
- **H4：证据来源有效性**。真实结构证据的效果应优于 shuffled evidence、token-matched irrelevant evidence 和无 evidence 基线。
- **H5：来源差异**。KG-intrinsic memory 与 trajectory-derived memory 的增益模式不同；二者可能互补，但不能将历史轨迹记忆的效果误归因于 KG structural memory。

V2 不再把“first-hop gold relation recall 提升”作为 KG structural memory 的必要成功条件；该指标只作为历史分支的归档诊断指标。

## 3. 实验边界

### 3.1 保留内容

- 继续使用已有 schema profile、path template、coverage 和 witness 记录；
- 保留 discovery/validation entity split；
- 保留 JSONL、manifest、memory hash 和 SPARQL replay 机制；
- 保留现有 PoG B0 作为主基线；
- 保留 `pog_trace.jsonl` 的 relation、entity、reflection 和结果级记录；
- 继续禁止使用 benchmark test 的 question、answer、gold path 或 gold SPARQL 构建持久记忆。

### 3.2 V2 暂不包含

- 不修改 `relation_search_prune()` 的候选排序；
- 不改变 first-hop relation 候选集合；
- 不使用 path template 提升 relation score；
- 不将 memory evidence 注入最终 answer reasoning；
- 不将当前问题的 ephemeral cache 直接晋升为全局 memory；
- 不启动 self-play、在线经验演化或 LLM 微调；
- 不在 hard150 上继续选择 fusion 超参数后再宣称最终泛化结果。

## 4. 数据划分与评测协议

### 4.1 hard150 的重新定位

`hard150_v1.json` 已经被多次用于诊断、fusion 分析和阶段决策，因此从 V2 起只能定义为：

```text
development stress slice
```

它可以用于：

- smoke test；
- trace 检查；
- 选择 reflection 阈值；
- 发现错误模式。

它不能用于：

- 最终论文主结果；
- 在看过结果后冻结最终方法并继续报告为 test performance；
- 支撑跨数据集泛化结论。

### 4.2 新增 final unseen split

V2 必须新增一份完全冻结的 `final_unseen` 切片。该切片在以下内容全部冻结后才能运行：

- memory build hash；
- reflection evidence schema；
- confidence gate；
- prompt 模板；
- top-k；
- timeout/retry policy；
- 统计分析脚本。

`final_unseen` 不参与阈值选择和错误案例驱动的代码修改。

### 4.3 分母和异常处理

所有结果必须同时报告：

- 全部问题分母，例如 `n=150`；
- reflection-valid 分母；
- relation-valid 分母，例如当前日志中的 `143/150`；
- timeout、retry、空结果和异常终止数量。

每个实验组必须使用相同的：

- LLM endpoint；
- temperature；
- max token；
- request timeout；
- retry 次数和退避策略；
- KG query budget；
- 题目顺序或随机种子。

## 5. 记忆定义与证据约束

### 5.1 V2 的记忆单元

V2 不把 memory 视为“应该继续”或“应该停止”的历史结论，而定义为可重放的结构证据：

```text
Structural Evidence
= current applicability
  + candidate relation/path
  + validation support
  + coverage
  + expected branching cost
  + witness/provenance
  + uncertainty
```

建议运行时视图至少包含：

```json
{
  "memory_id": "...",
  "source_type": "...",
  "candidate_entity": "...",
  "relation_path": ["..."],
  "target_type": "...",
  "validation_coverage": 0.0,
  "entity_support": 0,
  "median_branching": 0.0,
  "confidence": 0.0,
  "witness": {"query_hash": "...", "path": ["..."]},
  "applicability": 0.0,
  "status": "validated"
}
```

### 5.2 Evidence gate

结构证据只有满足以下条件时才能产生正向干预：

1. 当前 entity/type/direction 与记录匹配；
2. relation/path 在当前 KG 候选或当前 frontier 上真实存在；
3. 至少有 validation support；
4. witness 可以重放，或有明确的可重放查询模板；
5. `confidence * applicability` 高于冻结阈值。

如果没有满足条件的证据：

- 不提升候选；
- 不把未观察到写成不存在；
- 不生成“应该继续”的语言结论；
- 回退到 B0 的 reflection 输入。

### 5.3 Reflection score

对候选路线或 frontier entity `x`，记录以下诊断分数：

```text
evidence_score(x)
  = validation_coverage(x)
    * confidence(x)
    * applicability(x)

utility_score(x)
  = evidence_score(x)
    / (1 + expected_branching(x))
```

该分数只用于生成 evidence summary 和候选回溯排序，不直接代替 LLM 的最终判断。所有被提升的候选必须在 trace 中保存原始分数、证据和后续结果。

## 6. V2 的主要注入点

### 6.1 Decision A：是否继续探索

在 `PoG/utils.py::if_finish_list()` 的 Decision A 之前构造 reflection evidence。

输入包括：

- 当前问题和子问题；
- 当前 frontier entity；
- 已探索 relation/entity/path；
- 当前搜索深度；
- 尚未探索的候选路线；
- validated structural evidence。

输出只包含分组后的证据：

```text
validated unexplored routes
unknown routes
already explored routes
high-cost / high-branching routes
```

输出不允许直接包含：

```text
continue = true
stop = true
backtrack = entity_x
```

这些仍由 PoG 的 reflection decision 产生。

### 6.2 Decision B：选择回溯实体

在 Decision B 之前，对当前 frontier 中的候选回溯实体构造结构证据摘要。

每个候选实体至少记录：

- 尚未探索的 validated relation/path 数量；
- 最高 evidence score；
- 预计 branching；
- witness 数量；
- 是否为重复实体或已确认死路；
- 实际回溯后是否发现新 triple、是否最终找到答案。

V2 的主要方法贡献应集中在这一阶段：**用经过验证的 KG 结构证据校准 reflection，而不是让结构记忆直接替代 LLM 的 relation 选择。**

### 6.3 明确不修改 first-hop

V2 运行时要求：

- `relation_search_prune()` 使用 B0 的候选顺序；
- 不加载 M1/M2 的 first-hop rerank score；
- 不将 path template 的 tail semantic 注入 relation prompt；
- 关闭 memory 时必须与已有 B0 等价。

## 7. 实验组与对照矩阵

### 7.1 MVP 实验组

| ID | 配置 | 目的 |
|---|---|---|
| `R0` | B0，无 reflection memory | 主基线 |
| `R1` | 真实 KG evidence -> Decision A | 测试继续/停止判断 |
| `R2` | 真实 KG evidence -> Decision B | 测试回溯实体选择 |
| `R3` | R1 + R2 | 测试 reflection 联合效果 |
| `RC1` | shuffle reflection evidence | 排除任意额外证据文本 |
| `RC2` | token-matched irrelevant evidence | 排除 token、格式和 prompt 长度 |
| `R4` | evidence summary + confidence gate | 测试门控是否减少 harmful intervention |

R4 只有在 R1/R2 的 trace 证明存在 harmful intervention 后才启动，不作为默认第一版实现。

### 7.2 来源对照

在 MVP 形成阶段级正结果后，再增加：

| ID | 记忆来源 | 目的 |
|---|---|---|
| `RT` | trajectory-derived reflection memory | 对照历史 QA 轨迹经验 |
| `RK` | KG-only validated evidence | 本文主方法 |
| `RKT` | KG evidence + trajectory memory | 判断互补性 |
| `RCache` | 原始邻域缓存，无统计证据 | 排除仅减少 KG 查询的解释 |

`RT` 可以采用与当前 PoG 兼容的 matched implementation，不要求复现其他工作全部工程细节；报告中必须明确标记为 trajectory-memory style baseline。

### 7.3 历史分支归档

旧实验组的结果继续保留：

- `B0/B1/B2`：原始和 supervised memory 对照；
- `M1/M2/C1/C2`：first-hop branch archive。

它们不再与 V2 的 R0-R4 混合计算主结论。

## 8. 评价指标

### 8.1 最终问答指标

- EM；
- F1；
- answerable/unanswerable 子集；
- 全部问题和 reflection-valid 子集分别统计。

### 8.2 Decision A 指标

- premature stop rate：仍存在有效路线却停止；
- wasteful continuation rate：没有有效证据却继续扩大；
- continue decision accuracy；
- decision 后是否产生新 triple；
- decision 后是否找到答案；
- 平均和中位搜索深度。

### 8.3 Decision B 指标

- 有效回溯率：回溯后发现新 triple 或进入正确路线；
- 无效回溯率；
- 回溯实体的 evidence-score ranking；
- 回溯后到答案的深度；
- frontier entity expansion 数。

### 8.4 效率指标

- LLM calls；
- KG queries；
- tokens；
- latency；
- 展开 relation/entity 数；
- timeout/retry 数。

### 8.5 机制归因

任何 final EM/F1 变化都必须同时检查：

1. Decision A/B 是否实际改变；
2. 改变是否由真实 evidence 触发；
3. change 后是否出现新 triple 或有效路径；
4. shuffled/irrelevant control 是否产生相同变化；
5. 增益是否只来自额外 token 或随机性。

不能仅凭 final EM 高于 B0 就判定 memory 有效。

## 9. Trace 与实现要求

### 9.1 允许修改的模块

优先修改：

| 文件 | 修改内容 |
|---|---|
| `PoG/utils.py` | 在 Decision A/B 前构造结构 evidence，不改变 first-hop |
| `PoG/trace_utils.py` | 记录 evidence、decision、后果和回溯结果 |
| `PoG/kg_memory_retrieval.py` | 增加 reflection-stage retrieval 和 evidence gate |
| `PoG/reflection_structural_memory.py` | 构造 Decision A/B 的结构摘要 |
| `PoG/analyze_kg_memory_run.py` | 计算 reflection 和后果指标 |
| `PoG/main_freebase.py` | 增加 `reflection_memory_mode`、split、seed 和异常配置 |

V2 不修改 `freebase_func.py` 的 first-hop relation score 逻辑。

### 9.2 每次 reflection event 必须记录

```json
{
  "question_id": "...",
  "depth": 0,
  "stage": "reflection_a | reflection_b",
  "memory_mode": "none | kg_structural | shuffle | irrelevant",
  "candidate_frontier": ["..."],
  "evidence_items": [
    {
      "memory_id": "...",
      "applicability": 0.0,
      "coverage": 0.0,
      "confidence": 0.0,
      "branching": 0.0,
      "witness_replayable": true
    }
  ],
  "prompt_visible_evidence": true,
  "llm_decision": "...",
  "selected_entity": "...",
  "post_decision_new_triples": 0,
  "post_decision_found_answer": false,
  "timeout": false,
  "retry_count": 0
}
```

### 9.3 语义过滤边界

reflection evidence 必须明确发生在 `semantic_filter_relations()` 之前还是之后。建议 V2 初版使用：

```text
先完成 PoG 原始候选和语义过滤
    -> 再对剩余 frontier 构造结构 evidence
    -> evidence 只影响 reflection summary 和回溯排序
```

这样可以避免出现 first-hop 实验中“memory order 改变但 LLM 实际选择没有同步改变”的归因问题。

## 10. 分阶段执行计划

### V2-0：协议审计与实现冻结

目标：不新增 first-hop 实验，补齐 V2 的可解释性和公平性。

任务：

1. 将 hard150 标记为 `development_stress`；
2. 准备并冻结 `final_unseen`；
3. 解释 150 与 143 两种分母；
4. 统一 timeout/retry/max-token 配置；
5. 检查 `semantic_filter_relations()` 与 reflection evidence 的顺序；
6. 增加 Decision A/B 的 evidence trace schema；
7. 校验 memory-off 与 B0 的行为等价性。

验收：没有新增 first-hop 跑数；审计脚本和 trace checker 全部通过。

### V2-1：Reflection evidence 单元测试

任务：

1. 构造有 witness 的正例；
2. 构造无 witness 的 unknown 例；
3. 验证低 confidence 不产生正向干预；
4. 验证已探索路线不会被重复推荐；
5. 验证 branching penalty 和 evidence score 边界；
6. 验证 shuffle/irrelevant 只改变 evidence 内容，不改变候选数量和基础 prompt 结构。

验收：schema、score、fallback、trace 和 B0 equivalence checker 通过。

### V2-2：Reflection smoke 与开发切片 pilot

顺序固定为：

```text
R0/R1/R2/RC1/RC2 smoke n=20
    -> 修复协议错误
    -> R0/R1/R2/RC1/RC2 hard150 development n=150
```

hard150 结果只能用于选择 confidence threshold、evidence top-k 和 prompt 压缩方式，不能作为最终论文主结果。

### V2-3：冻结 final unseen

仅当 V2-2 满足效果门槛后执行。冻结所有代码、memory hash、prompt、参数和分析脚本，然后在 `final_unseen` 上一次性运行 R0-R3 及必要的 RC1/RC2。

### V2-4：跨数据集和来源对照

在 final unseen 上确认 reflection 分支有效后：

1. 一次构建 Freebase KG memory；
2. 冻结 memory 后分别迁移到 WebQSP、CWQ、GrailQA；
3. 增加 `RT`、`RK`、`RKT` 和 `RCache`；
4. 比较 KG-only、trajectory-only 和融合记忆。

### V2-5：Self-play（可选）

只有在以下条件同时满足时才允许：

- KG-only reflection evidence 在 final unseen 上优于 RC1/RC2；
- Decision A 或 B 至少一个有阶段级正归因；
- 效率没有因 memory 显著恶化；
- provenance、witness replay 和 split 隔离完整。

Self-play 作为扩展实验，不作为 V2 的第一主贡献。

## 11. 阶段门槛与停止条件

### 11.1 V2-2 进入 final unseen 的门槛

R1、R2 或 R3 至少满足以下一项，并且不出现明显 harmful intervention 增加：

- reflection decision 指标稳定改善；
- final EM/F1 在 development stress slice 上提升，且提升可由 Decision A/B 变化解释；
- final accuracy 持平但无效 continuation、无效 backtracking 或 entity expansion 明显下降；
- real evidence 明显优于 RC1/RC2。

### 11.2 必须停止的情况

出现以下任一情况，停止当前 reflection 变体并回到诊断：

- R1/R2/R3 与 RC1/RC2 无差异；
- evidence 频繁推动无 witness 或低 confidence 路线；
- premature stop 与 wasteful continuation 同时上升；
- memory 只增加 tokens/calls/latency，没有阶段级收益；
- post-decision outcome 无法与 evidence intervention 对齐；
- final unseen 之前出现阈值或 prompt 调整。

### 11.3 研究方向停止条件

只有在以下条件都满足时，才允许把结论升级为“KG structural memory 方向不成立”：

1. first-hop relation branch 已失败；
2. reflection-only branch 在独立 final unseen 上失败；
3. KG-only 与 trajectory-only/fusion 对照已完成；
4. 失败不是由 coverage、timeout、分母或实现顺序问题造成。

## 12. 预期产物

新增或修改：

```text
clue_on_graph/PoG/
├── reflection_structural_memory.py
├── analyze_reflection_memory_run.py
├── check_reflection_memory.py
└── run_PoG_reflection_memory_v2.ps1
```

实验产物：

```text
<run_dir>/
├── results.jsonl
├── pog_trace.jsonl
├── run_meta.json
├── reflection_decision_metrics.json
├── reflection_memory_cases.jsonl
└── failure_and_timeout_report.json
```

## 13. 论文主结论的预设边界

在没有完成 V2-3 之前，不得声称：

- KG structural memory 普遍增强 PoG；
- M1 first-hop relation rerank 有效；
- path template memory 能改善 relation selection；
- self-play memory 比静态 KG memory 更有创新性或更有效。

如果 V2-3 成功，论文主张应限定为：

> 经过独立 KG 探测和 held-out validation 的结构 evidence，可以在不改变 first-hop relation selection 的条件下，校准 PoG 的 reflection/frontier decision，并在未参与 memory 构建的评测切片上减少无效搜索。

如果 V2-3 失败，论文仍可以将 first-hop 和 reflection 两条分支整理为负结果或诊断性研究，但不能把 hard150 上 M1 的 +3 EM 作为结构 memory 的正向证据。

