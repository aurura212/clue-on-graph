# SP2-B：无 Self-Play Experience Memory 的 LLM+KG 端到端基线 Rollout

> 文档编号：SP2B-PLAN  
> 版本：1.1  
> 初始制定日期：2026-08-22  
> 状态：已登记，尚未运行  
> 当前阶段：SP2-B 启动准备  
> 上位约束：`00_experiment_overall_requirements.md` v1.15  
> 前置计划：`01_SP0_protocol_workspace_and_data_contract.md`、`02_SP1_pog_adapter_and_environment_binding.md`、`03_SP2A_live_kg_environment_validation.md`、`03A_SP2A_supplement_tail_and_dynamic_multihop.md`  
> 前置报告：`reports/sp2a/SP2A_experiment_report.md`  
> 前置结论：SP0 PASS；SP1 PASS；SP2-A 主实验与补充实验均 PASS 并完成收口

## 1. 本步骤定位

SP2-B 是首次正式使用 LLM 与 live KG 联合进行 PoG 推理的基线阶段。其任务不是验证 Self-Play Experience Memory 是否有效，而是先回答一个更基础的问题：

> 在不读取或写入 Self-Play Experience Memory、不使用 Oracle 派生信息、不进行 Self-Play 纠错的条件下，原 PoG 能否在真实 KG 上由 LLM 驱动完成可执行、可终止、可保存、可重放的端到端推理？

self-play/ 下已有代码仍是本实验使用的原 PoG 基线。SP2-B 允许在 self-play/ 内增加适配、调用、日志和验证代码，但不得把原 PoG 基线改写成 memory 模型，也不得在本阶段提前生成或注入 Self-Play 经验。

SP2-B 的产物是后续 SP3 经验生成的**无 Self-Play Experience Memory 在线基线和运行协议**。SP2-B 通过不等于 Self-Play memory 有效，也不产生 KGQA 增益结论。

## 2. 实验问题与具体目标

### 2.1 核心实验问题

在真实 KG 环境已通过 SP2-A 验证的前提下，LLM 是否能根据问题和当前 O0 可见状态选择合法 PoG 动作，并完成至少一轮真实的关系选择、KG 执行、状态更新、继续/停止和答案提交？

### 2.2 具体目标

1. 建立固定模型、prompt、动作协议、预算和 live KG 配置下的无 Self-Play Experience Memory 端到端 rollout。
2. 验证 LLM 输出能被解析为严格的 PoG 合法动作，非法输出不会直接发起 KG 请求。
3. 验证 relation selection、continue/stop 和 answer submission 在 live KG 上的调用边界、状态变化和终止条件。
4. 验证每题都能保存完整的 question、可见状态、LLM 请求/响应摘要、动作、KG I/O、状态转移、预算、答案提交和最终结果。
5. 验证同一冻结输入和配置下，轨迹可以通过 recorded I/O 离线重放；重放不调用网络、LLM 或 Self-Play Experience Memory。
6. 统计无 Self-Play Experience Memory 原 PoG 的端到端成功率、失败类型、搜索成本和可重放率，为 SP3 discovery 和后续 memory 对照提供基线。
7. 明确当前在线基线的失败边界，避免把接口故障、LLM 格式错误、答案抽取失败和 KG 无结果混为同一类失败。

## 3. 本阶段明确不做

