# SP2-B 实验报告：无 Self-Play Experience Memory 的 LLM+KG 端到端基线

> 报告目录：`self-play/reports/sp2b/`  
> 计划版本：SP2B-PLAN 1.1  
> 协议版本：`sp-protocol-v1`  
> 总体要求：SP-GENERAL 1.15（收口登记后升级为 1.16）  
> 验收结论：**PASS**  
> 报告日期：2026-08-22  
> 报告 SHA-256：见同目录 `metrics.json` 的 `report_sha256`

本报告只覆盖独立 `self-play/` 实验的 SP2-B：**无 Self-Play Experience Memory 的 LLM+live KG 端到端基线**。本轮首次正式调用 LLM 与 live KG 联合 rollout。结论是推理链能否合法运行、终止、保存和重放，**不是** KGQA EM/F1，也**不是** Self-Play memory 增益。不得把本报告写成 V2-5 Self-Play。

## 1. 研究目标

在 SP2-A 已验证的 live KG 上，确认原 PoG 能由 LLM 驱动完成可执行、可终止、可保存、可重放的端到端推理，同时：

- 不读取或写入 Self-Play Experience Memory（candidate / promoted / formal memory）；
- 允许当前题目、当前 Run 的 `pog_working_memory`，且必须题内隔离；
- Actor/LLM 只看到 O0 与题内工作状态；Oracle/Verifier 不得回流；
- 非法动作不得进入 KG；`BACKTRACK(state)` 保持 unsupported。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/04_SP2B_llm_kg_baseline_rollout.md` SP2B-PLAN 1.1 |
| 协议 | `sp-protocol-v1` |
| 运行时 overall | SP-GENERAL 1.15 |
| 配置 | `configs/sp2b_llm_kg_baseline_v1.json` |
| 配置 SHA-256 | `93723adadff56ab5c5fa340ed93b5df8e9175aca492c272b01284729e887124d` |
| prompt 版本 | `sp2b_actor_v1`（源 `prompt_list.py`） |
| prompt inventory SHA-256 | `9c55ecf7758da4805bf5e0d197ece9d21353d56281891c5a7eb8d77a16f6ffec` |
| B0 registry SHA-256 | `7f4fc1634a62ce99730ac7757d328df1b5d4317c1c5f0c8ee2889fe6b2ea3bb7` |
| B1 registry SHA-256 | `2ab618ceb7accee1ef217c52a4e9d0830260e3184585321baa872631e0464770` |
| B2 数据 | `artifacts/datasets/webqsp_smoke_20.jsonl` SHA-256 `e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393` |
| 前置 SP2-A run | `sp2a-20260822T082704Z-28a5bc97` |
| 前置 SP2-A 补充 run | `sp2a-supp-20260822T111116Z-79aa8ea8` |
| 前置报告 SHA-256 | `0d448a55c8c37dc77ab4d4da26f6d6e63092491136c7e95d25553237febf4bfc` |
| endpoint | `http://localhost:8890/sparql`（只读 POST） |
| LLM | `gpt-3.5-turbo-0125`，temperature 0.3/0.3，max_tokens 4096，base `https://cn2us02.opapi.win/v1` |
| 密钥 | 仅进程环境 `OPENAI_API_KEY`；未写入配置快照、日志或本报告 |
| 预算 | depth 4 / steps 24 / kg 80 / llm 40 / critic 0 / frontier 80 |
| `allow_llm` / `allow_live_kg` / `allow_self_play_experience_memory` | true / true / false |
| `backtrack_state_policy` | unsupported；fallback `original_pog_if_finish_list` → `SELECT_FRONTIER` |
| `entity_cap_fallback` | `unicode_sort_truncate` @ 70 |

## 3. 有效运行

