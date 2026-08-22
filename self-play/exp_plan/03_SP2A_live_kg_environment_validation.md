# SP2-A：真实 KG 环境验证

> 文档编号：SP2A-PLAN  
> 版本：1.0  
> 初始制定日期：2026-08-22  
> 状态：已完成 PASS（2026-08-22）  
> 当前阶段：SP2-A（已收口）  
> 上位约束：`00_experiment_overall_requirements.md` v1.10（运行时为 v1.10；收口后升级）  
> 前置必读：`00_experiment_overall_requirements.md`、`01_SP0_protocol_workspace_and_data_contract.md`、`02_SP1_pog_adapter_and_environment_binding.md`、`reports/sp1/SP1_experiment_report.md`  
> 前置结论：SP0 PASS；SP1 PASS；协议 `sp-protocol-v1`、固定评测集和 PoG adapter 已冻结

## 1. 本步骤定位

SP2-A 是 SP1 与首次 LLM+KG 联合 rollout 之间的独立环境验证阶段。本阶段首次允许通过 SP1 已验证的 adapter/environment binding 访问真实 KG，但只执行预先登记的合法 Action，不让 LLM 决定 relation、继续/停止或答案。阶段目标是确认真实 KG 的返回能够被原 PoG 基线上的实验适配层正确、可审计、可重放地处理。

本阶段得到的是“真实 KG 环境可用性与协议正确性”的证据，不是 KGQA 效果，也不是 Self-Play memory 的效果证据。只有 SP2-A 通过后，才允许在 SP2-B 进行无 memory 的 LLM+KG 端到端 rollout。

## 2. 实验问题与具体目标

### 2.1 核心实验问题

> 在不调用真实 LLM、不生成或读取 memory、不给 Actor/Critic 提供 Oracle 信息的条件下，SP1 adapter 能否把预制合法动作稳定地转换为真实 KG 请求，并将真实返回正确地转换为 PoG 可见状态？

### 2.2 具体目标

1. 验证真实 KG endpoint 的只读连通性、请求格式和返回 schema。
2. 验证 `Direction.HEAD` 与 `Direction.TAIL` 到原 PoG `entity_search(..., head=...)` 的映射，确认三元组方向不被反转。
3. 验证真实 KG 原始响应到 canonical triples、实体候选、literal 值和 `VisibleState` 的确定性转换。
4. 验证空结果、重复结果、literal、缺字段、格式错误和 endpoint 异常的分类及状态转移。
5. 验证 logical action、physical request、retry、successful request、empty result 和 failed request 的计数边界。
6. 验证 timeout/retry、KG budget、depth budget 和总 action budget 在边界条件下不会被错误扣减或重复扣减。
7. 验证 live KG recorded I/O 可以离线 replay，replay 结果与在线记录一致。
8. 形成可供 SP2-B 使用的环境接口证据、风险清单和可审计产物，但不提前进入 LLM 推理或 memory 生成。

## 3. 明确不做

- 不调用真实 LLM，不启动 Explorer/Actor、Critic、Distiller 或 Promoter。
- 不生成、读取、检索、注入或更新任何 Self-Play memory。
- 不使用 Oracle 的答案、witness、gold path、benchmark label 或 answer ID 作为 Action 输入。
- 不运行 WebQSP smoke 20、WebQSP model-compare 150 或 CWQ model-compare 50 来生成轨迹或进行 KGQA 效果比较。
- 不把 live KG 结果直接写入 candidate experience，也不把 SP2-A 记录宣称为 Self-Play 经验。
- 不实现真实 `BACKTRACK(state)`；该动作在 SP1 中仍为 unsupported，SP2-A 不得把 `SELECT_FRONTIER` 冒充为 state backtrack。
- 不修改 `clue_on_graph/data/`、`clue_on_graph/cope_alias/` 或原 PoG 基线文件；新增代码、配置、记录和派生数据只能位于 `self-play/`。
- 不把 endpoint 不可用、认证失败或网络阻断伪造为 PASS；这类情况必须按 `system_failure` 或 `INVALID` 记录。

