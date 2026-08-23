# SP4 实验报告：前置能力、反事实、蒸馏与 promotion

> 报告目录：`self-play/reports/sp4/`  
> 计划版本：SP4-PLAN 2.1  
> 协议版本：`sp-protocol-v1`  
> 总体要求：SP-GENERAL 1.20  
> 验收结论：**CONDITIONAL PASS**  
> 报告日期：2026-08-23  
> 有效 Run：`sp4-20260823T050956Z-69e15a34`

本报告只覆盖独立 `self-play/` 的 SP4：**补齐合成任务与严格 split、多轨迹 Critic、backtrack 协议门禁、candidate injection 审计、同状态反事实、蒸馏和 promotion 判定**。结论不是 WebQSP/CWQ EM/F1，不是 memory 增益，也不是 V2-5 Self-Play。`promoted_memory` 条数为 0，不得进入 SP5。

## 1. 研究目标

回答计划中的问题：固定 KG snapshot 上能否生成答案可判定、问题不泄漏路径和答案的合成任务；Explorer 与 O0 Critic 能否形成可重放多轨迹；候选动作在同状态同预算下是否优于原动作或随机合法动作；蒸馏规则能否去实体去答案，并按冻结门槛 promotion。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/06_SP4_precondition_counterfactual_distillation_promotion_v2.md` SP4-PLAN 2.1 |
| overall | SP-GENERAL 1.20 |
| 协议 | `sp-protocol-v1` |
| 配置 | `configs/sp4_precondition_and_promotion_v2.json`（冻结后含 synthetic manifest hash `b22c08bd…`） |
| Explorer prompt | `prompts/sp4_explorer_v2.txt` SHA-256 `b331e54f489fac34ba7ba53b011f51e6ac0f10dfc7e48b8d207a7ef4b2449aed` |
| O0 Critic prompt | `prompts/sp4_critic_o0_v2.txt` SHA-256 `d46dbe1797bd82c260c0924055c0b13188d7574b95c89c7a553a65d9ef00e956` |
| Distiller prompt | `prompts/sp4_distiller_v2.txt` SHA-256 `85a90ba8c663d530dcee30ff4327de0f9e84ddc69781d51834d3c3b91a86a896` |
| 代码生成 prompt | `prompts/sp4_code_generation_v2.md` SHA-256 `bf1d48b8f9abaf1eeb9535d7a5ed090edd968d5dea89382315cb7213c693296b` |
| 快照 | `sp4-fixture-graph-v1`，source=`protocol_fixture`，snapshot hash `545a7372f723864e53d31f1777488a42348581f7a29a80d94b8a994b21e40f8d` |
| verbalizer | `template_v1_degraded`（模板自然语言，不是 LLM 生成问题） |
| SP3 候选 | 119 条，SHA-256 `dcead529ff32f7a5aa4c3e653dc29cee90c4e3c85f7eeb09826645a50fe6a1dd` |
| SP3 报告 | SHA-256 `c30f54dad9d37f099c3faddac5377087400eb6287ee2c25831fec19d921bc650` |
| seed | 20260823 |
| LLM / live KG | **未调用**（显式关闭；启发式 Critic + snapshot ReplayEnvironment） |
| memory_read / injection | false |
| 评测集 20/150/50 | 未用于生成、调参或 promotion |
| PoG BACKTRACK | 仍 unsupported；snapshot 协议 backtrack 可解析、拒绝不可见目标、恢复已观察状态 |

## 3. 有效运行

| 项 | 值 |
|---|---|
| 有效 Run | `sp4-20260823T050956Z-69e15a34`；05:09:56Z–05:09:57Z；SUCCESS |
| 无效 Run | 无 |
| Git | `492348b5aef5b04ca2d77cb41a1a9da8049e6b79` dirty |
| 单元测试 | 103 通过 / 0 失败 / 0 skip（含 14 条 SP4 离线测试） |
| 密钥 | 未写入配置或报告 |

## 4. 合成任务与严格 split

合成任务在冻结 fixture snapshot 上生成，Actor JSONL 与 Oracle JSONL 物理分离。split 污染交叉数为 0。

| split | n | 角色 |
|---|---|---|
| discovery / SP4-SYN | 12 | 多轨迹与候选提取 |
| SP4-CF 状态 | 12 | 同状态反事实初始状态 |
| validation_v1 / SP4-V1 | 8 | 原始候选 held-out |
| validation_v2 / SP4-V2 | 8 | 蒸馏规则 held-out |
| holdout | 8 | 未参与候选生成 |

生成统计：可执行任务 44 条（含 paraphrase），4 条路径不可执行后丢弃；歧义率 0；泄漏率 0；去重率 0。问题不含 relation ID、答案 ID 或答案名。

`executable_rate` 按 source×kind 计为 26 次尝试、44 条 paraphrase 保留；manifest hash `b22c08bdbbf9dc355308611dfdebd8a645beda3cb23b89c57ad85d54ced8962f`。

## 5. 多轨迹与 Critic

在 discovery 12 题上运行 G0 actor-only、G1 三 seed O0 启发式 Critic、G2 offline teacher、G3 random critic。轨迹完整率与确定性 replay 均为 1.0。system/protocol failure 为 0。上下文压缩后不再出现 SP3 的 16k 超长失败。

| 组 | n | 来源标签 | 成功率 | 恢复率 | 均步数 |
|---|---|---|---|---|---|
| G0 | 12 | explorer_only | 0.333 | 0 | 11.33 |
| G1 | 36 | o0_critic | 0.778 | 0.056 | 7.81 |
| G2 | 12 | oracle_guided_offline_teacher | 0.833 | 0 | 4.75 |
| G3 | 12 | random_critic | 0.833 | 0 | 6.25 |

G2 与 G3 **不是** O0 Self-Play。G1 相对 G0 的成功率差来自 snapshot 启发式 Critic，不能写成 live PoG + gpt-3.5 的恢复能力。配对恢复仍低（约 2/36）。

## 6. SP3 候选审计

对 SP3 的 119 条候选做 schema / privacy / leakage / replay 审计。只写审计产物，不注入 Explorer。

| 项 | 值 |
|---|---|
| n | 119 |
| schema / replay | 1.0 / 1.0 |
| privacy / leakage | 0.9496 |
| 通过 | 113 |
| 拒绝 | 6（实体名或答案值进入 reason） |
| 方法 | o0_critic 24 / teacher 38 / random_critic 57 |
| 阶段 | relation_selection 106 / continue_stop 5 / answer_submission 8 |

通过的 113 条写入 `sp4_validated_candidates_v2.jsonl`，拒绝 6 条写入 `sp4_rejected_candidates_v2.jsonl`。这些 WebQSP discovery 候选不能在 fixture snapshot 上执行同状态反事实，因此 CF/蒸馏主证据来自 SP4-SYN 本地候选。

## 7. 同状态反事实

在 12 个 discovery 初始状态上比较 CF0 原动作、CF1 候选动作、CF2 随机合法动作；另做 sham 对照。

| 指标 | 值 |
|---|---|
| n | 12 |
| win_rate | 0.000 |
| tie_rate | 0.583 |
| invalid_rate | 0.417 |
| harm_rate | 0.000 |
| sham 更优 | 0 |

候选动作多数与 greedy 原动作打平，或因关系在该初始状态不可见而 invalid。没有稳定正收益，也没有 sham 假阳性优于真实候选。

## 8. 蒸馏与 promotion

从 snapshot 本地候选蒸馏出 4 条规则。0 条 `promoted`，0 条 `rejected_harmful`，4 条 `deferred`。`promoted_memory_v2.jsonl` 为空文件（n_promoted=0），manifest 保留完整失败原因。

未过门槛的典型原因：`min_cf_states`、`margin`（win−harm=0）、`v1_triggers`、`v1_success_or_cost`；其中一条仅来自 `random_critic`。

因此没有任何决策阶段具备进入 SP5 单阶段接入的资格。

## 9. 验收

| 项 | 门槛 | 实际 | 是否通过 |
|---|---|---|---|
| 前置门禁、hash、写边界 | 通过 | preflight ok；secret 0 | 是 |
| 合成任务可判定、不泄漏 | 是 | 泄漏率 0；答案可执行 | 是 |
| 严格 split 不交叉 | 0 | 0 | 是 |
| 未使用 20/150/50 | 是 | 是 | 是 |
| Actor/Critic O4 | 0 | 0 | 是 |
| 多轨迹 replay | ≥95% | 100% | 是 |
| G1 上下文协议失败 | 可分类 | 0 | 是 |
| backtrack 协议可判定 | 是 | 可见/已观察可执行，隐藏拒绝 | 是 |
| PoG BACKTRACK | 记录 | 仍 unsupported | 是（门禁，非实现） |
| 反事实 win/tie/harm/invalid | 有记录 | 有 | 是 |
| promotion 或完整失败证据 | 是 | 0 promoted，4 deferred | 是 |
| 完整自然语言任务生成器 | 计划目标 | 模板降级 | **否，故 CONDITIONAL** |
| live LLM/KG Critic | 计划目标 | 未调用 | **否，故 CONDITIONAL** |
| 至少一条规则 promotion | 进入 SP5 条件 | 0 | 否，不启动 SP5 |

## 10. 未解决风险

1. 合成问题是模板 verbalizer，不是自由自然语言；snapshot 是 protocol fixture，不是 live Freebase 抽样图。
2. Critic 为压缩上下文上的启发式 backend，不是 gpt-3.5-turbo-0125。
3. 原 PoG `BACKTRACK(state)` 仍 unsupported；SP5 的 PB 必须保持 unsupported。
4. SP3 的 119 条候选有 6 条隐私失败；其余无法在本 snapshot 上做同状态执行，不能直接 promotion。
5. 反事实无正收益。不得把 G1/G2/G3 成功率差写成 memory 有效。

## 11. 产物索引

```text
self-play/configs/sp4_precondition_and_promotion_v2.json
self-play/prompts/sp4_explorer_v2.txt
self-play/prompts/sp4_critic_o0_v2.txt
self-play/prompts/sp4_distiller_v2.txt
self-play/prompts/sp4_code_generation_v2.md
self-play/artifacts/datasets/sp4_kg_snapshot_v1.json
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
self-play/runs/sp4-20260823T050956Z-69e15a34/
self-play/reports/sp4/SP4_experiment_report.md
self-play/reports/sp4/metrics.json
```

## 12. 结论

SP4 **CONDITIONAL PASS**。前置门禁、固定 snapshot 合成任务、严格 held-out、压缩 Critic 多轨迹、backtrack 协议门禁、SP3 候选审计、同状态反事实、蒸馏和 promotion 判定均已落地且可重放；隔离未被破坏。降级点是模板问题与非 live LLM/KG runner。没有任何规则通过 promotion，不得启动 SP5，不得声称 Self-Play Memory 提升了 PoG 的 EM/F1。
