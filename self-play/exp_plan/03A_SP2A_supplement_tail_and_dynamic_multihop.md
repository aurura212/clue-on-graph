# SP2-A 补充实验：TAIL 正向语义与动态多跳验证

> 文档编号：SP2A-SUPPLEMENT-PLAN  
> 版本：1.0  
> 初始制定日期：2026-08-22  
> 状态：已完成 PASS（2026-08-22）  
> 所属阶段：SP2-A 补充验证，不构成新的总体阶段  
> 上位约束：`00_experiment_overall_requirements.md` v1.12（收口后 overall 升级为 1.13）  
> 主计划：`03_SP2A_live_kg_environment_validation.md` SP2A-PLAN 1.0  
> 前置报告：`reports/sp2a/SP2A_experiment_report.md`  
> 前置结论：SP2-A 主实验已完成基础 PASS，但 TAIL 正向语义和真实返回驱动的动态两跳证据不足

## 1. 补充实验定位

本计划用于补足 SP2-A 主实验中两个证据缺口，不重新定义 SP2-A 的研究目标，也不进入 SP2-B。补充实验仍然只执行预制合法 Action，通过真实 KG 和 SP1 adapter/environment binding 验证查询方向与动态状态转移。

本补充实验的核心目的不是重新测试“端口是否连通”，而是确认：

1. 一个已知存在的 KG 边能否被从反向方向查询出来；
2. 第一步 live KG 返回的真实实体，能否自动成为第二步查询的输入；
3. 在上述过程中，canonical triple、VisibleState、frontier、depth 和计数是否保持正确。

补充实验完成后，才能对 SP2-A 的“HEAD/TAIL 方向正确”和“真实动态多跳状态转移正确”作出更完整的结论。

## 2. 与主 SP2-A 实验的关系

本计划只补充主计划中的 E2A.2 和 E2A.3，不替代主计划，也不修改主实验已经保存的失败、风险或原始运行证据。

| 项目 | 规定 |
|---|---|
| 主计划 | `03_SP2A_live_kg_environment_validation.md` |
| 补充对象 | E2A.2 TAIL 语义；E2A.3 动态两跳 |
| LLM | 不调用 |
| Explorer/Actor/Critic | 不启动 |
| memory | 不生成、不读取、不注入 |
| Oracle | 不进入 Action 或 Environment view |
| KG | 允许调用已登记的只读 live KG |
| Action | 预先冻结的合法 Action；第二跳实体必须来自第一跳 live 返回 |
| 评测集 | 不使用 WebQSP 20、WebQSP 150、CWQ 50 生成轨迹 |
| Self-Play 经验 | 不生成、不写入 candidate experience |
| 下一阶段 | 结果作为 SP2-B 启动前的环境前置证据 |

本补充实验不是 SP2-B，不允许借此提前运行 LLM+KG rollout。

## 3. 待解决的问题

### 3.1 TAIL 正向查询问题

主实验使用的 TAIL case 为：

~~~text
entity = m.02hrh
relation = people.person.place_of_birth
direction = TAIL
result = empty
~~~

同时，主实验 HEAD 查询实际得到：

~~~text
m.02mjmr --people.person.place_of_birth--> m.02hrh0_
~~~

由于 `m.02hrh` 与 `m.02hrh0_` 不一致，原 TAIL empty result 不能充分证明一个真实存在的反向关系能够被正确查出。

补充实验必须使用一条已通过 live KG HEAD 查询确认存在的边，或使用其他预先登记且已确认存在的边，构造对应的正向 TAIL case。不能把空结果重新解释为正向 TAIL 证据。

### 3.2 动态两跳问题

主实验的两跳 case 将第二跳实体预先写成 `m.02hrh`，而第一跳真实返回的是 `m.02hrh0_`。因此主实验验证了连续执行两个合法 Action，但没有充分验证“第一跳 live 返回 -> 自动写入状态 -> 从状态取出实体 -> 执行第二跳”的动态链路。

补充实验第二跳的 entity 字段必须由第一跳实际规范化结果产生，不得从任务 registry 中提前写死为另一个 MID。

## 4. 具体目标

1. 构造至少一个非空、可审计、可重放的 TAIL 查询 case。
2. 证明 TAIL 请求模板、变量绑定和 canonical triple 方向均正确。
3. 构造至少一个由第一跳 live 返回结果驱动的动态两跳 case。
4. 证明第二跳读取的是第一跳真实返回的实体，而不是任务 registry 的预置实体。
5. 检查动态两跳过程中 state_id、visible triples、frontier、current entity、depth 和 budget 的变化。
6. 检查重复执行同一个补充 case 时，状态和规范化结果稳定一致。
7. 更新 SP2-A 的证据结论，使 TAIL 和动态两跳的结论与实际证据强度一致。
8. 补齐 SP2-A 报告和 metrics 的 SHA-256 登记，使阶段收口满足 overall v1.11 的文档要求。