- 不读取、检索、生成、蒸馏、promotion 或注入任何 Self-Play Experience Memory。
- 不启动 Self-Play 的 Critic 纠错循环，不做 Oracle-guided teacher，不进行反事实经验比较。
- 不把成功轨迹、失败轨迹、LLM 总结或 KG 返回写入 candidate experience 或正式 memory。
- Actor/Explorer 和 LLM 只能读取 O0 信息以及当前题目内由原 PoG 产生的临时工作状态；不得看到答案、witness、gold path、benchmark label、Verifier 结果或未来状态。
- Verifier 可以在后台读取必要答案或逻辑约束完成判定，但其信息不得回流到 Actor/Explorer 的当前 rollout。
- 不使用 WebQSP model-compare 150 或 CWQ model-compare 50 做正式效果对比。
- WebQSP smoke 20 只能在独立开发任务流程稳定并满足本计划门槛后进行一次冻结数据冒烟；不得根据结果调 prompt、模型、预算或动作协议。
- 不声称 SP2-B 证明了 Self-Play memory 增强，也不将本阶段结果与 SP5 的正式 memory 对比混为一谈。
- 不修改 `clue_on_graph/data/`、`clue_on_graph/cope_alias/` 或原 PoG 基线文件；新增代码和产物只能写入 `self-play/`。
- 不把 `BACKTRACK(state)` 尚未支持的问题伪装成已通过；若 rollout 触发该动作，必须记录为受控 unsupported 或按预注册 fallback 规则处理。

### 3.1 Memory 边界：原 PoG 题内工作记忆与 Self-Play 经验记忆分离

当前原 PoG 的 `main_freebase.py` 会为每道题创建 `mem` 文件，并由 `update_memory()` 写入当前题目已经探索到的三元组摘要，再由 `reasoning()` 和 `if_finish_list()` 读取。这是原 PoG 的**题内临时工作记忆**，不是本研究要验证的 Self-Play Experience Memory。

SP2-B 必须执行以下边界：

| 类型 | SP2-B 要求 |
|---|---|
| `pog_working_memory` | 允许；仅限当前题目、当前 Run；Run 开始时为空；保存到 `runs/<run-id>/scratch/`；不得跨题或跨 Run 复用 |
| `candidate_experience` | 禁止读取和写入 |
| `promoted_memory` | 禁止读取和写入 |
| 历史任务经验检索 | 禁止 |
| Oracle/Verifier 派生经验 | 禁止进入 Actor/Explorer/LLM view |

因此，验收指标中的“memory 为 0”具体指 Self-Play Experience Memory 的读写为 0，而不是删除原 PoG 必需的题内工作状态。工作记忆的创建、读取、写入和删除必须全部记录到审计日志。

## 4. 前置条件与冻结输入

正式实现或运行前，必须重新读取 overall 和本计划，并在 run manifest 中记录：

1. overall 当前版本、SP2-B 计划文件哈希和阶段登记状态。
2. SP2-A 主实验有效 Run：`sp2a-20260822T082704Z-28a5bc97`。
3. SP2-A 补充有效 Run：`sp2a-supp-20260822T111116Z-79aa8ea8`。
4. SP2-A 报告路径及登记的 LF 字节 SHA-256：`0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`。
5. 协议版本 `sp-protocol-v1`、PoG baseline inventory、SP1 adapter/environment binding、SP2-A live KG 配置和 supplement 结论。
6. live endpoint、snapshot/endpoint 标识、超时、重试、KG/action/depth budget 和只读设置。
7. LLM provider、model identifier、temperature、最大输出长度、请求超时、重试和 prompt 版本。密钥不得写入配置快照、日志或报告。
8. 当前工作树 commit、dirty status、原 PoG baseline 文件哈希、SP2-B 配置哈希和任务 registry 哈希。
9. `data/`、`cope_alias/` 和冻结评测集的只读快照；只记录哈希，不复制或读取评测答案生成开发动作。

## 5. 实验对象与数据分层

SP2-B 使用三类任务，必须分别登记、分别报告，不得把不同阶段的结果混合统计。

| 层级 | 数据 | 用途 | 是否允许进入下一层 |
|---|---|---|---|
| B0 | 少量人工核查任务，建议 3–5 条 | 检查 prompt、动作解析、live KG、状态和答案提交的端到端链路 | 只有 B0 无阻塞问题才进入 B1 |
| B1 | 独立开发任务 registry，建议 20 条左右 | 建立无 memory 在线基线、发现系统性失败、冻结运行配置 | 只有 B1 满足稳定性门槛才进入 B2 |
| B2 | 已在 SP0 冻结的 `webqsp_smoke_20.jsonl` | 运行前冒烟检查，不作正式模型对比 | 不得据此调参或生成 memory |