## 4. 前置条件与冻结输入

实施前必须逐项检查并在运行 manifest 中记录：

1. overall 当前版本为 v1.10，当前阶段为 SP2-A，且本计划已经登记。
2. SP1 报告结论为 PASS，SP1 有效 run 为 `sp1-20260822T030044Z-8cb155e0`。
3. `sp-protocol-v1`、SP1 adapter 配置和 PoG decision map 与 SP1 收口记录一致。
4. 三个固定评测文件只校验哈希，不用于 SP2-A 生成动作轨迹：

| 文件 | 用途 | SP2-A 处理 |
|---|---|---|
| `artifacts/datasets/webqsp_smoke_20.jsonl` | 冒烟评测集 | 只登记 exclusion，不读取答案生成 live KG 轨迹 |
| `artifacts/datasets/webqsp_model_compare_150.jsonl` | 正式模型对比集 | 只登记 exclusion，不生成轨迹 |
| `artifacts/datasets/cwq_model_compare_50.jsonl` | 正式模型对比集 | 只登记 exclusion，不生成轨迹 |

5. 已预注册 SP2-A 开发任务清单，或由配置明确列出人工构造的实体/relation case；每个 case 必须有稳定 task ID、查询方向、预制 Action 序列和用途标签。
6. live KG endpoint、schema 版本、超时、重试和只读设置已写入配置；API key、token、Cookie 等 secret 不得进入配置快照、日志或 recorded I/O。
7. 运行前记录工作树 commit/dirty 状态、配置哈希、输入注册表哈希和 endpoint/snapshot 标识。

## 5. 数据与任务边界

### 5.1 SP2-A 开发任务

SP2-A 使用非评测的开发任务，不从 WebQSP/CWQ 冻结评测集重新抽样。推荐每个任务包含：

- 一个已登记的实体或实体对；
- 一个预先登记的 relation；
- 一个明确的 `HEAD` 或 `TAIL` 查询方向；
- 一到两步的合法 Action 序列；
- 预期响应类别（non-empty、empty、literal 或异常）；
- 与任务用途相匹配的 expected schema，不将隐藏答案作为 Actor/Critic 输入。

任务可以来自独立开发查询、人工注册的公开实体/relation 或脱敏 recorded I/O。若任务来源与 benchmark 有重叠，必须通过 exclusion registry 标记并从正式评测与 memory discovery 中排除。

### 5.2 预制动作约束

每个 case 的 Action 序列在运行前冻结。Action 只能包含协议允许的字段，例如：

- `EXPAND(entity, relation, direction)`；
- `SELECT_FRONTIER(frontier_id)`；
- 在验证状态机需要时使用 `CONTINUE`、`STOP`、`ABSTAIN` 的预制动作；
- 不使用真实 LLM 生成的自由文本作为决策来源。

预制动作必须先通过 SP1 action validator，再交给 Environment。任何非法动作都应在调用 KG 前拒绝，并单独计入 action-space 检查，不得产生物理 KG 请求。

## 6. 真实 KG 调用边界与审计要求

SP2-A 的唯一允许调用链为：

~~~text
预制 Action
  -> SP1 Action Validator
  -> Environment binding
  -> 原 PoG KG client / live endpoint
  -> 原始响应记录
  -> canonical triple / literal 规范化
  -> VisibleState 更新
  -> budget、counter 和 replay record 更新
~~~

每次 logical action 必须区分并记录：

- logical action ID、task ID、step ID、action 类型、entity、relation、direction；
- physical request ID、endpoint 或 snapshot 标识、请求参数摘要、请求哈希；
- 原始响应哈希、脱敏后的响应摘要或受控 recorded I/O、响应状态和时间；
- 规范化后的 triple/literal/entity 候选及其来源位置；
- success、empty_result、timeout、malformed_response、endpoint_failure、invalid_action 等状态；
- retry 次数、每次物理请求的结果、KG/action/depth/budget 计数变化；
- 是否允许进入后续 Actor/Critic fixture（SP2-A 默认不进入在线 Actor/Critic）。