## 5. 前置条件与冻结输入

实施前必须记录以下内容：

1. overall 当前版本为 v1.11，SP2-A 已收口，当前仍未启动 SP2-B。
2. 主 SP2-A 有效 Run 为：`sp2a-20260822T082704Z-28a5bc97`。
3. 主计划版本为 SP2A-PLAN 1.0，补充计划版本为 SP2A-SUPPLEMENT-PLAN 1.0。
4. 使用与主实验相同的 SP2-A 配置和协议，除新增开发任务外不得改变方向语义、canonicalization、预算或 replay 规则。
5. 记录 live endpoint、endpoint/snapshot 标识、工作树 commit/dirty 状态、配置 hash 和补充 task registry hash。
6. 固定评测文件只校验 hash，不读取其答案、路径或标签构造补充 Action。
7. 新增开发 task 的实体和 relation 必须登记在补充 registry 中，并通过 exposure registry 标记可能与 WebQSP/CWQ 重叠的实体。
8. 不得把新 case 写入 data/、cope_alias/ 或原 PoG 基线目录。

## 6. 补充任务注册要求

新增任务统一登记到：

~~~text
self-play/artifacts/registries/sp2a_supplement_task_registry_v1.json
~~~

每个任务至少包含：

- 稳定的 task_id；
- entity、relation、direction；
- 查询用途标签（TAIL_positive 或 dynamic_twohop）；
- 任务来源和是否独立于评测集；
- 预期响应类别；
- 对 TAIL positive case，已知存在的 subject/object 关系说明；
- 对 dynamic twohop case，第一跳允许返回的候选实体约束；
- 不包含 Oracle answer、gold path 或评测标签作为 Actor 输入。

推荐使用如下两类任务：

### 6.1 TAIL-positive case

应先通过一条已知 HEAD 边确认：

~~~text
subject S --relation R--> object O
~~~

随后以 O 为查询实体执行：

~~~text
TAIL(O, R)
~~~

预期至少返回 S，或者返回包含 S 的合法候选集合。需要记录：

- 原始 SPARQL；
- 查询方向；
- 返回 binding；
- canonical triple；
- subject/object 是否恢复正确；
- 返回结果是否进入 VisibleState。

如果实际 KG 中没有稳定可复现的反向边，不得人为篡改响应；应更换为另一个预注册 case，并在日志中说明原 case 的作废原因。

### 6.2 Dynamic-twohop case

任务 registry 只冻结第一跳：

~~~text
EXPAND(entity_0, relation_1, direction_1)
~~~

第二跳 Action 不得在 registry 中写死最终 entity。运行时必须：

~~~text
第一跳 live response
-> canonical entity extraction
-> 更新 VisibleState/frontier
-> 从 frontier 选择预先登记的 relation_2 和 direction_2
-> 使用第一跳返回的真实 entity 执行第二跳
~~~

如果第一跳返回多个实体，必须预先规定选择规则，例如按 canonical entity ID 排序取第一个，或对所有候选逐一执行；不得根据答案或运行结果临时选择。

## 7. 需要实现或补充的代码功能

只允许在 `self-play/` 内实现或修改：

1. **TAIL positive task loader**：读取补充 registry 并校验正向 TAIL case 的字段完整性。
2. **Dynamic action materializer**：根据第一跳 canonical result 动态生成第二跳 Action，并保留来源指针。
3. **Dynamic transition audit**：记录第二跳 entity 来源于哪个第一跳 binding、frontier 或 state_id。
4. **Direction assertion**：对 TAIL positive case 检查返回 subject 是否符合预注册边关系，不使用 Oracle answer 注入运行上下文。
5. **Supplement check runner**：输出独立 run manifest、检查结果和指标，不覆盖主 SP2-A run。
6. **No-LLM/no-memory guard**：沿用主计划 guard，调用数必须为 0。
7. **Report hash finalizer**：在报告内容冻结后，计算并写入 `reports/sp2a/metrics.json` 和 overall；不能把 `PENDING` 留作收口状态。

若现有 adapter 已经支持动态状态转移，不得为了“产生新代码”重复实现；应通过补充测试和审计验证现有功能，并记录实际复用的模块。

## 8. 实验项目与执行步骤

### S2A-S.1：补充前置检查

- 校验主计划、主报告、配置和协议版本。
- 校验主实验固定评测集 hash、exclusion count、原 PoG baseline hash。
- 校验补充 registry 不含评测题答案、gold path 或 Oracle witness。

### S2A-S.2：TAIL 正向语义验证