| 项 | 值 |
|---|---|
| 有效 Run | `sp2b-20260822T131350Z-b70a898b`（manifest status=SUCCESS，退出码 0） |
| 无效 Run | 无 |
| Git commit | `577a8f946d25ef46adec96a0d9488b6a1df36ffc` |
| 工作树 | dirty（本轮新增 SP2-B 适配代码与产物，未提交） |
| 单元测试 | 75 通过 / 0 失败 / 0 skip |
| 开始 / 结束 | 2026-08-22T13:13:50Z / 2026-08-22T13:27:37Z |

失败 run 未出现，因此没有覆盖问题。产物在 `runs/sp2b-20260822T131350Z-b70a898b/`，共享结果为 `artifacts/protocol/sp2b_check_result.json`。

## 4. 实验设置

- 包装原 PoG，不改写 `main_freebase.py` 等基线文件。
- 关系枚举与名称查找走 live SPARQL，计入 KG 调用；LLM 选择的关系必须先映射为已枚举的 `EXPAND`，非法输出不发 KG。
- 冻结 ActionType 无 `SELECT_RELATION`。
- 题内 `mem` 重定向为 `runs/<run-id>/scratch/<task-id>/pog_working_memory`。
- Replay：KG 使用 recorded I/O（`network_enabled=False`）；LLM 使用响应缓存并标记 replay，不计真实调用。
- 执行顺序：B2B.0 preflight → B0 → B1 门槛 → B2；未根据 B2 调参。

## 5. B2B.0–B2B.3 结论

| 步骤 | 结论 | 要点 |
|---|---|---|
| B2B.0 启动检查 | PASS | preflight 通过；原 PoG 基线哈希未变；B0/B1 与冻结评测问题无重叠；`data/`、`cope_alias/` 只读快照在运行前后一致 |
| B2B.1 B0 人工核查 | PASS | 4/4 可终止，replay 4/4，pipeline_ok 4/4，未分类 0，Oracle 泄漏 0。覆盖一跳、两跳、literal、空结果边界 |
| B2B.2 B1 开发任务 | PASS | 20/20 轨迹完整，replay 20/20，未分类 0。失败均已分类，未靠加预算或改题掩盖 |
| B2B.3 B2 smoke 20 | PASS | 在 B0/B1 门槛后读取冻结 smoke 20；20/20 终止且可重放；不以准确率作为 SP2-B 门槛 |
| B2B.4 收口审计 | PASS | Self-Play Experience Memory 读写 0；工作记忆按 task_id 隔离；secret_hits 0；baseline 未登记变化 0 |

## 6. 分层结果（分别报告，不合并为 EM/F1）

### 6.1 B0

| task_id | 终止 | 失败分类 | 提交 | Verifier |
|---|---|---|---|---|
| `sp2b.b0.onehop.obama_birthplace` | STOP_SUBMITTED | none | `m.02hrh0_` | True（exact id/name） |
| `sp2b.b0.twohop.obama_birth_containedby` | STOP_SUBMITTED | none | `m.02hrh0_` | True（observed_optional） |
| `sp2b.b0.literal.obama_birthdate` | STOP_SUBMITTED | none | `1961-08-04` | True |
| `sp2b.b0.empty.obama_death` | FAILURE | answer_extraction_failure | （空） | True（empty_or_abstain） |

真实 LLM 调用合计 21。两跳题在 hop-1 出生地后停止，未提交包含地；这是 Explorer 边界，不是协议失败。空死亡日期未产出 ABSTAIN 动作，但提交为空且 Verifier 规则接受空结果。

### 6.2 B1

失败分类：`none` 16，`answer_extraction_failure` 3，`budget_insufficient` 1。真实 LLM 合计 141。Verifier 17 True / 3 False（False 均对应空提交）。

系统边界（不构成 SP2-B FAIL）：

- `einstein.twohop.birth_country`、`microsoft.founders`、`everest.containedby`：答案抽取失败，轨迹完整。
- `python.empty_death`：对“编程语言死亡日期”继续搜索至 LLM 预算耗尽（33 次真实调用），分类为 `budget_insufficient`。
- `france.twohop.capital_containedby`：提交首都 `m.05qtj` 而非上一跳的 containedby 实体；协议上已终止。