不得记录 secret。对于原始响应含有敏感字段或体积过大的情况，保存脱敏响应、内容哈希和受控本地 I/O 记录，并确保能够在不泄露 secret 的前提下 replay。

必须保留 logical KG action 与 physical KG request 的一对多关系。一次 retry 不能被错误计为多个 logical action，也不能被错误忽略为零次物理请求。

## 7. 计划实现的代码功能

本阶段需要实现或补充的功能均位于 `self-play/`，具体文件名可按现有代码结构调整，但必须在运行前登记：

1. **Live KG binding**：在 SP1 Environment binding 上增加只读 live endpoint 调用入口，复用原 PoG 的 KG 请求语义，不复制 secret。
2. **Request builder**：根据冻结 Action 构造可审计的 HEAD/TAIL 请求，固定参数顺序、编码和请求摘要算法。
3. **Response normalizer**：把不同返回形态规范化为 canonical triple、literal、实体候选、空结果或异常类型。
4. **State transition adapter**：将规范化结果转换为 `VisibleState`，记录前后 state ID、可见性和新增 frontier。
5. **Budget/counter ledger**：分别维护 logical action、physical request、retry、KG call、depth 和剩余预算。
6. **Failure classifier**：将 invalid action、empty result、timeout、malformed response、endpoint failure 和 system failure 分类，不吞掉异常。
7. **Recorded I/O writer/replayer**：保存去 secret 的请求/响应记录，支持离线 replay、哈希校验和差异报告。
8. **SP2-A check runner**：提供可重复执行的检查入口、run manifest、配置快照、逐 case 结果和汇总指标。
9. **No-LLM/no-memory guard**：运行时对 LLM、Actor/Critic 和 memory 读写设置 fail-fast guard，并输出调用计数。

实现过程中不能把这些功能扩展为 LLM 决策、Self-Play 轨迹生成或 memory 检索；超出本计划的修改必须先记录计划变更。

## 8. 实验项目与执行步骤

### E2A.1：live KG 连通性、只读性与 schema

- 使用预注册开发 case 发起最小只读请求。
- 验证 endpoint、请求编码、HTTP/协议状态、返回 schema 和响应 hash。
- 验证运行过程中没有写请求、secret 泄漏或访问未登记 endpoint。
- 产物：连通性结果、endpoint/snapshot 标识、脱敏 I/O、请求与响应审计记录。

### E2A.2：HEAD/TAIL 方向与 canonical triple

- 对同一类实体/relation 分别执行 HEAD 和 TAIL 预制 Action。
- 对照 request builder、原始返回和 canonical triple 的方向字段。
- 检查 direction 变换不会把 subject/object、查询实体和候选实体互换。
- 产物：方向测试矩阵、canonical triple 对照、失败样例。

### E2A.3：真实返回到 VisibleState 的状态转移

- 执行单跳和连续两跳的合法 Action 序列。
- 检查每一步前后 state ID、visible triples、frontier、当前实体和 depth。
- 检查重复结果不会产生不稳定的重复 frontier，且顺序规范化可重放。
- 产物：逐步 state transition trace 和 replay 对照。

### E2A.4：空结果、literal、重复结果与特殊响应

- 使用预注册 case 覆盖 non-empty、empty、literal、重复实体、缺字段或空字段。
- 确认 empty result 不是 system failure，literal 不被强制当作实体，重复结果按既定规则去重或保序。
- 对不满足 schema 的响应记录 malformed_response，不静默修复为成功。

### E2A.5：timeout、endpoint failure、retry 与预算

- 在不破坏真实 KG 的前提下，通过受控 timeout、mocked transport 或 recorded fault injection 验证异常路径；不把模拟异常称为 live endpoint 成功。
- 检查 retry 上限、退避配置、logical/physical/retry/KG/depth budget 的增量和边界。
- 验证超出预算后不会继续发起物理 KG 请求。

### E2A.6：recorded I/O 与离线 replay

- 保存至少一组 successful、empty 和 failure/timeout 类别的脱敏记录。
- 关闭网络后使用 recorded I/O replay，比较 response normalization、state transition、counter ledger 和最终 case 状态。
- 检查 replay 不依赖隐藏答案或在线环境，并输出差异报告。