### 5.1 B0 人工核查任务

B0 任务必须覆盖至少：

- 一跳实体关系查询；
- 至少一条两跳或连续状态更新任务；
- 至少一条 literal/答案提交任务；
- 至少一个空结果或提前停止边界。

B0 只用于检查链路，不能用来宣称模型性能。每条任务必须有稳定 task ID、问题、初始实体或实体链接输入、允许关系候选、预期动作空间和后台 Verifier 判定字段。

### 5.2 B1 独立开发任务

B1 任务必须独立于 WebQSP/CWQ 冻结评测样本，不得从评测问题、答案、gold path 或测试轨迹反向构造。任务 registry 至少记录：

- `task_id`、question、初始可见实体/候选实体；
- 允许关系和方向，或由原 PoG 的关系候选过程产生的合法动作集合；
- 是否允许多跳、最大深度和预算；
- 预期答案类型和后台验证规则；
- 来源、曝光状态、与冻结评测集的 exclusion 检查结果。

任务 registry 不得向 Actor/Explorer 提供答案、witness、gold path 或 Verifier 标签。

### 5.3 B2 WebQSP smoke 20

只有 B0、B1 的稳定性门槛全部通过后，才允许读取固定的 `artifacts/datasets/webqsp_smoke_20.jsonl`。B2：

- 只使用 SP0 已冻结的 20 条题目；
- 不重新抽样、不替换、不补题；
- 使用与 B1 完全相同的已冻结模型、prompt、预算和动作协议；
- 运行前记录数据文件、manifest 和 SHA-256；
- 结果仅用于冒烟和链路检查，不用于调参或生成 memory；
- 若 B2 失败，保留全部失败轨迹，不能重新抽样后宣称通过。

## 6. LLM、KG 与信息权限

### 6.1 LLM 输入

LLM 每一步只允许接收：

- 原始问题及已允许的实体链接结果；
- 当前 PoG VisibleState、frontier、已执行动作和可见 KG 返回；
- 当前合法候选动作集合；
- 剩余 KG/action/depth budget；
- 固定的任务协议和输出 schema。

禁止输入：答案、witness、gold path、测试标签、Verifier 结果、Oracle 派生局部进展、未来 KG 返回、memory 内容和未执行动作的结果。

### 6.2 LLM 输出

LLM 输出必须通过结构化解析和 action validator。建议最小输出 schema：

```json
{
  "action_type": "SELECT_RELATION|EXPAND|STOP|SUBMIT_ANSWER",
  "relation": "optional relation id",
  "direction": "head|tail",
  "entity": "optional visible entity id",
  "answer": "optional answer candidate",
  "rationale": "optional non-authoritative text"
}
```

实际 schema 必须以现有 PoG adapter 和本阶段冻结配置为准。`rationale` 不能作为 Verifier 证据，也不能写入 candidate memory。解析失败、字段缺失、非法 relation、非法方向、不可见 entity 和越过预算的动作必须在 KG 调用前拒绝并分类。

### 6.3 KG 执行

只有通过 validator 的动作才能调用 SP2-A 已验证的 live Environment。每次 KG 调用必须记录：request hash、response hash、方向、canonical triples、VisibleState 更新、logical/physical/retry 计数和预算变化。LLM 不得直接拼接任意 SPARQL。

## 7. 需要实现或补充的代码功能

所有新增代码只能位于 `self-play/`，并优先复用 SP1/SP2-A 已验证模块：