### 6.3 B2 WebQSP smoke 20

失败分类：`none` 11，`answer_extraction_failure` 6，`budget_insufficient` 3。真实 LLM 合计 153。本层 Verifier 使用 `observed_optional`，只检查提交值是否出现在可见状态，**不是** WebQSP gold EM。11/20 有非空提交且均被记为 observed。不得把 11/20 写成正式准确率。

## 7. 验收指标

| 指标 | 门槛 | 实际 | 是否通过 |
|---|---:|---:|---|
| 真实 LLM 调用有记录 | 与 manifest/响应记录一致 | B0 21 + B1 141 + B2 153；每步有 prompt/response hash | 是 |
| Self-Play Experience Memory 读/写 | 0 | 0 | 是 |
| `pog_working_memory` 跨题/跨 Run 复用 | 0 | 0（每题独立 scratch 路径，含 task_id） | 是 |
| 工作记忆生命周期审计 | 100% | 每题 create/read/write/close 均记录 | 是 |
| Oracle/test-label 进入 Actor/LLM view | 0 | 0 | 是 |
| 非法动作进入 KG | 0 | 非法输出在 validator 前拒绝 | 是 |
| B0 端到端可终止率 | 100% | 100% | 是 |
| B1 轨迹完整率 | 100% | 100% | 是 |
| B1 可重放率 | ≥95% | 100%；关键状态/动作/计数差异 0 | 是 |
| 预算越界物理 KG 请求 | 0 | 0 | 是 |
| 未分类异常 | 0 | 0 | 是 |
| baseline 未登记变化 | 0 | 0 | 是 |
| `data/`、`cope_alias/` 写入 | 0 | 只读快照不变 | 是 |
| B2 smoke 运行 | B0/B1 稳定后一次冻结集 | 已运行，未调参 | 是 |
| secret 扫描 | 0 | 0 | 是 |

## 8. 失败分类与未解决边界

允许存在且已分类的失败：

- `answer_extraction_failure`：LLM 在 Sufficient=Yes 时给出未观察答案，或半停止路径未能构造 STOP/ABSTAIN。
- `budget_insufficient`：深度循环中 LLM 预算耗尽。

未出现：`invalid_task`、`action_space_failure`（作为最终分类）、`critic_recovery_failure`、`system_failure`、未分类异常。

未解决风险（不阻塞 SP2-B PASS，进入 SP3 前必须登记）：

1. 多跳题可能在第一跳后过早 STOP。
2. 空结果边界更常落成抽取失败，而不是干净的 ABSTAIN。
3. 无信息关系（如语言的 date_of_death）会烧预算。
4. SentenceTransformer 仍缺失，实体截断使用预注册 unicode 排序。
5. `BACKTRACK(state)` 仍 unsupported。

## 9. 产物索引

```text
self-play/configs/sp2b_llm_kg_baseline_v1.json
self-play/prompts/sp2b_actor_v1.json
self-play/artifacts/registries/sp2b_b0_manual_tasks_v1.json
self-play/artifacts/registries/sp2b_b1_development_tasks_v1.json
self-play/artifacts/registries/sp2b_exposure_registry_v1.json
self-play/artifacts/protocol/sp2b_check_result.json
self-play/runs/sp2b-20260822T131350Z-b70a898b/
self-play/reports/sp2b/SP2B_experiment_report.md
self-play/reports/sp2b/metrics.json
```

B2 只读取已冻结的 `artifacts/datasets/webqsp_smoke_20.jsonl`。`data/` 与 `cope_alias/` 无写入。

## 10. 结论

SP2-B **PASS**。无 Self-Play Experience Memory 的 LLM+live KG 端到端基线已建立：B0/B1/B2 均可终止、轨迹完整、可离线重放，隔离与基线完整性满足计划门槛。本阶段不证明 memory 有效，不产生候选经验，不启动 SP3，也不构成 V2-5。