### E2A.7：与原 PoG entity_search 语义对照

- 在不改变原 PoG 基线文件的前提下，对可比的开发 case 对照原 PoG `entity_search` 的请求语义和 adapter 规范化结果。
- 只比较接口语义、方向和结果分类，不进行 KGQA accuracy 或 memory 效果结论。
- 若原 PoG endpoint 语义无法直接复用，记录差异、原因和后续 SP2-B 风险。

## 9. 验收指标与通过门槛

| 指标 | 通过门槛 | 说明 |
|---|---:|---|
| 真实 LLM 调用数 | 0 | 由 guard 和 run ledger 双重确认 |
| memory 读/写次数 | 0 | 不创建 candidate 或正式 memory |
| Oracle/test 标签进入 Action view 次数 | 0 | Verifier 之外不得可见 |
| HEAD/TAIL 映射正确率 | 100% | E2A.2 全部 case |
| 原始响应到 canonical 结果可追溯率 | 100% | 每个结果有响应哈希和来源位置 |
| 合法状态转移正确率 | 100% | E2A.3 全部 case |
| logical/physical/retry 计数正确率 | 100% | E2A.5 全部 case |
| 预算边界处理正确率 | 100% | 超预算不发起请求 |
| 预期异常分类正确率 | 100% | 未分类异常数为 0 |
| recorded I/O replay 一致率 | 100% | 状态、结果和计数均一致 |
| secret 写入日志/产物次数 | 0 | 通过扫描确认 |
| 非预注册写入 data/cope_alias 次数 | 0 | 通过输入目录快照确认 |
| 评测集用于轨迹生成次数 | 0 | exclusion registry 和 run manifest 双重确认 |
| 原 PoG 基线非预注册变化 | 0 | 以 SP1 基线清单为准 |

SP2-A 不能以某个答案准确率或少数成功请求作为通过依据。若 live endpoint 不可用，结论只能是 `INVALID`、`system_failure` 或“环境未就绪”，并说明阻断证据；不得将 fixture replay 结果替代 live KG 证据。

## 10. 运行顺序与产物

建议运行顺序：

1. 读取 overall、SP0/SP1 计划和报告，验证版本、哈希、阶段状态。
2. 生成 SP2-A 配置快照和 run manifest，登记开发 case、endpoint/snapshot 和工作树状态。
3. 先执行 action validation，再执行最小 live KG 连通性检查。
4. 依次执行 E2A.1-E2A.5；每项失败立即保留证据，不自动跳过。
5. 在网络隔离条件下执行 E2A.6 replay；最后执行 E2A.7 语义对照。
6. 汇总指标、异常、未解决风险和是否满足通过门槛。
7. 将实验日志追加到本文件末尾；阶段结束后生成 `reports/sp2a/SP2A_experiment_report.md`，并把报告路径和 hash 登记到 overall。

预期新增产物（仅为规划，不代表本文件生成时已经创建）：

~~~text
self-play/
├── configs/sp2a_live_kg_v1.json
├── scripts/run_sp2a_checks.py
├── src/sp_memory/                # live binding、normalizer、ledger、replay 等实现
├── tests/test_sp2a_live_kg.py
├── tests/fixtures/sp2a/
├── artifacts/recorded_io/sp2a/
├── artifacts/protocol/sp2a_check_result.json
├── artifacts/registries/sp2a_development_task_registry_v1.json
├── runs/<sp2a-run-id>/
└── reports/sp2a/SP2A_experiment_report.md
~~~

## 11. 失败处理与停止规则