1. **LLM client wrapper**：统一模型调用、超时、重试、请求/响应摘要、模型版本和 token/cost 记录；禁止写入 secret。
2. **O0 prompt builder**：从当前 PoG 状态生成不含 Oracle、test-label、历史任务经验和 Self-Play Experience Memory 的 prompt；允许包含当前题目的 `pog_working_memory`，并记录 prompt hash。
3. **Structured action parser**：解析 LLM 输出并交给现有 action validator；非法输出不能进入 KG。
4. **Rollout controller**：执行 question -> LLM action -> validator -> live KG -> state update -> next decision 的循环。
5. **Stop/answer adapter**：将原 PoG 的 continue/stop 和 answer submission 接入统一轨迹格式；明确 stop、answer extraction 和 no-answer 状态。
6. **Memory/Oracle boundary guard**：阻止 candidate/promoted memory 和 Oracle 信息回流；同时审计 `pog_working_memory` 的题内生命周期和跨题隔离。
7. **Trace and manifest writer**：保存逐步 trace、LLM/KG 调用计数、预算、异常、最终结果和输入/配置哈希。
8. **Recorded I/O replay**：至少支持 KG I/O replay；若 LLM replay 使用响应缓存，必须标记为 replay，不得计作真实 LLM 调用。
9. **Baseline integrity check**：运行前后比较原 PoG baseline inventory，确认没有未登记修改。
10. **B0/B1/B2 check runner**：对三个层级分别输出 manifest、结果和可审计指标，不覆盖历史 Run。

若原 PoG 已有对应功能，优先建立 adapter/wrapper 和测试，不重复改写原 PoG 核心逻辑。

## 8. 实验执行步骤

### B2B.0：启动前协议与环境检查

本步骤的目标是确认“可以开始正式 rollout”，而不是回答任何 KGQA 性能问题。

1. **读取和登记约束**：读取 overall v1.15、本计划 v1.1、SP1/SP2-A 计划与报告；计算并登记文档、baseline inventory、配置和任务 registry 哈希。
2. **冻结运行配置**：冻结 LLM provider/model、temperature、最大输出长度、超时、重试、PoG depth、KG/action/physical budget、prompt 版本和 endpoint 标识；密钥只从环境变量读取，不进入产物。
3. **冻结任务入口**：登记 B0/B1 task registry；确认 B1 与 WebQSP/CWQ 固定评测集无重叠；B2 只指向已有的 `webqsp_smoke_20.jsonl`。
4. **检查原 PoG 依赖**：验证 `main_freebase.py` 相关模块、SentenceTransformer、LLM client、SPARQL endpoint 和 `data/cope_alias` 只读访问；不执行全量数据集。
5. **检查路径隔离**：将原 PoG 的 `mem` 重定向为 `runs/<run-id>/scratch/<task-id>/pog_working_memory`，结果、日志和缓存全部写入当前 Run；确认不会写入 `data/`、`cope_alias/` 或项目外部目录。
6. **运行边界检查**：确认 `allow_self_play_experience_memory=false`、`allow_oracle_in_actor=false`、`allow_live_kg=true`、`allow_llm=true`，并确认失败不会通过无限重试被掩盖。
7. **生成启动 manifest**：只有以上检查全部通过，才生成 `runs/<run-id>/manifest.json`，进入 B0。

### B2B.1：B0 少量人工核查

本步骤按单题逐条执行，不并发、不自动补题。

1. 对每个 B0 task 建立独立的 `task-run` 和空的 `pog_working_memory`。
2. 调用原 PoG 的问题分解和关系候选逻辑，记录第一条 LLM 请求、可见状态和 prompt hash。
3. 解析 LLM 输出；验证 action schema、关系方向、实体范围和预算。非法动作只能被记录和拒绝，不能发起 KG 请求。
4. 对合法 relation/entity action 调用 SP2-A 已验证的 live KG Environment，记录 request/response hash、canonical triples 和状态更新。
5. 至少完成一轮“关系选择 → KG 查询 → 状态更新”；两跳任务必须使用第一跳真实返回继续下一步。
6. 调用原 PoG 的 stop/answer 逻辑，记录答案提交、终止原因和后台 Verifier 结果；Verifier 结果不得回流到本题后续 LLM 请求。
7. 离线重放该题，确认动作序列、状态、KG 结果、预算和终止状态一致。
8. B0 的退出门槛：所有任务可终止、无泄漏、无非法 KG 请求、轨迹完整；否则保留 INVALID Run，修复后重新生成新 Run，不覆盖旧记录。

### B2B.2：B1 独立开发任务基线

