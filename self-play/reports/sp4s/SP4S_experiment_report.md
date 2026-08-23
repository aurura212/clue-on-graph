# SP4-SUPPLEMENT 实验报告：同状态反事实、共享关系 split、自然语言问句与 snapshot Critic

> 报告目录：`self-play/reports/sp4s/`  
> 计划版本：SP4-SUPPLEMENT 1.0  
> 协议版本：`sp-protocol-v1`  
> 总体要求：SP-GENERAL 1.22（本报告生成时；收口登记后为 1.23）  
> 验收结论：**CONDITIONAL PASS**  
> 报告日期：2026-08-23  
> 有效 Run：`sp4s-20260823T082040Z-7cecbcb0`

本报告只覆盖独立 `self-play/` 的 **SP4 补充实验**。它不改写已冻结的 SP4 CONDITIONAL PASS，不启动 SP5，也不是 WebQSP/CWQ 的 EM/F1，更不是 V2-5 Self-Play。`promoted_memory` 条数为 0，空 memory 只作为失败证据，不得注入 PoG。生成、调参和 promotion 均未使用 WebQSP 20/150 或 CWQ 50。

## 1. 研究目标

补 SP4 的方法学缺口和 CONDITIONAL 降级项，回答：

1. 反事实是否能绑定到抽出候选时的原决策 checkpoint，并把 `inapplicable` 与 `invalid` 分开；
2. 四个 split 能否隔离实体、答案、题目和 path 实例，同时共享同一关系词表；
3. 问句能否用多模板（可选 LLM）表述，且不泄漏 relation ID / 答案 / 路径；
4. 在冻结 snapshot 上，压缩上下文的 LLM Critic 是否可接（本 run 默认关闭）；
5. 在 **不改** `PROMOTION_GATES` 的前提下，蒸馏规则能否 promotion。

