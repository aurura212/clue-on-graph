# SP4 补充实验：同状态反事实、共享关系 split、自然语言问句与 snapshot LLM Critic

> 文档编号：SP4-SUPPLEMENT-PLAN  
> 版本：1.0  
> 制定日期：2026-08-23  
> 状态：CONDITIONAL PASS 并收口  
> 所属阶段：SP4 补充验证，不构成 SP5，也不改写已收口的 SP4 CONDITIONAL PASS  
> 上位约束：`00_experiment_overall_requirements.md`（启动 1.22；收口后 1.23）  
> 主计划：`06_SP4_precondition_counterfactual_distillation_promotion_v2.md` SP4-PLAN 2.1  
> 前置报告：`reports/sp4/SP4_experiment_report.md`  
> 前置结论：SP4 CONDITIONAL PASS；0 条 promoted_memory；模板问句；启发式 Critic；CF 在任务起点绑定导致 invalid 偏高

## 1. 补充实验定位

本计划补 SP4 的方法学缺口和 CONDITIONAL 降级项，不进入 SP5，不放宽 `PROMOTION_GATES`，不使用 WebQSP 20/150 或 CWQ 50，不把空 memory 注入 PoG。

```text
登记 SP4-SUPPLEMENT
  -> 同状态 checkpoint 反事实（inapplicable 与 invalid 分离）
  -> 共享关系词表、实体隔离的 sp4s snapshot
  -> 多模板 / 可选 LLM 问句 + 泄漏审计
  -> 冻结 snapshot 上的压缩上下文 LLM Critic（默认关；显式 flag 才开）
  -> 不改门槛重跑蒸馏与 promotion
  -> 补充报告；0 promoted 仍禁止 SP5
```

旧 `sp4_*` 产物只读对照。新产物前缀 `sp4s-*`。

## 2. 禁止事项

- 启动 SP5 或正式 WebQSP/CWQ EM/F1
- 修改 `promotion.py` 的 `PROMOTION_GATES`
- 实现 PoG BACKTRACK / SP5 PB
- 把 G1>G0 写成 memory 增益
- 用 20/150/50 生成、调参或 promotion
- 改写已冻结 SP3/SP4 v1 文件
- 把跨图、非原决策状态的 CF 写成有效证据

## 3. 步骤

1. **S4S.1 同状态 CF**：候选必须带抽出时的 replay 前缀、state hash、可见关系、剩余预算。关系不可见记 `inapplicable`，不计入 margin 分母。
2. **S4S.2 新 snapshot**：四连通分量实体不交，关系 ID 相同；path-signature 用实体有序元组。规模 discovery≥40、V1/V2/holdout≥20。
3. **S4S.3 问句**：A 档多模板同义改写；B 档可选 LLM，生成后跑泄漏扫描。Actor/Oracle 物理分离。
4. **S4S.4 LLM Critic**：只在冻结 snapshot ReplayEnvironment 上调用；G2/G3 不并入 O0。live KG 子图本补充实验不跑。
5. **S4S.5 promotion**：门槛不变。0 条 promoted 是合法结论。

## 4. 验收

补充实验 PASS 需要：同状态 CF 协议可测；split 污染 0；问句泄漏 0；未改 promotion 门槛；未用 20/150/50。LLM Critic 若因无密钥未实呼，必须在报告中登记，不得写成无条件 live Critic PASS。仍 0 promoted 则不得启动 SP5。

## 5. 实验日志

### LOG-S4S-1 — 2026-08-23 — SP4-SUPPLEMENT CONDITIONAL PASS

- 有效 run：`sp4s-20260823T082040Z-7cecbcb0`
- 配置：`configs/sp4s_supplement_v1.json` SHA-256 `d50d56a4c74807a1dc61b1ce5c81ba20f82cfaad442b8c5c0141937865378b92`
- 报告：`reports/sp4s/SP4S_experiment_report.md` SHA-256 `bd59b193edbdf82e04ee42fc53c774d2115712a97053a724a48b45ef6eb2295b`
- 规模：discovery 40 / V1 20 / V2 20 / holdout 20；共享 6 个关系 ID；实体按 split 隔离
- 问句：多模板 verbalizer；泄漏 0；未调用 LLM 改写
- Critic：G0/G1/G2/G3 replay 100%；G1 启发式 snapshot Critic；`--allow-llm` 未开；live KG 子图跳过
- 同状态 CF：n=120，applicable=120，invalid=0，win=0，tie=1.0，harm=0
- Promotion：6 条规则全部 deferred；n_promoted=0；PROMOTION_GATES 未改
- 结论：CONDITIONAL PASS。SP4 主结论不改写。不得启动 SP5，不得注入空 memory


### LOG-S4S-2 — 2026-08-23 — 报告中文化

- 将 `reports/sp4s/SP4S_experiment_report.md` 改写为中文叙述，数字与结论不变
- 新 SHA-256：`bd59b193edbdf82e04ee42fc53c774d2115712a97053a724a48b45ef6eb2295b`
- 未重跑实验；仍为 CONDITIONAL PASS；不得启动 SP5