- 非法 Action：在 KG 调用前拒绝，记录 `invalid_action`，不得重试为合法动作。
- 空结果：作为合法环境结果记录，不自动改写成异常或失败答案。
- timeout：按配置重试；超过上限后记录 `timeout`/`system_failure`，并保留每次物理请求。
- malformed response：停止当前 case，记录原始响应哈希、解析错误和状态，不猜测字段含义。
- endpoint failure 或认证问题：停止 live run，保护现场并标记 INVALID；不使用未登记的备用 endpoint。
- 计数、方向或 replay 任一关键门槛失败：SP2-A 不得 PASS，先完成失败分类和计划变更记录。
- 任何 secret、Oracle/test label 或评测集轨迹泄漏：立即停止并将运行标记 INVALID，清点受影响产物，不删除原始审计记录。

## 12. 阶段报告要求

SP2-A 完成后必须生成主实验报告：

~~~text
self-play/reports/sp2a/SP2A_experiment_report.md
~~~

报告至少应包含：目标与边界、计划和协议版本、代码/配置/输入/endpoint 标识及哈希、有效和无效 Run ID、E2A.1-E2A.7 结果、live KG 与 replay 证据、异常分类、计数与预算核对、未解决风险、是否允许进入 SP2-B，以及报告自身 SHA-256。报告不得把 SP2-A 的环境验证结果表述为 LLM 或 memory 增强效果。

## 13. 实验日志（运行后追加）

本节在实验执行期间只追加，不覆盖已有记录。每次运行至少记录：

- 日期、Run ID、计划版本、配置版本和 Git commit/dirty status；
- 开发 task registry、endpoint/snapshot 标识和输入文件哈希；
- E2A 项目逐项结果、请求/响应审计产物和 replay 差异；
- LLM/memory/Oracle/test-label 调用计数；
- logical action、physical request、retry、KG/depth/budget 计数；
- 失败分类、停止原因、有效性和修复/计划变更；
- 汇总指标、验收结论和报告路径。

### 13.1 运行日志占位

### LOG-SP2A-001 — 2026-08-22 — 有效 run PASS

- 日期：2026-08-22
- Run ID：`sp2a-20260822T082704Z-28a5bc97`（有效）
- 计划版本：SP2A-PLAN 1.0
- 配置：`configs/sp2a_live_kg_v1.json` SHA-256 `46cb85863055ba94698dcde966168ecd5e77f465e6a06bd07b2e78de80d60023`
- Git：commit `09fd3a5657889e1f986b7e22021b92a429695cce`，dirty
- 开发 task registry：`artifacts/registries/sp2a_development_task_registry_v1.json` SHA-256 `e85513a0c9ac1a1cbbb9586e033aaae1d121bcc21c85e2dd7e62a403b7906301`
- endpoint/snapshot：`http://localhost:8890/sparql`（只读 POST）
- 输入：冻结 20/150/50 只校验哈希，未生成评测轨迹；exclusion 220
- E2A.1–E2A.7：全部 PASS。live Obama `place_of_birth` → `m.02hrh0_`；登记 Honolulu `m.02hrh` 的 TAIL/containedby 为空但方向映射正确
- recorded I/O：`artifacts/recorded_io/sp2a/sp2a_recorded_io_v1.json` bundle_hash `5137e8d97d14d7a91742827a7d4a2ddaea80cc235c5c3fb67006f3d59d1498b5`，n=23；replay 一致率 100%，replay 未用网络
- LLM/memory/Oracle/test-label：0 / 0 / 0 / 0
- 计数：retry 用例 logical=1 physical=2 retry=1；timeout 耗尽 physical=3；非法动作 physical=0；超预算第二次物理请求=0
- 失败分类：无未分类异常；无 INVALID run
- 有效性：PASS。单元测试 58 通过
- 报告：`reports/sp2a/SP2A_experiment_report.md`

### 13.2 计划变更记录

| 日期 | 版本 | 修改前 | 修改后 | 原因 | 对可比性的影响 |
|---|---|---|---|---|---|
| 2026-08-22 | 1.0 | 无 SP2-A 计划 | 建立真实 KG 环境验证计划；限定预制合法动作、禁止 LLM/memory/正式评测，增加 live I/O 审计、异常、预算和 replay 验收 | SP1 已 PASS，下一步需要首次 live KG 的独立环境证据 | 尚未实施，不影响 SP0/SP1；后续 SP2-B 只能以本计划验收结果为前置 |