0 条 promoted 仍是合法结论；不得据此启动 SP5。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/06A_SP4_supplement_same_state_nl_live_critic.md` SP4-SUPPLEMENT 1.0 |
| overall | SP-GENERAL 1.22（生成时） |
| 协议 | `sp-protocol-v1` |
| 配置 | `configs/sp4s_supplement_v1.json` |
| 快照 | `sp4s-shared-rel-graph-v1`，snapshot hash `a9007ed3b17ba2e20daf68a8b132441f275b523647f3186d1d3fa1a414bfd660` |
| 合成 manifest | `a75db6baa9a257119078f9bd5520c3e5943d0be6cfb7714320763ee13932b365` |
| 生成器 | `sp4s-synthetic-v1`；seed=`20260823` |
| verbalizer | `multi_template_v1`（多模板同义改写；本 run 未调用 LLM 改写） |
| LLM Critic | **未调用**（`--allow-llm` 关闭；G1 为 snapshot 启发式 O0 Critic） |
| live KG 子图 | **跳过**（`--allow-live-kg` 关闭；不作为 PASS 主张） |
| memory_read / 注入 | false；空 `promoted_memory` 不注入 |
| PROMOTION_GATES | 未改：audit_pass_rate=1.0，min_discovery_tasks=3，min_cf_states=5，min_margin=0.20，max_harm_rate=0.10，min_v1_triggers=5，min_cost_drop=0.10 |
| 评测集 20/150/50 | 未用于生成、调参或 promotion |
| 旧 `sp4_*` 产物 | 只读对照，未回改 |

## 3. 有效运行

| 项 | 值 |
|---|---|
| 有效 Run | `sp4s-20260823T082040Z-7cecbcb0` |
| 无效 Run | 前一次 `sp4s-20260823T081941Z-6a1e2139` 因 prefix replay 的 `action_id` 重解析导致 CF `invalid_rate=1.0`，**不作有效结论** |
| 离线测试 | `tests/test_sp4s_offline.py` 8 通过 / 0 失败 |
| 密钥 | 未写入配置或报告 |

## 4. 合成任务与严格 split

新 snapshot 四个连通分量使用不同实体，**关系 ID 相同**；path-signature 用实体有序实例，而不是给关系加 `.v1` / `.v2` / `.h` 后缀。Actor JSONL 与 Oracle JSONL 物理分离。split 交叉污染为 0，问句泄漏为 0。

共享关系词表：

- `people.person.friend`
- `people.person.place_of_birth`
- `location.location.containedby`
- `people.person.spouse_s`
- `sports.pro_athlete.teams`
- `sports.sports_team_roster.team`

| split | n | 角色 |
|---|---|---|
| discovery | 40 | 轨迹、候选抽出、蒸馏 |
| validation_v1 | 20 | 规则触发与成功/成本 held-out |
| validation_v2 | 20 | 第二 held-out |
| holdout | 20 | 未参与候选生成 |

生成统计：生成 42 条路径尝试，保留 136 条（含多模板问句）；不可执行 8；歧义 0；泄漏 0；LLM 改写使用 0。

文件哈希（合成 manifest）：

| 文件 | SHA-256 |
|---|---|
| snapshot | `ebff044e3c4f8da3e7e70c99313da9b8da5f40936dc295b7936aa5278c829342` |
| discovery | `d2410ad08151b096e297e2beb5fba7f1984e747eaa5b231cb913cb049df0179a` |
| validation_v1 | `f73110dcfdb3dcfd0dc004f5f7090394455b72e57c580bd8b2a70f5798d71e04` |
| validation_v2 | `9e89663e92b470d0a1de6818af3990e29c071e1b17fe09df9b33d7a2b510aa66` |
| holdout | `fc440ba9dd91aef8e91126e4cbb2c3834cb9d21c3d9a1e7901be7d116fa1b420` |
| oracle_discovery | `a05cc49cf87521b5fe1dd55b4d3bd0ae373db1c347d179f42cc37f260fd11253` |
| oracle_v1 | `61d1c821140d032aedcf68e82b3f99897f4dae16aa1c204913d8326e4627535d` |
| oracle_v2 | `dee1009c741412d18fb03df497d382a69effe6a628c9d296d30478bd56ec714d` |
| oracle_holdout | `5b7ee32749fa63684b9ca975b91b2e6b99dfaa91c8430460d7404f95bde3f61c` |
| counterfactual | `6ef1d56c9ef4c250ad847272fad24ed7ef06ea235b8866325e8d76c3d64e8008` |
| exposure | `44bbbf36612090741720b85ac850b69d1cf0c1c5fec4ab2d02f4079fdd34fded` |
| validation_registry | `169e3784053826380591d6942c17418b27b3d107f18675a9e835a069a91c42f0` |

## 5. 多轨迹与 Critic

在 discovery 上运行 G0 Explorer-only、G1 O0 Critic（本 run 为启发式 snapshot Critic）、G2 offline teacher、G3 random critic。轨迹完整率与确定性 replay 均为 1.0。G2 / G3 **不得**并入 O0 promotion。

| 组 | n | 来源标签 | 成功率 | 恢复率 | 完整率 | replay | 均 Critic 轮数 |
|---|---|---|---|---|---|---|---|
| G0 | 40 | explorer_only | 0.25 | 0.0 | 1.0 | 1.0 | 0.0 |
| G1 | 120 | o0_critic | 0.4 | 0.25 | 1.0 | 1.0 | 1.675 |
| G2 | 40 | oracle_guided_offline_teacher | 0.825 | 0.0 | 1.0 | 1.0 | 0.0 |
| G3 | 40 | random_critic | 0.225 | 0.15 | 1.0 | 1.0 | 1.85 |

G1 相对 G0 的成功率差来自启发式 snapshot Critic，**不能**写成 memory 增益，也不能写成 live LLM Critic 已验收。压缩上下文 LLM Critic 代码已接入，但本 run 未实呼。live Freebase 子图未导出。

## 6. 同状态反事实

每个候选绑定抽出时的 `replay_prefix`、`decision_state_hash`、可见关系和剩余预算。流程：replay 到该 checkpoint → CF0 原动作、CF1 候选、CF2 随机合法、sham。关系不在可见集合记 `inapplicable`，不计入 margin 分母；真正非法才记 `invalid`。

| 指标 | 值 |
|---|---|
| n | 120 |
| n_applicable | 120 |
| win_rate | 0.0 |
| tie_rate | 1.0 |
| invalid_rate | 0.0 |
| inapplicable_rate | 0.0 |
| harm_rate | 0.0 |
| sham 更优 | 0 |

同状态协议可测：invalid=0，inapplicable=0。候选与原动作在该 checkpoint 上全部打平（tie=1.0），没有稳定正收益，也没有 sham 假阳性。这仍说明**经验尚未证明有用**，但不能再把“状态不对”算成“经验有害/无用”。SP3 的 WebQSP 候选仍未在本图上做同状态执行，不得跨图写成有效 CF。

## 7. 蒸馏与 promotion

只用同状态可绑定、非 `random_critic`-only、去实体去答案的规则。门槛与 SP4 冻结值相同。蒸馏 6 条规则，**0 条 promoted**，0 条 `rejected_harmful`，6 条 `deferred`。空 `promoted_memory` 作为失败证据写入，不注入。

promotion manifest hash：`678c11ecce3555137e519d526e9633dc8108366ed6f9233f091456f029b1c75b`。

| 规则 | 关系模式 | discovery 任务数 | CF 状态数 | 状态 | 未过门槛 |
|---|---|---|---|---|---|
| `sp4-rule-3ba8b45c4b79c092` | `location.location.containedby` | 7 | 21 | deferred | margin、v1_triggers、v1_success_or_cost |
| `sp4-rule-4015b80a1b60f382` | `people.person.spouse_s` | 3 | 9 | deferred | margin、v1_success_or_cost |
| `sp4-rule-60135778faa4100c` | `people.person.friend` | 13 | 39 | deferred | margin、v1_success_or_cost |
| `sp4-rule-b822f1bd24035368` | `location.location.containedby` | 5 | 15 | deferred | margin、v1_triggers、v1_success_or_cost |
| `sp4-rule-ef00cfa349d82295` | `people.person.spouse_s` | 5 | 15 | deferred | margin、v1_success_or_cost |
| `sp4-rule-f50347e59a7dd3b8` | `people.person.friend` | 7 | 21 | deferred | margin、v1_success_or_cost |

未过门槛的典型原因：`margin`（win−harm=0）、`v1_triggers`、`v1_success_or_cost`。因此没有任何规则具备进入 SP5 的资格。

## 8. 验收

| 项 | 门槛 | 实际 | 是否通过 |
|---|---|---|---|
| 同状态 CF 协议可测 | 是 | n=120，invalid=0，inapplicable 单独记账 | 是 |
| 严格 split 不交叉 | 0 | 0 | 是 |
| 问句泄漏 | 0 | 0 | 是 |
| 未使用 20/150/50 | 是 | 是 | 是 |
| PROMOTION_GATES 未改 | 是 | 是 | 是 |
| 多轨迹 replay | 可重放 | G0–G3 replay 100% | 是 |
| G2/G3 不并入 O0 | 是 | 是 | 是 |
| promotion 或完整失败证据 | 是 | 0 promoted，6 deferred | 是 |
| 自然语言问句 | 计划目标 | 多模板 verbalizer；未开 LLM 改写 | **否，故 CONDITIONAL** |
| snapshot 上真 LLM Critic | 计划目标 | 未实呼 | **否，故 CONDITIONAL** |
| live KG 子图 | 可选后续 | 跳过 | 不作为 PASS 主张 |
| 至少一条规则 promotion | 进入 SP5 条件 | 0 | 否，不启动 SP5 |

## 9. 未解决风险

1. 问句仍是多模板 verbalizer，不是自由自然语言 / LLM 生成问句。
2. Critic 仍是冻结 snapshot 上的启发式 backend，不是真 LLM Critic。
3. 同状态 CF 已可测，但 win_rate=0、tie_rate=1.0，蒸馏规则仍过不了 margin / V1 门槛。
4. SP3 WebQSP 候选仍不能在本 snapshot 上重建原决策状态，不得跨图 promotion。
5. 原 PoG `BACKTRACK` 仍 unsupported；补充实验只维持协议门禁，不实现 SP5 的 PB。
6. 不得把 G1>G0 写成 memory 成功。

## 10. 产物索引

```text
self-play/configs/sp4s_supplement_v1.json
self-play/prompts/sp4s_critic_o0_v1.txt
self-play/prompts/sp4s_verbalizer_v1.txt
self-play/artifacts/datasets/sp4s_kg_snapshot_v1.json
self-play/artifacts/datasets/sp4s_synthetic_discovery_v1.jsonl
self-play/artifacts/datasets/sp4s_synthetic_discovery_oracle_v1.jsonl
self-play/artifacts/datasets/sp4s_validation_v1.jsonl
self-play/artifacts/datasets/sp4s_validation_v1_oracle.jsonl
self-play/artifacts/datasets/sp4s_validation_v2.jsonl
self-play/artifacts/datasets/sp4s_validation_v2_oracle.jsonl
self-play/artifacts/datasets/sp4s_synthetic_holdout_v1.jsonl
self-play/artifacts/datasets/sp4s_synthetic_holdout_oracle_v1.jsonl
self-play/artifacts/datasets/sp4s_synthetic_manifest_v1.json
self-play/artifacts/datasets/sp4s_counterfactual_v1.jsonl
self-play/artifacts/registries/sp4s_validation_registry_v1.json
self-play/artifacts/registries/sp4s_exposure_registry_v1.json
self-play/artifacts/counterfactual/sp4s_counterfactual_results_v1.jsonl
self-play/artifacts/candidates/sp4s_validated_candidates_v1.jsonl
self-play/artifacts/candidates/sp4s_rejected_candidates_v1.jsonl
self-play/artifacts/memory/sp4s_promotion_decisions_v1.jsonl
self-play/artifacts/memory/sp4s_memory_manifest_v1.json
self-play/artifacts/memory/sp4s_promoted_memory_empty.json
self-play/artifacts/protocol/sp4s_check_result.json
self-play/artifacts/protocol/sp4s_live_kg_subgraph.json
self-play/reports/sp4s/SP4S_experiment_report.md
self-play/reports/sp4s/metrics.json
```

## 11. 结论

SP4-SUPPLEMENT **CONDITIONAL PASS**。同状态反事实协议、共享关系且实体隔离的 split、多模板问句泄漏审计、以及不改门槛的蒸馏/promotion 判定均已落地且可重放。登记降级仍是：多模板而非 LLM 问句、启发式而非实呼 LLM Critic、未导出 live KG 子图。0 条规则 promotion，不得启动 SP5，不得把空 memory 注入 PoG，不得声称 Self-Play Memory 提升了 PoG 的 EM/F1。