- 执行一个或多个预注册 TAIL-positive case。
- 确认请求使用反向查询模板：`?x relation entity`。
- 确认至少一个预期 subject 被返回。
- 确认 canonical triple 的 subject/object 和 direction 正确。
- 确认结果进入 VisibleState，且 replay 后保持一致。

### S2A-S.3：动态两跳状态转移验证

- 执行第一跳预制 Action。
- 从第一跳 live response 中提取 canonical entity。
- 按固定规则生成第二跳 Action。
- 验证第二跳请求中的 entity 与第一跳真实返回 entity 完全一致。
- 验证 state_id、frontier、depth、KG call 和 logical/physical/retry 计数变化。
- 至少重复执行一次，验证结果一致。

### S2A-S.4：异常与边界回归

- 对动态 Action 进行非法 entity、空第一跳、多个第一跳结果、重复实体和超预算检查。
- 验证没有第一跳实体时不会发起第二跳物理请求。
- 验证第二跳失败时保留第一跳证据，不篡改为成功。
- 继续使用 scripted transport 验证 timeout/malformed，但必须与真实 live 结果区分。

### S2A-S.5：收口与报告修正

- 将补充结果追加到主 SP2-A 计划的实验日志，或在本计划日志中记录独立证据路径；不得覆盖主日志。
- 更新 SP2-A 报告中的 E2A.2、E2A.3 和未解决风险表述。
- 将 `reports/sp2a/metrics.json` 的 `report_sha256` 从 `PENDING` 更新为实际报告 hash。
- 在 overall 阶段历史或 SP2-A 收口记录中登记报告 hash 和补充 run ID。
- 如果补充验证仍失败，不得将 SP2-A 完整结论写成无条件 PASS；应保留 CONDITIONAL PASS 或 system_failure，并阻止 SP2-B 正式 rollout。

## 9. 验收指标与通过门槛

| 指标 | 通过门槛 |
|---|---:|
| 补充 run 有效性 | 有效，且不存在未分类系统异常 |
| 真实 LLM 调用数 | 0 |
| memory 读写次数 | 0 |
| Oracle/test label 进入 Action view | 0 |
| TAIL positive case 数 | 至少 1 个 |
| TAIL 非空返回率 | 100%（对已预注册 positive case） |
| TAIL 方向和 canonical subject/object 正确率 | 100% |
| 第一跳返回实体作为第二跳输入的比例 | 100% |
| 动态两跳状态转移正确率 | 100% |
| 动态两跳 replay 一致率 | 100% |
| 无第一跳结果时的第二跳物理请求数 | 0 |
| 计数与预算正确率 | 100% |
| 未分类异常数 | 0 |
| 固定评测集轨迹使用次数 | 0 |
| data/cope_alias 非预注册写入次数 | 0 |
| 原 PoG 基线非预注册变化 | 0 |
| 报告 SHA-256 登记完整率 | 100%，不允许 `PENDING` |

补充实验不以 KGQA accuracy、EM/F1 或 memory 增益作为验收指标。补充实验 PASS 只能说明 SP2-A 的环境证据完整度提高，不能说明 LLM 推理或 memory 有效。

## 10. 结果解释与推进规则

### 10.1 允许进入 SP2-B 的条件

只有同时满足以下条件，才允许启动 SP2-B 的实现或运行：

1. 补充实验满足第 9 节所有门槛；
2. TAIL positive 和 dynamic twohop 的证据均能通过 recorded I/O 或在线审计重放；
3. 主报告、补充日志、metrics 和 overall 的 hash/Run ID 登记完成；
4. 生成并登记 SP2-B 计划文件；
5. SP2-B 继续保持 memory 关闭，并先使用独立开发任务；
6. SP2-B 不使用 WebQSP 150/CWQ 50 进行正式效果对比，直到在线基线稳定。

### 10.2 不允许进入 SP2-B 的情况

- TAIL positive case 仍无法得到非空返回；
- 第二跳 entity 不是第一跳 live 返回结果；
- replay 与在线状态、结果或计数不一致；
- 发现 Oracle/test label 泄漏；
- report_sha256 仍为 `PENDING`；
- 发生未分类异常或 data/cope_alias 写入；
- endpoint 变化但没有更新 endpoint/snapshot 记录。

## 11. 预期产物

~~~text
self-play/
├── artifacts/registries/sp2a_supplement_task_registry_v1.json
├── artifacts/registries/sp2a_supplement_exposure_registry_v1.json
├── configs/sp2a_supplement_v1.json
├── scripts/run_sp2a_supplement_checks.py
├── tests/test_sp2a_supplement.py
├── artifacts/protocol/sp2a_supplement_check_result.json
├── runs/<sp2a-supplement-run-id>/
├── reports/sp2a/metrics.json              # 补充后不得为 PENDING
└── reports/sp2a/SP2A_experiment_report.md # 更新既有报告，保留原始结论和补充记录
~~~