1. 在 B0 通过后冻结 B1 registry、模型、prompt、预算、重试和动作协议；不得从 B1 结果反向改题。
2. 对全部约 20 条 B1 任务运行独立的无 Self-Play Experience Memory rollout；每题开始时清空 `pog_working_memory`。
3. 每题记录成功/失败、答案、轨迹长度、LLM 调用数、KG logical/physical/retry 次数、token/cost、终止原因和失败分类。
4. 对每个 task-run 执行 recorded I/O replay；比较 action sequence、VisibleState、canonical KG 结果、计数、预算和终止状态。
5. 对失败进行分类：`action_space_failure`、`explorer_failure`、`answer_extraction_failure`、`system_failure` 或 `invalid_task`；不得用增加 retry、预算或人工改题掩盖失败。
6. B1 的退出门槛：无泄漏、无非法 KG 请求、所有有效任务有完整 trace 或明确 INVALID 原因、replay 一致率达到计划门槛、未分类异常为 0。

### B2B.3：B2 WebQSP smoke 20

1. 只有 B0/B1 的退出门槛全部满足后，才允许读取冻结的 `webqsp_smoke_20.jsonl`。
2. 运行前重新校验数据 manifest、overall、本计划、配置、prompt 和 baseline inventory 哈希。
3. 使用与 B1 完全相同的模型、prompt、预算、动作协议和错误处理规则；不使用 WebQSP 150 或 CWQ 50。
4. 对 20 条题分别记录完整 trace、终止原因、答案和失败分类；不根据单题结果调参、不重新抽题、不补题。
5. B2 只用于验证固定评测集上的运行链路和冒烟稳定性，不作为 SP2-B 的正式 EM/F1 结论，也不生成 Self-Play 经验。

### B2B.4：阶段验收与收口

1. 汇总 B0、B1、B2 的有效/无效 Run、失败分类、可重放结果、成本和未解决风险；三类任务分别报告。
2. 单独审计 `pog_working_memory`：是否题内隔离、是否跨题复用、是否写入 Self-Play memory 目录。
3. 运行 baseline integrity check，确认原 PoG 基线、`data/`、`cope_alias/` 无未登记修改。
4. 生成 `reports/sp2b/SP2B_experiment_report.md`，登记报告 SHA-256、配置/模型/prompt/任务 registry 哈希和有效 Run ID。
5. 在本计划日志区追加运行记录和验收结论；只有报告、日志和证据索引完成后，才判断是否允许进入 SP3。

## 9. 验收指标与通过门槛

SP2-B 的 PASS 关注端到端合法性、隔离性和可复现性，不要求达到某个预先设定的 EM/F1，也不要求所有题目都回答正确。

| 指标 | 通过门槛 |
|---|---:|
| 真实 LLM 调用计数 | 与 manifest 和 provider 账单/响应记录一致；不得出现未记录调用 |
| Self-Play Experience Memory 读/写 | 0 |
| `pog_working_memory` 跨题/跨 Run 复用 | 0 |
| `pog_working_memory` 生命周期审计覆盖率 | 100% |
| Oracle/test-label 进入 Actor/LLM view | 0 |
| 非法动作进入 KG | 0 |
| B0 端到端可终止率 | 100% |
| B1 轨迹完整率 | 100% 的有效任务有完整 trace 或明确 INVALID 原因 |
| B1 可重放率 | ≥ 95%；关键状态/动作/计数差异必须为 0，剩余差异须分类说明 |
| 预算越界物理 KG 请求 | 0 |
| 未分类异常 | 0 |
| baseline 未登记变化 | 0 |
| `data/`、`cope_alias/` 写入 | 0 |
| B2 smoke 运行 | 仅在 B0/B1 稳定后执行；不以准确率作为 SP2-B 唯一门槛 |

以下任一情况出现时，SP2-B 不得 PASS：LLM/Oracle/Self-Play Experience Memory 泄漏、题内工作记忆跨题复用、非法动作进入 KG、不可重放、无法终止、未分类 system failure、baseline 或只读输入被未登记修改、关键调用或配置无法审计。