以上是计划产物，不代表本文件生成时已经创建或实验已经运行。

## 12. 实验日志（运行后追加）

本节只追加，不覆盖主 SP2-A 日志或原始 Run 证据。每次补充运行至少记录：

- 日期、补充 Run ID、计划版本、主计划版本、配置和 Git commit/dirty status；
- 补充 task registry、endpoint/snapshot 和所有输入 hash；
- TAIL positive 的原始请求、响应、canonical triple 和方向断言；
- dynamic twohop 每一步的 source state、source binding、第二跳 entity 和请求 hash；
- LLM/memory/Oracle/test-label 调用计数；
- replay 对照、异常分类、预算和计数；
- 是否更新主报告、metrics 和 overall，以及更新后的 hash；
- 最终结论：PASS、CONDITIONAL PASS、FAIL 或 INVALID。

### 12.1 运行日志占位

### LOG-SP2A-S-001 — 2026-08-22 — 有效补充 run PASS

- 日期：2026-08-22
- 补充 Run ID：`sp2a-supp-20260822T111116Z-79aa8ea8`（有效，manifest status=SUCCESS）
- 主计划版本：SP2A-PLAN 1.0；补充计划版本：SP2A-SUPPLEMENT-PLAN 1.0
- 配置：`configs/sp2a_supplement_v1.json` SHA-256 `288c5799a39438b4074ad27ce188f5b3bc48ccafb8a10d9d38f5f302c3fa9c02`
- Git：commit `7da26850e4c5a519da6147e19398d86098359010`，dirty
- 补充 task registry：`artifacts/registries/sp2a_supplement_task_registry_v1.json` SHA-256 `ec6cdfb9c07a5e516f3e145ff0e2f8965deab7d077b4c1a03e784d102c26efca`
- endpoint/snapshot：`http://localhost:8890/sparql`（只读 POST）
- 输入：冻结 20/150/50 只校验哈希；exclusion 220；未生成评测轨迹
- S2A-S.1–S.4 与 replay：全部 PASS。TAIL(`m.02hrh0_`, `people.person.place_of_birth`) 非空且包含 `m.02mjmr`；hop2 entity 100% 来自 hop1 live 返回
- recorded I/O：`artifacts/recorded_io/sp2a/sp2a_supplement_recorded_io_v1.json` bundle_hash `db0ea71b8f329e0fa42c13651a4e2e6fb54bc915437ead3cde5074a08a244534`；replay 一致率 100%，replay 未用网络
- LLM/memory/Oracle/test-label：0 / 0 / 0 / 0
- 空 hop1 时 hop2 物理请求：0；预算边界正确率 100%
- 失败分类：无未分类异常；无 INVALID run
- 有效性：PASS。补充单元测试 9 通过
- 主报告：`reports/sp2a/SP2A_experiment_report.md`（追加第 13 节，不覆盖主实验原始记录）
- metrics：`reports/sp2a/metrics.json`；`report_sha256` `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`
- 结论：PASS。SP2-A TAIL 正向语义与动态两跳证据完整。不启动 SP2-B

### LOG-SP2A-S-002 — 2026-08-22 — 报告哈希核对（换行符，非内容漂移）

- 登记值：`0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc`
- 仓库字节（LF，`sha256sum` / Python `hashlib` on raw file）：与登记值一致；HEAD blob 一致
- 另一计算值 `beb43d7a9f8f4d2b20048bad8483486e11ec68b61daebeeab6797c241eec7ecd`：同一正文将 `\n` 换成 `\r\n` 后的 SHA-256，不是另一份报告
- 处理：不改 `metrics.json`、不改报告正文、不把 CRLF 哈希登记为收口值
- 结论：收口哈希有效。核对应对仓库内 LF 文件字节，不能对 CRLF 转写再哈希

### 12.2 计划变更记录

| 日期 | 版本 | 修改前 | 修改后 | 原因 | 对可比性的影响 |
|---|---|---|---|---|---|
| 2026-08-22 | 1.0 | 主 SP2-A 已完成基础环境验证，但 TAIL positive 和动态两跳证据不足，报告 hash 尚未登记 | 增加独立补充实验，验证正向 TAIL、真实返回驱动的动态两跳，并补齐阶段收口 hash | 主实验发现 `m.02hrh` 与 live 返回 `m.02hrh0_` 不一致；现有两跳第二步没有使用第一跳真实返回实体 | 不改变主 SP2-A 原始 Run；补充结果作为 SP2-B 的新增前置证据 |