## 10. 失败分类与处理

所有失败必须至少归入：

- `invalid_task`：任务或 registry 本身不合法；
- `action_space_failure`：LLM 输出无法映射到合法动作；
- `budget_insufficient`：预算不足导致无法完成；
- `explorer_failure`：LLM 在合法动作空间内未找到可行路线；
- `critic_recovery_failure`：本阶段默认不启用 Critic；如出现该字段，必须说明是误触发还是后续模块残留；
- `answer_extraction_failure`：KG 路线完成但答案解析/提交失败；
- `system_failure`：endpoint、LLM 服务、解析器、文件或运行时系统异常。

不得通过增加 retry、预算、prompt 轮数或人工改题掩盖失败。修复配置后必须生成新 Run，旧失败 Run 只追加说明，不覆盖。

## 11. 预期产物

```text
self-play/
├── exp_plan/04_SP2B_llm_kg_baseline_rollout.md
├── configs/sp2b_llm_kg_baseline_v1.json
├── prompts/sp2b_actor_v1.*
├── artifacts/registries/sp2b_b0_manual_tasks_v1.json
├── artifacts/registries/sp2b_b1_development_tasks_v1.json
├── artifacts/registries/sp2b_exposure_registry_v1.json
├── runs/sp2b-<timestamp>-<suffix>/
├── artifacts/protocol/sp2b_check_result.json
└── reports/sp2b/SP2B_experiment_report.md
```

B2 使用已有冻结文件：

```text
self-play/artifacts/datasets/webqsp_smoke_20.jsonl
```

不得在 `data/` 或 `cope_alias/` 下写入任何新增产物。

## 12. 实验日志（运行后追加）

本节只追加，不覆盖失败记录。每次实现或运行至少记录：

- 日期、Run ID、计划版本、overall 版本和 Git commit/dirty status；
- 模型标识、provider、temperature、prompt 版本、配置和任务 registry 哈希；
- endpoint/snapshot、数据 manifest/hash、baseline inventory/hash；
- 每题逐步 trace、LLM 请求/响应摘要、prompt hash、动作解析结果、KG request/response hash；
- state_id、VisibleState、frontier、depth、logical/physical/retry 计数和预算变化；
- 终止原因、答案提交、Verifier 后台结果和失败分类；
- memory、Oracle、test-label、secret 和 data/cope_alias 写入审计结果；
- replay 结果、指标、异常和是否为有效 Run；
- 最终结论：PASS、CONDITIONAL PASS、FAIL 或 INVALID。

### 12.1 运行日志占位

<!-- 在首次实现或运行后追加 LOG-SP2B-001。计划预注册内容不得无记录地覆盖。 -->

### 12.2 计划变更记录

| 日期 | 版本 | 修改前 | 修改后 | 原因 | 对可比性的影响 |
|---|---|---|---|---|---|
| 2026-08-22 | 1.0 | 尚未登记 SP2-B 计划 | 登记无 memory 的 LLM+KG 端到端基线计划；分 B0/B1/B2 三层，先独立开发任务再 WebQSP smoke 20 | SP2-A 已完成基础与补充环境验证，需要先建立无 memory 在线基线，再进入 Self-Play 经验生成 | 不改变 SP2-A 历史证据；不产生 memory，不使用 WebQSP 150/CWQ 50 正式对比数据 |
| 2026-08-22 | 1.1 | 将所有 memory 统一按读写为 0 处理，且执行步骤较为概括 | 明确区分原 PoG 题内 `pog_working_memory` 与 Self-Play Experience Memory；将 SP2-B 拆为启动检查、B0、B1、B2 和阶段收口，并补充每步输入、证据、退出门槛与审计要求 | `main_freebase.py` 依赖题内 `mem` 文件完成当前问题的工作状态；需要保持原 PoG 行为，同时避免跨题经验泄漏并使实验可复现 | 不改变研究对象；提高 SP2-B 与原 PoG 行为的可比性，并明确 Self-Play Experience Memory 读写必须为 0 |
