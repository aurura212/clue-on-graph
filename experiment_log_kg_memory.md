# KG-Structural Memory 实验日志

对应方案：**执行依据** [`experimental_plan_kg_memory_v2.md`](experimental_plan_kg_memory_v2.md)。旧方案 [`experimental_plan_kg_memory_from_gpt56.md`](experimental_plan_kg_memory_from_gpt56.md) 仅解释 LOG-000–049。

本文件是该实验的**唯一进度源**。后续实现、跑数、评测、是否进入下一阶段，一律以这里的「当前状态」和已记录结果为准，而不是凭对话记忆。

---

## 使用约定

1. 每次开始与本实验相关的工作前，先读「当前状态」和最近一条实验记录。
2. 「下一步」只由本日志 + 方案中的阶段门槛决定，禁止跳阶段、禁止在门槛未过时做 Self-Play。
3. 每次实验结束后（含失败、中断、smoke、构建、评测、仅改代码未跑数）必须追加一条记录，并更新「当前状态」。
4. 验收未勾完 → 停留在当前 Phase。门槛未过 → `GATE_HOLD`，只做诊断，不扩大范围。
5. 触发方案 §16.3 回退条件 → 记 `ROLLBACK`，回到只诊断的 retrieval，禁止进入 Phase 5。
6. **切片角色（自 LOG-052）**：`hard150_v1` = `development_stress`（选阈值/压力测试，**不是**能力主结果）。`random150_v1` = `capability_eval` / V2 `final_unseen`（WebQSP test 均匀随机 150，seed=42）。方法冻结前不得对 `random150_v1` 跑 LLM。prefix150 与 first-hop M1/M2 数字只作档案。V2 跑数组 ID 为 `R0/R1/R2/R3/RC1/RC2/R4`。

记录编号从 `LOG-000` 起递增。

---

## 当前状态

| 字段 | 值 |
|---|---|
| 更新日期 | 2026-08-21 |
| 总体状态 | **V2 reflection 仍 `GATE_HOLD`。用户要求恢复原 PoG 栈（supervised relation memory + 约束编译），正在 hard150/random150 上复跑** |
| 当前 Phase | `V2-3`（reflection 不变）+ **原栈切片复跑进行中** |
| 判定 | `PLAN_REVISION`（仅开辟原栈评测；V2 reflection 变体仍停止扩大） |
| 已通过验收的 Phase | 同 LOG-063。V2-3 主评估未过 |
| 已冻结的 inference / memory 配置 | V2 R2 冻结配置不得再改去重跑。本次原栈：`run_PoG_test.sh`（relation_memory=prompt top2 hybrid stages=relation；constraint_pushdown=on；constraint_routing=auto；kg_memory=none） |
| **已冻结的校验切片** | hard150 / random150 角色不变。本次是**另一方法**在同切片上评测，不得与 R2 混报 |
| 阻塞 | 原栈两个切片跑数尚未完成 |
| 禁止事项 | 不得把本次数字写成 V2-3 成功。不得把 hard150 当能力主结果。不得 Self-Play。不得改 R2 后重跑 random150。hard150 由「orig PoG 与 20260814 rel+decomp 全错」构成，旧全量在其上 EM=0 是切片定义。 |

### 下一步（唯一允许的动作）

等待原栈 hard150 / random150 跑完并写评测。V2 reflection 仍不得扩大。

---

## 阶段推进判定（后续必须执行）

每次更新「当前状态」时按此算法，输出只能是下列之一：

| 判定 | 含义 |
|---|---|
| `CONTINUE_PHASE_N` | 当前 Phase 验收未完成，继续该 Phase 清单中的下一项 |
| `ACCEPT_PHASE_N_AND_ADVANCE` | 当前 Phase 验收全部通过，进入下一允许阶段 |
| `GATE_HOLD` | 阶段验收过了，但效果门槛未过；只分析，不扩大 |
| `ROLLBACK` | 触发 §16.3；回退到诊断-only retrieval |
| `STOP_DIRECTION` | 结构记忆方向不成立，停止扩大范围 |
| `PLAN_REVISION` | 根据已完成实验结果修订执行主线；旧分支冻结，新分支须先完成 V2-0 审计 |

### 旧方案前进路径（已归档）

```text
pre
  → Phase 0（冻结 + trace 补全 + B0 复跑）
  → Phase 1（Protocol 1，只建 validated schema_profile）
  → Phase 3 的 M1 切片（relation rerank，不做 hard filter）
  → Pilot：B0 / M1 / C1 / C2（100–200 题）
  → 若 M1 优于 C1/C2，且无显著 recall 损失
        → Phase 2（path template）
        → Phase 3 的 M2/M3
        → 若 relation 记忆确认有效 → Phase 4（M4/M5/M6）
        → 若 M3 或 M6 相对 B0 稳定正增益，且优于 C1/C2 → 才允许 Phase 5
  → 否则 GATE_HOLD / ROLLBACK，禁止 Phase 2 与 Phase 5
```

上述路径仅用于解释 LOG-000 至 LOG-049 的历史推进，不再作为后续执行路径。

### V2 默认前进路径

```text
LOG-050 PLAN_REVISION
  → V2-0：协议审计、final_unseen 准备、timeout/retry/分母冻结
  → V2-1：Decision A/B evidence gate 与 trace 单测
  → V2-2：R0/R1/R2/R3/RC1/RC2 smoke
  → V2-2：hard150 development stress pilot
  → 若 reflection 阶段有可归因增益且优于 RC1/RC2
        → V2-3：冻结 final_unseen 并一次性运行
        → V2-4：跨数据集与 trajectory-memory 对照
        → 满足独立门槛后才可评估 V2-5 Self-play
  → 否则 GATE_HOLD / ROLLBACK，停止当前 reflection 变体
```

V2 不要求 first-hop relation memory 先通过；但 V2-0 审计未完成前，不得启动 R1/R2/R3 正式评测。

### 进入完整实验的门槛（方案 §16.1）

Pilot 中 **M3 或 M6** 至少满足以下之一，且没有显著 gold-next-relation recall 损失：

- EM/F1 相对 B0 有稳定正提升；或
- 准确率持平但 calls / tokens / 展开实体数明显下降；或
- 在 memory-covered subset 上有清晰提升，且 harmful intervention 可被 confidence 过滤控制。

在最小闭环里，**M1 vs C1/C2** 是进入 Phase 2 的前置门槛；不要用“看起来有希望”代替对照。

### 进入 Phase 5 Self-Play 的门槛（方案 §16.2）

须同时满足：

- Protocol 1/2 的真实 memory 优于 C1/C2；
- 至少一个注入阶段有可归因的正效果；
- provenance、复验和阶段 trace 完整。

### 暂停或回退条件（方案 §16.3）

出现任一项即 `ROLLBACK`：

- relation recall 因 rerank 持续下降；
- shuffled / irrelevant control 与真实 memory 效果相当；
- 增益只来自额外 token，效率显著恶化；
- memory evidence 无法重放，或 discovery/validation 泄漏；
- reflection 的 misleading-memory rate 高于挽救率。

---

## 基线冻结快照（实验开始时填写，之后只追加不改写）

记录于 2026-08-18，**KG 结构记忆实验尚未改代码、尚未跑数**。下列信息是仓库现状，不是本实验的 B0 结果。

| 项 | 值 |
|---|---|
| git branch | `main` |
| HEAD commit | `d935572fde54640028de09cdba1dd97fbfaffa66` |
| HEAD 说明 | 进一步优化约束编译，解决因实体过多导致约束编译失败或运行时间过长的问题 |
| 工作树 | `.gitignore` 有未提交修改；结构记忆相关新文件尚不存在 |
| 已有结构记忆模块 | **无**（`kg_probe.py` / `kg_structural_memory.py` 等均未创建） |
| 已有 supervised memory | `PoG/relation_memory.py`、`PoG/decomposition_memory.py`（本实验不删除，作为 B1/B2） |

近期一次 **supervised memory** 全量 test（**不是**本实验 B0/B2，仅作对照参考，不可当作 Phase 0 已完成）：

| 项 | 值 |
|---|---|
| run | `PoG/result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n839_20260816_074154/` |
| 配置含义 | WebQSP test，relation+decomposition memory prompt，约等于方案中的 B2 风格 |
| n | 839 |
| LLM | `gpt-3.5-turbo-0125` |
| temperature | exploration 0.3 / reasoning 0.3 |
| depth | 4 |
| EM / F1 | 0.8415 / 0.738 |
| avg calls / time / tokens | 8.64 / 18.53s / 6345.7 |

Phase 0 真正的 B0 必须是：**无 reference、无 relation/decomposition memory、无 KG 结构记忆** 的原始 PoG。上表不能替代 B0。

Phase 0 冻结配置（2026-08-18 写入，之后只追加不改写）：

| 项 | 值 |
|---|---|
| 冻结 commit | `d935572fde54640028de09cdba1dd97fbfaffa66`（trace 补全将作为该冻结之上的 instrumentation-only 改动） |
| LLM | `gpt-3.5-turbo-0125` |
| temperature_exploration / reasoning | `0.3` / `0.3` |
| max depth | `4` |
| max_length | `4096` |
| KG endpoint | `http://localhost:8890/sparql` |
| 数据集与切片 | WebQSP test，`--start 0 --limit 20`（smoke，不作结论） |
| 约束 | `--constraint_pushdown off --constraint_routing off` |
| B0 | `--reference_mode none --relation_memory_mode none --decomposition_memory_mode none` |
| B1/B2 记忆库 | `PoG/memory/webqsp_gpt-3.5-turbo-0125_train_n600_20260703_231525/` |
| B0 smoke run_dir | `PoG/result/webqsp_gpt-3.5-turbo-0125_n20_20260818_110222/` |
| B1 smoke run_dir | `PoG/result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_n20_20260818_111256/` |
| B2 smoke run_dir | `PoG/result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n20_20260818_111849/` |
| 补 trace 不改变答案 | 按计划：`prompt_list.py` 无 diff；`run_llm` 参数未改；过滤/排序逻辑未改。B0/B1/B2 的 `check_phase0_trace.py` 均 PASS |

---

## Phase 验收清单

勾选规则：只有对应实验记录给出证据后才能勾。禁止提前勾选。

### Phase 0：基线冻结与 trace 补全

- [x] 代码 commit、LLM、temperature、prompts、KG endpoint、数据切片已写入上方冻结表（LOG-001）
- [x] 未引入任何 KG 结构记忆（仅空 `kg_memory` scaffold，无 `kg_probe.py`）
- [x] `pog_trace.jsonl` 含 relation 候选前后顺序（LOG-003 checker PASS；B0 有 `candidate_relations`/`selected_relations`/`pre_relations`）
- [x] `pog_trace.jsonl` 可拆出 reflection Decision A / B（B0：8 个走过 reflection 的 depth 均有 `decision_a`/`decision_b`）
- [x] 实体探索统计可离线计算（每 depth 有 `exploration_stats` 与 `entity_search`）
- [x] 相同配置下，补 trace 前后答案一致（git diff 证明未改 prompt/`run_llm`/过滤；不另付 T=0.3 双跑）
- [x] 小规模 B0 可复现（`results.jsonl` / `pog_trace.jsonl` / `run_meta.json` 齐全，LOG-003）
- [x] 关闭未来 memory 开关时的行为约定已明确：等价 B0（`run_PoG_phase0_baseline.sh GROUP=B0`：reference/relation/decomp 均为 none，约束关闭）

**Phase 0 通过后才能进入 Phase 1。**

### V2-0：协议审计与切片冻结

- [x] `hard150_v1` 标记为 `development_stress`
- [x] `random150_v1` 已构建并冻结（能力主评估 / final unseen；不跑评测）
- [x] 分母 150 vs relation-valid 143 的说明写入日志（LOG-054：hard150 143/150；random150 149/150）
- [x] timeout / retry / max-token / seed 统一并记录（180s / 5 / 4096 / 40 / seed=42）
- [x] `semantic_filter_relations()` 与 reflection evidence 的顺序已确认（先 SPARQL+语义过滤，再 Decision A/B evidence）
- [x] Decision A/B evidence trace schema 落地
- [x] memory-off 与 B0 等价 checker 通过（`python check_v2_protocol.py` ALL CHECKS PASSED）

**V2-0 未勾完不得启动 R1/R2/R3。** 本条已勾完；正式评测仍须先过 V2-2 smoke。

### V2-1：Reflection evidence 单元测试

- [x] 有 witness 的正例产生正向干预（LOG-055）
- [x] 无 witness 归入 unknown，不干预
- [x] 低 confidence 不产生正向干预
- [x] 已探索路线不重复推荐
- [x] branching penalty 与 evidence/utility score 边界
- [x] shuffle/irrelevant 只改 evidence 内容，不改候选数和四段 prompt 结构
- [x] schema / fallback / trace / B0 equivalence checker 通过（`python check_reflection_memory.py` ALL CHECKS PASSED）

**V2-1 未勾完不得启动 V2-2 smoke。**

### V2-2：Reflection smoke 与 development

- [x] hard150 `START=0 LIMIT=20` R0/R1/R2/RC1/RC2 smoke 完成且协议 checker 通过（LOG-058）
- [x] hard150 n=150 development 完成（LOG-060）
- [x] 效果门槛在 hard150 development 上判定：R2 通过；R1 未通过；R3 未跑（LOG-060）

**V2-2 已勾完。V2-3 只允许冻结后对 `random150_v1` 跑一次，禁止中途调参。**

### V2-3：`random150_v1` 一次性主评估

- [x] 冻结后一次性运行 R0 / R2 / RC1 / RC2（LOG-061 启动，LOG-062 完成）
- [x] 主结果只报 `random150_v1`；hard150 不得替代、不得混报（LOG-062：R2 未过主评估）
- [x] GATE_HOLD 离线诊断完成（LOG-063）：continue 上升、翻盘题、R2 vs RC1

**V2-3 评测已完成。效果未过，判定 `GATE_HOLD`，不得进 V2-4。**

### Phase 1：Schema profile 构建

- [x] `kg_probe.py`、schema、manifest 已实现（LOG-007）
- [x] discovery / validation 实体按 entity ID 严格去重（checker PASS）
- [x] 构建可断点续跑；同配置 hash 相同（manifest hash round-trip）
- [x] 抽查 witness 可用 SPARQL 重放（full 20/20）
- [x] 记忆记录无 benchmark gold 字段（question / answer / gold path / gold SPARQL）
- [x] 输出 `kg_structural_memory.jsonl` + `build_manifest.json`

**Phase 1 通过后才能做 M1 注入；不要在此时实现 path template，除非判定已明确允许进入 Phase 2。**

**Phase 1 通过后才能做 M1 注入；不要在此时实现 path template，除非判定已明确允许进入 Phase 2。**

### Phase 2：Path template 构建

- [x] 仅在 M1 优于 C1/C2 之后启动（门槛 LOG-026；smoke LOG-028；full LOG-029）
- [x] 1/2-hop 模板均有 validation support 与 provenance（full：1-hop 179 / 2-hop 664；checker replay 20/20）
- [x] 未观察模式标记为 `unknown_or_low_support`，不写成负事实（full：96 条 unknown_or_low_support，无负事实）
- [x] 输出 `path_template` / `coverage`，必要时输出 `connectivity`（full 输出 path_template + validation_coverage；本轮不另建 connectivity 库）

### Phase 3：Relation memory 注入

- [x] 记忆只重排真实 KG 候选，不新增 relation（LOG-011 unit；LOG-012/013/014 smoke checker PASS）
- [x] `prompt` / `rerank` 可切换；默认无 hard filter（LOG-011 unit）
- [x] memory 关闭时行为与 B0 一致（mode=none 不调用 retrieval，LOG-011）
- [x] trace 可解释每次排序变化（M1：42 events / 219 hits / 34 order_changed）
- [x] 已跑对照：至少 M1 + C1 + C2（相对同一 B0）（smoke LOG-012–014；additive pilot LOG-019–020；gated smoke LOG-024；**gated pilot LOG-026 过门槛**）

**进入 Phase 2 的门槛（最小闭环）**：M1 优于 C1/C2，且无显著 gold-next-relation recall 损失。**曾通过（LOG-026，gated M1，prefix150 档案）。hard150 上未维持（LOG-047/048）。**

### Phase 4：Reflection memory 注入

- [ ] 仅在 relation 记忆确认有效后启动
- [ ] Decision A/B 证据可追溯，不把 memory conclusion 当事实答案
- [ ] 可统计 intervention 后果（premature stop / wasteful continuation / 有效回溯）
- [ ] 已跑 M4、M5，再跑 M6

### Phase 5：Self-play（条件阶段）

- [ ] §16.2 门槛已在本日志中显式判定为通过
- [ ] self-play 记录与纯 SPARQL 记忆分库或带 `source_protocol`
- [ ] 未通过门槛前此项必须保持未勾选

---

## 实验组完成情况

| ID | 配置 | 状态 | 证据记录 | 备注 |
|---|---|---|---|---|
| B0 | 原始 PoG，无 reference、无 memory | smoke + pilot | LOG-003 / LOG-019 | pilot n=150 EM 0.8467 gold_sel 0.7905 |
| B1 | supervised relation memory | smoke 完成 | LOG-004 | 同切片 |
| B2 | supervised decomp + relation memory | smoke 完成 | LOG-005 | 同切片；decomp context 20/20 非空 |
| M1 | schema_profile → relation | prefix150 档案过门槛；hard150 机制未过 | LOG-012 / LOG-026 / LOG-047 / LOG-048 / LOG-049 | hard150 EM 0.1933 相对 C1 净 +3 **不可归因**（翻盘题 depth1 top1 全同）。`STOP_DIRECTION` |
| M2 | path_template → relation | prefix150 + hard150；notail 诊断 | LOG-028–045 | **hard150 H4 未过，first-hop 放弃**：M2 EM 0.1467；M2-notail EM **0.1733=B0** 仍 < M1/C1 0.1933、C2 0.1800。path 不再用于 first-hop rerank |
| M3 | M1+M2 → relation | 不跑 | LOG-049 | first-hop 方向已 `STOP_DIRECTION` |
| M4 | coverage → reflection A | 不启动 | LOG-049 | §18：relation 记忆未在 hard150 确认有效 |
| M5 | connectivity → reflection B | 不启动 | LOG-049 | |
| M6 | M3+M4+M5 | 不启动 | LOG-049 | |
| C1 | shuffled memory | prefix150 档案；hard150 M1-C1 完成 | LOG-013 / LOG-019 / LOG-026 / LOG-047 | hard150 schema C1 EM **0.1733** gold_sel 0.4685 |
| C2 | irrelevant structural scores | prefix150 档案；hard150 M1-C2 完成 | LOG-014 / LOG-020 / LOG-026 / LOG-047 | hard150 schema C2 EM **0.1800** gold_sel 0.4615 |
| R0 | B0，无 reflection memory | **V2-3 主基线完成** | LOG-054–062 | **random150 EM 0.8533（128/150）**；hard150 0.1800 只作进门 |
| R1 | KG evidence → Decision A | hard150 完成，未进 V2-3 | LOG-055/058/060 | hard150 EM 0.1733，未过 §11.1 |
| R2 | KG evidence → Decision B | **V2-3 主评估未过** | LOG-060/062 | **random150 EM 0.8067（121/150）< R0**；hard150 0.2000 不得当主结果 |
| R3 | R1+R2 | 不跑 | LOG-060 | 无 development n=150 |
| RC1 | shuffle reflection evidence | V2-3 完成 | LOG-060/062 | **random150 EM 0.8133（122/150）≥ R2** |
| RC2 | irrelevant reflection evidence | V2-3 完成 | LOG-060/062 | random150 EM 0.7867（118/150） |
| R4 | evidence + confidence gate | 不默认启动 | | 仅当 R1/R2 trace 证明 harmful intervention 后才开 |

规模约定（方案 §12）：smoke 10–20 题不得用于结论；pilot 100–200 题只用于选超参；final test 一次冻结，禁止看 test 后调阈值。**自 LOG-040 起，上述题数都在 `hard150_v1` 切片内计数，不再用 WebQSP test 前 N 题。**

---

## 记录模板

追加新记录时复制此块，不要改已有记录。

```md
### LOG-XXX — YYYY-MM-DD — <短标题>

- 判定前状态：Phase / 判定
- 类型：`code` | `build` | `run` | `eval` | `decision` | `incident`
- 对应方案：Phase N / 实验组 ID / 规模（smoke|pilot|dev|test）
- 代码：commit / 脏工作树说明
- 配置：LLM、temperature、depth、dataset、slice、memory mode/path/hash、strategy、top_k、token budget、ablation、seed
- 产物：run_dir 或 memory_build_id；关键文件是否齐全
- 结果：EM/F1、calls、tokens、runtime；阶段指标（relation recall、展开规模、reflection 诊断）；对照谁
- 异常：失败、泄漏、无法重放、开关关闭不等价
- 结论：本条是否推进验收；是否触发门槛或回退
- 判定后状态：Phase / 判定 / 下一步一句
```

---

## 实验记录

### LOG-000 — 2026-08-18 — 创建实验日志，实验尚未开始

- 判定前状态：无 / 无
- 类型：`decision`
- 对应方案：全文；当前应执行 Phase 0
- 代码：HEAD `d935572`；尚无结构记忆模块
- 配置：未冻结
- 产物：本日志文件
- 结果：无实验数据
- 异常：无
- 结论：不能把 2026-08-16 的 supervised memory 全量 WebQSP run 当作本实验 B0/B2。必须从 Phase 0 重新冻结并跑无记忆基线。
- 判定后状态：`pre` / `CONTINUE_PHASE_0` / 下一步只做 Phase 0 冻结与 trace 补全

### LOG-001 — 2026-08-18 — Phase 0 冻结配置

- 判定前状态：`pre` / `CONTINUE_PHASE_0`
- 类型：`decision`
- 对应方案：Phase 0；实验组 B0/B1/B2；规模 smoke
- 代码：HEAD `d935572`；工作树仅 `.gitignore` 脏；尚无结构记忆模块
- 配置：WebQSP test `start=0 limit=20`；LLM `gpt-3.5-turbo-0125`；temp 0.3/0.3；depth 4；SPARQL `http://localhost:8890/sparql`；`constraint_pushdown=off`、`constraint_routing=off`；B0 无 reference/memory；B1/B2 使用 `memory/webqsp_gpt-3.5-turbo-0125_train_n600_20260703_231525/`
- 产物：本日志冻结表
- 结果：无跑数
- 异常：B2 必须显式 `--constraint_routing off`，否则 argparse 默认 `auto` 会跳过 decomposition memory
- 结论：冻结完成，尚未勾选 Phase 0 验收。下一步补 trace（不改 prompt）并跑 smoke。
- 判定后状态：Phase `0` / `CONTINUE_PHASE_0` / 补齐 relation、reflection A/B、实体探索 trace

### LOG-002 — 2026-08-18 — Phase 0 trace 补全（不改 prompt）

- 判定前状态：Phase `0` / `CONTINUE_PHASE_0`
- 类型：`code`
- 对应方案：Phase 0 instrumentation
- 代码：基于 `d935572` 的 working tree。改动文件：`PoG/trace_utils.py`、`PoG/freebase_func.py`（仅 `rel_trace` 字段）、`PoG/utils.py`（`if_finish_list` 多返回 reflection_trace；第二次 `run_llm` 仅变量改名为 `select_response`）、`PoG/main_freebase.py`（entity_search 事件、A/B 写入、exploration_stats）。新增 `PoG/check_phase0_trace.py`、`PoG/run_PoG_phase0_baseline.sh`。`PoG/prompt_list.py` 无 diff。
- 配置：同冻结表
- 产物：上述源码；尚无 run_dir
- 结果：本地 `py_compile` 与 `compute_exploration_stats` 自检通过
- 异常：无。补 trace 不改变答案的验收按计划采用 git diff + checker，不另付 pre-patch B0
- 结论：trace 代码已落地，尚未跑数。下一步 B0 smoke `start=0 limit=20`
- 判定后状态：Phase `0` / `CONTINUE_PHASE_0` / 跑 B0 smoke

### LOG-003 — 2026-08-18 — B0 smoke n=20

- 判定前状态：Phase `0` / `CONTINUE_PHASE_0`
- 类型：`run`
- 对应方案：Phase 0 / B0 / smoke
- 代码：instrumentation working tree on `d935572`
- 配置：WebQSP test `start=0 limit=20`；`reference_mode=none`；`relation_memory_mode=none`；`decomposition_memory_mode=none`；`constraint_pushdown=off`；`constraint_routing=off`；LLM `gpt-3.5-turbo-0125`；temp 0.3/0.3；depth 4
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_n20_20260818_110222/`（`results.jsonl` / `pog_trace.jsonl` / `run_meta.json` 齐全）
- 结果：EM 0.9000 (18/20)；F1 0.7845；avg calls 8.95；avg time 29.33s；avg tokens 5890.8。`check_phase0_trace.py --min-questions 20` PASS；decomp_memory_nonempty=0
- 异常：无。错题：`what type of art does marc chagall do?`、`when did bill clinton go to college?`
- 结论：B0 smoke 可复现且 stage trace 完整。不作为记忆有效性结论。下一步同切片 B1
- 判定后状态：Phase `0` / `CONTINUE_PHASE_0` / 跑 B1 smoke

### LOG-004 — 2026-08-18 — B1 smoke n=20

- 判定前状态：Phase `0` / `CONTINUE_PHASE_0`
- 类型：`run`
- 对应方案：Phase 0 / B1 / smoke
- 代码：同 LOG-002
- 配置：同 B0 切片；`relation_memory_mode=prompt` top_k=2 hybrid stages=relation；`decomposition_memory_mode=none`；`constraint_routing=off`；记忆库 `memory/webqsp_gpt-3.5-turbo-0125_train_n600_20260703_231525/`（Loaded 833 relation memory items）
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_n20_20260818_111256/`
- 结果：EM 0.9000 (18/20)；F1 0.8019；avg calls 8.15；avg time 15.77s；avg tokens 5788.3。checker PASS；decomp_memory_nonempty=0（符合 B1）
- 异常：无。错题与 B0 相同（smoke 不作记忆有效性结论）
- 结论：B1 smoke 完成。下一步同切片 B2，且必须确认 decomposition memory 实际注入
- 判定后状态：Phase `0` / `CONTINUE_PHASE_0` / 跑 B2 smoke

### LOG-005 — 2026-08-18 — B2 smoke n=20

- 判定前状态：Phase `0` / `CONTINUE_PHASE_0`
- 类型：`run`
- 对应方案：Phase 0 / B2 / smoke
- 代码：同 LOG-002
- 配置：同 B0 切片；`relation_memory_mode=prompt`；`decomposition_memory_mode=prompt`；**显式 `constraint_routing=off`**；Loaded 833 relation + 600 decomposition memory items
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_n20_20260818_111849/`
- 结果：EM 0.8500 (17/20)；F1 0.7333；avg calls 9.25；avg time 15.56s；avg tokens 6290.9。`check_phase0_trace.py --require-decomp-memory` PASS；decomp_memory_nonempty=20/20
- 异常：无。`run_meta.constraint_routing=off`、`decomposition_memory_mode=prompt`，确认 B2 未因 routing 默认 `auto` 而跳过 decomp memory
- 结论：B2 对照锚点已建立。smoke 不作记忆有效性结论
- 判定后状态：Phase `0` / `CONTINUE_PHASE_0` / 做 Phase 0 验收判定

### LOG-006 — 2026-08-18 — Phase 0 验收通过

- 判定前状态：Phase `0` / `CONTINUE_PHASE_0`
- 类型：`decision`
- 对应方案：Phase 0 验收
- 代码：instrumentation-only 相对 `d935572`；无 `kg_probe.py` / 结构记忆注入
- 配置：冻结表已填齐三个 smoke run_dir
- 产物：B0/B1/B2 smoke 三套 `results.jsonl` + `pog_trace.jsonl` + `run_meta.json`；`PoG/check_phase0_trace.py`；`PoG/run_PoG_phase0_baseline.sh`
- 结果：Phase 0 清单全部有证据。B0 checker：28 depths 均有 `exploration_stats`；8 个 reflection depth 均有 Decision A/B
- 异常：无回退条件
- 结论：Phase 0 验收通过。下一步 Phase 1 Protocol 1 / schema_profile。禁止此时做 path template、M1 注入或 Self-Play
- 判定后状态：Phase `1` / `ACCEPT_PHASE_0_AND_ADVANCE` / 开始 Phase 1 schema profile 构建

### LOG-007 — 2026-08-18 — Phase 1 代码落地（只建库）

- 判定前状态：Phase `1` / `ACCEPT_PHASE_0_AND_ADVANCE`
- 类型：`code`
- 对应方案：Phase 1 / Protocol 1 schema_profile
- 代码：新增 `PoG/kg_probe.py`、`PoG/kg_structural_memory.py`、`PoG/build_kg_structural_memory.py`、`PoG/check_kg_memory_build.py`、`PoG/run_build_kg_memory.sh`。未改 `main_freebase.py` / `relation_search_prune` / prompts
- 配置：smoke=5 types × (10 disc + 5 val)，full=150 × (50+30)，seed=42，min_support=3，min_coverage=0.2，endpoint `http://localhost:8890/sparql`，无 LLM
- 产物：上述源码；schema 单元测试通过
- 结果：无构建产物
- 异常：无
- 结论：可以开始 5-type smoke。失败则不启动 150-type 全量
- 判定后状态：Phase `1` / `CONTINUE_PHASE_1` / 跑 MODE=smoke

### LOG-008 — 2026-08-18 — schema_profile smoke 5 types

- 判定前状态：Phase `1` / `CONTINUE_PHASE_1`
- 类型：`build`
- 对应方案：Phase 1 smoke
- 代码：LOG-007 模块
- 配置：5 types × 10/5，seed=42
- 产物：`PoG/kg_memory/schema_smoke_e9332b8757_20260818_113901/`
- 结果：27 records，21 validated；`check_kg_memory_build.py --replay 20` PASS（20/20）。类型频次偏 music/common（`common.notable_for`、`common.document` 写出 0 条）
- 异常：无 SPARQL 失败。全量将额外排除 `common.*` 类型，避免占满 150 槽
- 结论：smoke 通过，启动 150-type 全量
- 判定后状态：Phase `1` / `CONTINUE_PHASE_1` / 跑 MODE=full

### LOG-009 — 2026-08-18 — schema_profile full 150 types

- 判定前状态：Phase `1` / `CONTINUE_PHASE_1`
- 类型：`build`
- 对应方案：Phase 1 full
- 代码：LOG-007；相对 smoke 额外排除 `common.*` 类型；neighbor detail 每 type 最多 40 条、每条最多 8 个实体
- 配置：150 types × 50 discovery + 30 validation；seed=42；min_support=3；min_coverage=0.2；hash `ee55ef9f175d45e5`
- 产物：`PoG/kg_memory/schema_full_ee55ef9f17_20260818_114056/`（jsonl / index / manifest / discovery / validation / progress / probe_cache）
- 结果：3529 records（validated 1477 / low_support 2052）；150 types 均有记录；含 `people.person`（40 条）。elapsed 812s；SPARQL queries 59088、cache hits 9258、retries 0。`check_kg_memory_build.py --replay 20` PASS（20/20）
- 异常：无。未读 benchmark gold。未改推理注入
- 结论：全量构建可复现、可重放。进入 Phase 1 验收
- 判定后状态：Phase `1` / `CONTINUE_PHASE_1` / 做 Phase 1 验收判定

### LOG-010 — 2026-08-18 — Phase 1 验收通过

- 判定前状态：Phase `1` / `CONTINUE_PHASE_1`
- 类型：`decision`
- 对应方案：Phase 1 验收
- 代码：仅新增建库模块；`relation_search_prune` 本阶段未再改
- 配置：冻结记忆库 `schema_full_ee55ef9f17_20260818_114056`
- 产物：同上
- 结果：Phase 1 清单全部有证据
- 异常：无回退条件
- 结论：Phase 1 通过。按 §18，下一步是 M1 relation rerank，不是 path template / Phase 2 / Self-Play
- 判定后状态：`3-M1` / `ACCEPT_PHASE_1_AND_ADVANCE` / 实现 schema_profile → relation rerank

### LOG-011 — 2026-08-19 — Phase 3 M1 代码落地（软 rerank，无 hard filter）

- 判定前状态：`3-M1` / `ACCEPT_PHASE_1_AND_ADVANCE`
- 类型：`code`
- 对应方案：Phase 3 / M1 / 尚未跑数
- 代码：工作树相对 `d935572` 脏；新增 `PoG/kg_memory_retrieval.py`、`PoG/check_kg_memory_m1.py`、`PoG/run_PoG_kg_memory_test.sh`；改 `relation_search_prune` / CLI / `run_meta` / output tag / depth trace。未实现 path template 或 reflection 注入。
- 配置：默认 `--kg_memory_strategy rerank`，semantic_weight=0.7，structure_weight=0.3，validated_only=1，min_confidence=0.6，ablation=none|shuffle|irrelevant；记忆库 `schema_full_ee55ef9f17_20260818_114056`
- 产物：unit checker `python check_kg_memory_m1.py` ALL CHECKS PASSED（含 frozen bank 加载与 “问题语义不被高频边盖过”）
- 结果：无 LLM 跑数
- 异常：无。C2 smoke 将用 **rerank + irrelevant scores**（与 M1 同一 strategy），不是额外 prompt 长度；prompt 策略已实现并有 unit 覆盖。
- 结论：接线正确，可进入 M1/C1/C2 smoke。不推进 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 跑 WebQSP n=20 的 M1、C1、C2 smoke

### LOG-012 — 2026-08-19 — M1 smoke n=20

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / M1 / smoke
- 代码：同 LOG-011
- 配置：WebQSP `--start 0 --limit 20`，`gpt-3.5-turbo-0125`，temp 0.3/0.3，depth 4，约束 off，reference/relation/decomp none；`--kg_memory_mode relation --kg_memory_strategy rerank --kg_memory_ablation none`；hash `ee55ef9f175d45e5`；semantic 0.7 / structure 0.3
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_n20_20260819_001548/`；`results.jsonl` / `pog_trace.jsonl` / `run_meta.json`；`check_kg_memory_m1.py --run_dir` PASS（42 events, 219 hits, 34 order_changed；无新增/删除 relation）
- 结果：EM 0.85 / F1 0.7407 / calls 8.65 / 13.53s / tokens 5458。对照 B0 smoke（LOG-003）EM 0.90 / F1 0.7845。多错 1 题：`what is there to do for fun in kansas city?`。不作有效性结论。
- 异常：无泄漏、无 hard filter
- 结论：接线生效（有命中且有排序变化）。继续 C1/C2 smoke。不进入 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 跑 C1 smoke

### LOG-013 — 2026-08-19 — C1 smoke n=20（shuffle）

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / C1 / smoke
- 代码：同 LOG-011
- 配置：同 LOG-012，仅 `--kg_memory_ablation shuffle`，seed=42
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_shuffle_n20_20260819_002052/`；checker PASS（39 events, 191 hits, 32 order_changed）
- 结果：EM 0.90 / F1 0.7915 / calls 8.10 / 13.73s / tokens 5353。错题与 B0 相同（Chagall / Clinton）。不作结论。
- 异常：无
- 结论：shuffle 路径可跑。继续 C2 smoke。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 跑 C2 smoke

### LOG-014 — 2026-08-19 — C2 smoke n=20（irrelevant scores）+ smoke 对照收口

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / C2 / smoke；四组对照收口
- 代码：同 LOG-011
- 配置：同 LOG-012，仅 `--kg_memory_ablation irrelevant`（rerank 上错配结构分，不是额外 prompt）
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_irrelevant_n20_20260819_002552/`；checker PASS（39 events, 201 hits, 34 order_changed）
- 结果：EM 0.90 / F1 0.7885 / calls 8.15 / 13.18s / tokens 5195。错题与 B0 相同。Smoke 四组：B0 0.90/0.7845，M1 0.85/0.7407，C1 0.90/0.7915，C2 0.90/0.7885。n=20 不得用于门槛。
- 异常：无 hard filter、无新增 relation。未跑 gold-next-relation recall（pilot 再算）。
- 结论：Phase 3 接线验收可通过 smoke 证据勾选。进入 Phase 2 的效果门槛未判定。下一步是 100–200 题 pilot，不是 path template。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 跑 WebQSP 100–200 题 B0/M1/C1/C2 pilot

### LOG-015 — 2026-08-19 — 启动 M1 pilot n=150

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / B0+M1+C1+C2 / pilot
- 代码：同 LOG-011；新增 `run_PoG_kg_memory_pilot.sh`
- 配置：WebQSP `--start 0 --limit 150`，其余与 Phase 0 冻结表一致；记忆 `schema_full_ee55ef9f17_20260818_114056`；M1 rerank / C1 shuffle / C2 irrelevant；semantic 0.7 / structure 0.3
- 产物：顺序跑 B0 → M1 → C1 → C2（本条记录启动时尚未出 run_dir）
- 结果：未完成
- 异常：无
- 结论：按 §18 进入 pilot，不根据 n=20 做 GATE_HOLD。完成后才能判定是否进入 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 等待 n=150 四组跑完并对照

### LOG-016 — 2026-08-19 — pilot 启动失败后立即重跑（GROUPS 环境变量冲突）

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`incident`
- 对应方案：Phase 3 / pilot
- 代码：`run_PoG_kg_memory_pilot.sh` 把循环变量写成 `GROUPS`，被环境里的 `GROUPS=10002` 覆盖，GROUP 变成非法值，立刻 exit 1。已改为 `PILOT_GROUPS`。
- 配置：仍为 start=0 limit=150
- 产物：无成功 run_dir
- 结果：未跑题
- 异常：启动失败，未改记忆或阈值
- 结论：修复后已重跑。B0 目录 `PoG/result/webqsp_gpt-3.5-turbo-0125_n150_20260819_003407/`，随后自动接 M1/C1/C2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 等待 n=150 四组跑完并对照

### LOG-017 — 2026-08-19 — pilot 改为并行（保留 B0，另启 M1/C1/C2）

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`incident`
- 对应方案：Phase 3 / pilot
- 代码：杀掉串行 `run_PoG_kg_memory_pilot.sh`，保留已在跑的 B0 python；`run_PoG_kg_memory_pilot.sh` 增加 `PARALLEL=1`
- 配置：同 n=150；B0 续跑 GPU3；M1 GPU4、C1 GPU7、C2 GPU4。共用 `localhost:8890` SPARQL 与同一 API
- 产物：B0 `webqsp_gpt-3.5-turbo-0125_n150_20260819_003407/`（切换时约 4 题已完成，`--run_dir` 可续）
- 结果：未完成
- 异常：四路并发可能拉高 SPARQL 延迟；若超时再降并发
- 结论：实验组独立，可以并行。不改阈值、不进 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 等待并行四组完成

### LOG-018 — 2026-08-19 — 写入 C1/C2 效率护栏

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`decision`
- 对应方案：Phase 3 / C1+C2 / pilot 进行中
- 代码：未改推理代码；只改本日志使用约定与对照效率护栏
- 配置：并行 n=150 仍在跑
- 产物：本日志「使用约定」第 6 条、§16.3 后「对照效率护栏」
- 结果：写入规则时观察（约 01:00）：B0 35/150、M1 35/150、C1 27/150、C2 21/150；C2 曾单题十余分钟无新结果。尚未结案，继续监视。
- 异常：若 C1/C2 相对 B0/M1 格外慢，必须诊断（SPARQL 争用 / ablation 过重 / 卡死题）并纠正，不能直接拿来做门槛。
- 结论：效率异常是对照失效信号，不是“shuffle 本来就慢”。未纠正前不据此 GATE_HOLD 或进 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 并行跑完；C1/C2 若持续掉队则先诊断

### LOG-019 — 2026-08-19 — C1 pilot n=150 完成（B0/M1 亦已完成）

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / C1（及已完成的 B0、M1）/ pilot
- 代码：同 LOG-011
- 配置：WebQSP start=0 limit=150；C1 ablation=shuffle seed=42 strategy=rerank
- 产物：C1 `PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_shuffle_n150_20260819_003911/`；B0 `..._n150_20260819_003407/`；M1 `..._rerank_top6_n150_20260819_003911/`
- 结果：C1 EM 0.8267 F1 0.7212 calls 9.99 24.9s tokens 6417。B0 0.8467 / 0.7401 / 10.34 / 27.3s。M1 0.8333 / 0.726 / 10.30 / 25.4s。C1 秒/题不慢于 B0/M1，本条不触发效率护栏。C2 仍约 147/150。
- 异常：无。四组 EM 未齐，禁止门槛判定。
- 结论：C1 跑通。等 C2 结束后再对照 gold-next-relation recall 并判定是否 GATE_HOLD。不进 Phase 2。
- 判定后状态：`3-M1` / `CONTINUE_PHASE_3` / 等待 C2 n=150 收尾

### LOG-020 — 2026-08-19 — M1 与 C2 pilot n=150 完成；GATE_HOLD

- 判定前状态：`3-M1` / `CONTINUE_PHASE_3`
- 类型：`run` + `decision`
- 对应方案：Phase 3 / M1+C2 / pilot n=150；§18 进入 Phase 2 门槛
- 代码：同 LOG-011；checker 与 `analyze_kg_memory_run.py` 离线分析
- 配置：冻结表；hash `ee55ef9f175d45e5`；M1 rerank none；C2 rerank irrelevant
- 产物：M1 `..._rerank_top6_n150_20260819_003911/`；C2 `..._irrelevant_n150_20260819_003911/`；各组 `kg_memory_analysis.json`；M1/C1/C2 checker PASS（无新增/删除 relation）
- 结果（n=150）：

| 组 | EM | F1 | calls | 秒/题 | tokens | gold_sel | gold_cand |
|---|---|---|---|---|---|---|---|
| B0 | 0.8467 | 0.7401 | 10.34 | 27.25 | 7074 | 0.7905 | 0.9865 |
| M1 | 0.8333 | 0.7260 | 10.30 | 25.43 | 6617 | 0.7838 | 0.9865 |
| C1 | 0.8267 | 0.7212 | 9.99 | 24.90 | 6417 | 0.8041 | 0.9865 |
| C2 | 0.8200 | 0.7079 | 10.24 | 25.56 | 6818 | 0.8041 | 0.9865 |

M1 干预真实发生（depth1：879 hits / 127 次改序）。C1/C2 平均秒/题与 M1 同量级，效率护栏不触发。
- 异常：M1 相对 B0 EM −1.34pp、gold_sel −0.67pp；gold_sel 低于 C1/C2。不构成 C1/C2 异常变慢。
- 结论：接线成立，但 **M1 未优于 C1/C2，且 gold-next-relation selected recall 未提升**。按 §18 记 `GATE_HOLD`：只诊断，禁止 Phase 2 / path template / Self-Play。不升为 `ROLLBACK`（尚未证明必须拆掉注入，但不得扩大）。
- 判定后状态：`3-M1` / `GATE_HOLD` / 诊断 M1 gold_sel 低于 shuffle/irrelevant 的原因

### LOG-021 — 2026-08-19 — GATE_HOLD 诊断：struct=0 gold 被高频边压过

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`eval` + `decision`
- 对应方案：Phase 3 / M1 / §18 GATE_HOLD 只诊断
- 代码：`PoG/diagnose_m1_gate_hold.py`（离线，不改推理）
- 配置：对照 LOG-020 四组 n=150 run_dir；冻结 schema `ee55ef9f175d45e5`；additive 0.7/0.3
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_n150_20260819_003911/kg_memory_gate_hold_diagnosis.json`
- 结果：
  - 成对 EM：B0 对 M1 错 7，B0 错 M1 对 5（净 −2）
  - depth-1 gold 被 LLM 选中：B0 117 / M1 116 / C1 119 / C2 119（retrieved 中有 gold 的 141 题）
  - 150 次 gold-in-list 事件：demote 29、promote 45；demote 后未选中 7
  - **gold 无 validated schema_profile（struct=0）：115/150（77%）**；29 次 demote **全部** gold_struct=0
  - 14/29 demote 的新 rank-1 有 struct>0（如 `location.location.containedby` coverage=1.0 压过 `travel.travel_destination.tourist_attractions`）
  - 被选中时 gold 平均 struct 0.19，未选中时 0.26：高频 gold 仍会输给更典型的 KG 边
- 异常：无 SPARQL 增边。机制是 additive `0.7*sem+0.3*struct` 让高覆盖竞争边在 gold 无 memory hit 时系统性地前移。C1/C2 打乱/错配结构分，不会稳定地把最典型 KG 边排到最前，故 gold_sel 反而更高。
- 结论：记忆覆盖不足 + 融合公式把「KG 常见」当成「问题需要」。**保持 `GATE_HOLD`**，禁止 Phase 2。解法仍留在 M1：`gated` 融合（additive 后禁止 struct=0 且语义更高的边被更低语义的 hit 压过）。默认 additive 不变，LOG-020 可复现。未升 `ROLLBACK`。
- 判定后状态：`3-M1` / `GATE_HOLD` / 实现 gated fusion 并做 n=20 smoke

### LOG-022 — 2026-08-19 — M1 gated fusion 代码（默认 additive 不变）

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`code`
- 对应方案：Phase 3 / M1 / GATE_HOLD 修复切片，不是 Phase 2
- 代码：`kg_memory_retrieval.py` 增加 `--kg_memory_fusion {additive,multiplicative,gated}`。`gated`：仍用 additive 打分，再做 miss 保护（struct=0 且 semantic 更高的候选不得排在更低 semantic 的 memory hit 之后）。`multiplicative` 已实现但本轮不跑。CLI / run_meta / output tag / checker 已接线。无 hard filter，无 path template。
- 配置：默认 fusion=additive。gated smoke 使用同一冻结表、同一记忆库、validated_only=1、min_confidence=0.6、0.7/0.3
- 产物：`python check_kg_memory_m1.py` ALL CHECKS PASSED（含 `additive_can_demote_unhit_gold` 与 `gated_protects_higher_semantic_miss`）
- 结果：无 LLM 跑数
- 异常：无
- 结论：接线正确。下一步 gated n=20 smoke（M1/C1/C2），B0 复用已有 n=20。不得根据 smoke 进 Phase 2。
- 判定后状态：`3-M1` / `GATE_HOLD` / 跑 gated fusion smoke n=20

### LOG-023 — 2026-08-19 — 启动 gated fusion smoke n=20

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`run`
- 对应方案：Phase 3 / M1 gated / smoke
- 代码：同 LOG-022；`KG_MEMORY_FUSION=gated`
- 配置：WebQSP start=0 limit=20；M1/C1/C2 顺序跑；GPU4；B0 不重跑，复用 `webqsp_gpt-3.5-turbo-0125_n20_20260818_110222`
- 产物：M1 已开 `PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_gated_n20_20260819_015406/`（本条记录时仍在跑）
- 结果：未完成
- 异常：无。GPU 2–7 有其他用户进程，本轮单卡 GPU4 顺序跑以降低争用。
- 结论：按 GATE_HOLD 修复切片开跑。完成后做 checker，不得据此进 Phase 2。
- 判定后状态：`3-M1` / `GATE_HOLD` / 等待 gated n=20 三组完成

### LOG-024 — 2026-08-19 — gated fusion smoke n=20 完成

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`run`
- 对应方案：Phase 3 / M1 gated / smoke
- 代码：同 LOG-022
- 配置：WebQSP start=0 limit=20；fusion=gated；M1 ablation=none / C1 shuffle / C2 irrelevant；GPU4 顺序；B0 复用 `..._n20_20260818_110222`
- 产物：
  - M1 `..._rerank_top6_gated_n20_20260819_015406/`
  - C1 `..._shuffle_gated_n20_20260819_015848/`
  - C2 `..._irrelevant_gated_n20_20260819_020307/`
  - checker 三组 PASS（M1：42 events / 205 hits / 29 order_changed；无增删 relation）
- 结果：

| 组 | EM | F1 | calls | 秒/题 | gold_sel | gold_cand |
|---|---|---|---|---|---|---|
| B0（旧 n=20） | 0.90 | 0.7845 | 8.95 | 29.33 | 0.80 | 1.0 |
| M1 additive（旧 n=20） | 0.85 | 0.7407 | 8.65 | 13.53 | — | — |
| M1 gated | 0.85 | 0.7442 | 8.45 | 13.68 | 0.80 | 1.0 |
| C1 gated | 0.90 | 0.7803 | 7.60 | 12.57 | 0.75 | 1.0 |
| C2 gated | 0.90 | 0.7656 | 7.65 | 43.08 | 0.80 | 1.0 |

M1 gated 错题：Chagall / Clinton college / Romney university。B0/C1/C2 只错前两题。M1 干预真实发生（depth1 hits 132 / reorder 14）。
- 异常：C2 平均 43s 由单题 `what ocean is around hawaii?` 的 **550.8s / 8 calls** 拉高；去掉该题后 16.4s，与 M1 同量级。additive C2 同题 11.4s / 8 calls。LLM 选了 `location.location.contains`（Hawaii 高扇出），耗时是 SPARQL/展开波动，不是 gated 打分过重。护栏已调查，不因此作废 C2，但后续 pilot 须盯单题卡死。
- 结论：gated 接线成立，无 hard filter。n=20 上 M1 仍低于 B0/C1/C2，**不得据此进 Phase 2，也不得用 n=20 宣称 gated 已过门槛**。下一步若继续修复切片，才允许 gated n=150 对照同一 B0/C1/C2。
- 判定后状态：`3-M1` / `GATE_HOLD` / 可选：gated fusion n=150 pilot（仍禁止 Phase 2）

### LOG-025 — 2026-08-19 — 启动 gated fusion pilot n=150

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`run`
- 对应方案：Phase 3 / M1 gated vs C1/C2 / pilot；§18 进入 Phase 2 门槛
- 代码：同 LOG-022；`KG_MEMORY_FUSION=gated`
- 配置：WebQSP start=0 limit=150；M1/C1/C2 并行；GPU_MAP `M1:6 C1:7 C2:7`；B0 不重跑，复用 `webqsp_gpt-3.5-turbo-0125_n150_20260819_003407/`。冻结表与记忆库不变。
- 产物：M1 `..._rerank_top6_gated_n150_20260819_022256/`；C1 `..._shuffle_gated_n150_20260819_022256/`；C2 `..._irrelevant_gated_n150_20260819_022256/`
- 结果：未完成
- 异常：无。C2 须盯单题 SPARQL；若相对 M1 格外慢或长时间无新 `results.jsonl`，先诊断再做门槛。
- 结论：按 GATE_HOLD 修复切片进入 n=150。不得提前实现 Phase 2。
- 判定后状态：`3-M1` / `GATE_HOLD` / 等待 gated n=150 完成并对照

### LOG-026 — 2026-08-19 — gated M1 n=150 过 Phase 2 门槛

- 判定前状态：`3-M1` / `GATE_HOLD`
- 类型：`run` + `decision`
- 对应方案：Phase 3 / M1 gated + C1/C2 / pilot n=150；§18 进入 Phase 2 门槛
- 代码：同 LOG-022；`--kg_memory_fusion gated`
- 配置：WebQSP start=0 limit=150；冻结表；记忆 `schema_full_ee55ef9f17_20260818_114056` hash `ee55ef9f175d45e5`；B0 复用 `..._n150_20260819_003407/`
- 产物：M1 `..._rerank_top6_gated_n150_20260819_022256/`；C1 `..._shuffle_gated_n150_20260819_022256/`；C2 `..._irrelevant_gated_n150_20260819_022256/`；checker 三组 PASS（M1：508 events / 1613 hits / 371 order_changed；无增删 relation）
- 结果：

| 组 | EM | F1 | calls | 秒/题 | tokens | gold_sel | gold_cand |
|---|---|---|---|---|---|---|---|
| B0 | 0.8467 | 0.7401 | 10.34 | 27.25 | 7074 | 0.7905 | 0.9865 |
| M1 additive | 0.8333 | 0.7260 | 10.30 | 25.43 | 6617 | 0.7838 | 0.9865 |
| **M1 gated** | **0.8667** | **0.7562** | 10.32 | 22.87 | 6766 | **0.8041** | 0.9865 |
| C1 gated | 0.8267 | 0.7209 | 9.91 | 23.94 | 6716 | 0.8108 | 0.9865 |
| C2 gated | 0.8267 | 0.7208 | 10.36 | 22.89 | 6714 | 0.8108 | 0.9865 |

成对 EM vs B0：B0 错 / gated 对 6，B0 对 / gated 错 3（净 +3）。depth1 hits 879 / reorder 91。M1 EM 相对 C1/C2 **+4.00pp**，相对 B0 **+2.00pp**；gold_sel 相对 B0 **+1.36pp**，相对 C1 −0.67pp（约 1/148 题），不算显著 recall 损失。candidate recall 四组相同。
- 异常：并行 stdout 中有一次 `APITimeoutError` 后又完成评测，三组均为 150/150。C1/C2 平均秒/题与 M1 同量级（22.9–23.9s），效率护栏不触发。C1 单题最长 618s（Franklin Pierce），未拖垮对照。
- 结论：**M1 gated 优于 C1/C2，且相对 B0 无 gold-next-relation recall 损失。** 按 §18 记 `ACCEPT_PHASE_3_M1_AND_ADVANCE`，允许进入 Phase 2。冻结 M1 融合为 gated。下一步是 Protocol 2 path template **构建**，不是立刻跑 M2、也不是 Self-Play。
- 判定后状态：`2-path-template` / `ACCEPT_PHASE_3_M1_AND_ADVANCE` / 实现 Protocol 2 smoke 构建

### LOG-027 — 2026-08-19 — 取消 C1/C2 效率护栏（前瞻要求）

- 判定前状态：`2-path-template` / `ACCEPT_PHASE_3_M1_AND_ADVANCE`
- 类型：`decision`
- 对应方案：日志使用约定；不改方案 §16.3 回退条件本身
- 代码：未改推理代码；删除本日志「使用约定」原第 6 条及 §16.3 后「对照效率护栏」小节。历史 LOG-018/019/020/026 不改写
- 配置：不变
- 产物：本日志
- 结果：无新跑数。后续 C1/C2 相对 B0/M1 变慢**不再**作为独立门槛或阻断 Phase 推进的护栏
- 异常：无
- 结论：效果门槛仍按 §18（M1 优于 C1/C2、无显著 gold-next-relation recall 损失）。下一步仍是 Protocol 2 path template smoke 构建，不是 M2 推理或 Self-Play
- 判定后状态：`2-path-template` / `CONTINUE_PHASE_2` / 跑 Protocol 2 smoke 构建 + checker

### LOG-028 — 2026-08-19 — Protocol 2 path template smoke 构建 + checker PASS

- 判定前状态：`2-path-template` / `CONTINUE_PHASE_2`
- 类型：`code` + `build`
- 对应方案：Phase 2 / Protocol 2 / smoke
- 代码：新增 `PoG/build_path_templates.py`；`kg_structural_memory.py` 增加 `path_template` / `path_probe`；`kg_probe.py` 增加 `two_hop_exists`；`check_kg_memory_build.py` 支持 2-hop witness 复放与负事实检查。未改 PoG 推理、未注入 M2。脏工作树，未提交
- 配置：`--mode smoke`；3 type（`music.release_track` / `music.recording` / `music.single`）；disc 8 / val 6；max_seed_rels 3；max_r2 4；neighbor 4；max_templates 8；min_support 2；min_coverage 0.2；1-hop 从冻结 Protocol 1 validated outgoing schema_profile 转写；2-hop SPARQL 探测。schema hash `ee55ef9f175d45e5`。SPARQL `http://localhost:8890/sparql`。无 LLM
- 产物：`PoG/kg_memory/path_smoke_a1a76fe6c4_20260819_094058/`（hash `a1a76fe6c4dee289`）；jsonl / manifest / index / discovery+validation splits / probe_cache
- 结果：33 records，validated 29，unknown_or_low_support 4，low_support 0；1-hop 9 / 2-hop 24。SPARQL 437 queries，cache_hits 5，failures 0。checker `--replay 20` **PASS**（20/20 witness）。冻结 schema 库 `--replay 0` 仍 PASS。无 gold 字段、无“不存在”负事实
- 异常：`music.single` 的 seed relation 与 `music.recording` 相同（来自 Protocol 1 该 type 的高覆盖 outgoing profile），不是串 type。smoke 只覆盖 3 个 music type，不能当作 full 验收
- 结论：Protocol 2 接线与 provenance 在 smoke 规模成立。**未勾完 Phase 2**，不得跑 M2。下一步 full（约 40 type）
- 判定后状态：`2-path-template` / `CONTINUE_PHASE_2` / 跑 Protocol 2 full 构建 + checker

### LOG-029 — 2026-08-19 — Protocol 2 path template full 构建 + checker PASS

- 判定前状态：`2-path-template` / `CONTINUE_PHASE_2`
- 类型：`build`
- 对应方案：Phase 2 / Protocol 2 / full
- 代码：沿用 LOG-028 构建器，未改推理、未注入 M2
- 配置：`--mode full`；40 type；disc 30 / val 20；max_seed_rels 8；max_r2 8；neighbor 8；max_templates 20；min_support 3；min_coverage 0.2。schema `schema_full_ee55ef9f17_20260818_114056` hash `ee55ef9f175d45e5`。SPARQL `http://localhost:8890/sparql`。无 LLM
- 产物：`PoG/kg_memory/path_full_ae5435360a_20260819_094201/`（hash `ae5435360ae3d7fd`）；jsonl / manifest / index / splits / probe_cache
- 结果：843 records，validated 747，unknown_or_low_support 96，low_support 0；1-hop 179 / 2-hop 664；40 types。SPARQL 28370 queries，cache_hits 2509，failures 0。约 5.4 min。checker `--replay 20` **PASS**（20/20）。无 gold 字段、无负事实。schema-hash 往返一致
- 异常：部分 type 的 seed 继承自相关 type 的高覆盖 relation（如 `music.single`→`music.recording.*`，`film.actor`→`people.person.*`），与 Protocol 1 冻结库一致，不是串库。`business.employer` 等组织类 2-hop 低支持较多（23 条中 15 条 unknown_or_low_support）
- 结论：Phase 2 验收勾完。冻结该 path 库。按判定算法 `ACCEPT_PHASE_2_AND_ADVANCE`。下一步实现 M2 注入，不是立刻 n=150、也不是 Self-Play
- 判定后状态：`3-M2` / `ACCEPT_PHASE_2_AND_ADVANCE` / 实现 M2 path_template → relation 注入（先 unit）

### LOG-030 — 2026-08-19 — M2 path_template 注入接线 + unit PASS

- 判定前状态：`3-M2` / `ACCEPT_PHASE_2_AND_ADVANCE`
- 类型：`code`
- 对应方案：Phase 3 / M2 / unit
- 代码：`kg_memory_retrieval.compact_stat` 接纳 `path_template`；同一 first-hop 优先 2-hop 统计；run tag 追加 `_path`；`run_PoG_kg_memory_test.sh` 增加 GROUP=M2/M2C1/M2C2；checker 增加 path 用例。未改 SPARQL 候选集、无 hard filter。脏工作树，未提交
- 配置：冻结 path `path_full_ae5435360a_20260819_094201` hash `ae5435360ae3d7fd`。融合默认仍 additive（M1 历史）；M2 smoke 将显式 `gated`
- 产物：无新 run_dir
- 结果：`python check_kg_memory_m1.py` ALL CHECKS PASSED（含 path_two_hop_preferred / path_no_extra_or_drop / frozen_path_bank）。schema 冻结库检查仍 PASS
- 异常：无
- 结论：M2 接线正确，可进入 n=20 smoke。不得用 unit 宣称 M2 有效，不得进 n=150 / M3 / Self-Play
- 判定后状态：`3-M2` / `CONTINUE_PHASE_3` / 跑 M2/M2C1/M2C2 n=20 gated smoke

### LOG-031 — 2026-08-19 — M2 gated n=20 smoke 完成

- 判定前状态：`3-M2` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / M2 + M2C1 + M2C2 / smoke
- 代码：同 LOG-030
- 配置：WebQSP start=0 limit=20；fusion=gated；path `path_full_ae5435360a_20260819_094201` hash `ae5435360ae3`；M2 ablation=none / M2C1 shuffle / M2C2 irrelevant；并行 GPU 4/5/6。B0 不重跑，复用 `webqsp_gpt-3.5-turbo-0125_n20_20260818_110222`。冻结 inference 表不变
- 产物：
  - M2 `PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_gated_path_n20_20260819_104006/`
  - M2C1 `..._shuffle_gated_path_n20_20260819_103845/`
  - M2C2 `..._irrelevant_gated_path_n20_20260819_103845/`
  - checker 三组 PASS（M2：40 events / 58 hits / 15 order_changed；无增删 relation）。加载 179 first-hop keys（175 validated）
- 结果：

| 组 | EM | F1 | calls | 秒/题 | tokens | gold_sel | gold_cand |
|---|---|---|---|---|---|---|---|
| B0（旧 n=20） | 0.90 | 0.7845 | 8.95 | 29.33 | 5891 | 0.80 | 1.0 |
| M1 gated（旧 n=20） | 0.85 | 0.7442 | 8.45 | 13.68 | — | 0.80 | 1.0 |
| **M2 gated** | **0.90** | 0.7730 | 8.45 | 26.73 | 5401 | 0.80 | 1.0 |
| M2C1 shuffle | 0.80 | 0.6936 | 6.90 | 11.57 | 4644 | 0.85 | 1.0 |
| M2C2 irrelevant | 0.90 | 0.7732 | 8.15 | 12.94 | 5209 | 0.85 | 1.0 |

M2 命中行 58：2-hop 50 / 1-hop 8。depth1 analyze：hits 32 / reorder 5。
- 异常：M2 平均 26.7s 接近 B0 smoke，慢于对照组；n=20 波动，不据此作废对照。M2C1 EM 低 0.80 同样不得当结论
- 结论：path_template 注入真实发生，无 hard filter。**不得用 n=20 宣称 M2 过门槛或优于 M1。** 下一步 n=150 gated pilot
- 判定后状态：`3-M2` / `CONTINUE_PHASE_3` / 跑 M2/M2C1/M2C2 n=150 gated pilot

### LOG-032 — 2026-08-19 — 启动 M2 gated pilot n=150

- 判定前状态：`3-M2` / `CONTINUE_PHASE_3`
- 类型：`run`
- 对应方案：Phase 3 / M2 vs M2C1/M2C2 / pilot n=150
- 代码：同 LOG-030
- 配置：WebQSP start=0 limit=150；fusion=gated；path `path_full_ae5435360a_20260819_094201` hash `ae5435360ae3`；M2/M2C1/M2C2 并行 GPU_MAP `M2:5 M2C1:6 M2C2:7`。B0 不重跑，复用 `webqsp_gpt-3.5-turbo-0125_n150_20260819_003407/`；gated M1 对照 `..._rerank_top6_gated_n150_20260819_022256/`。冻结表与记忆库不变
- 产物：本条记录时三组已启动，run_dir 待完成后填写
- 结果：未完成
- 异常：无
- 结论：按 LOG-031 进入 n=150。不得提前宣称 M2 过门槛，不得做 M3 / Self-Play
- 判定后状态：`3-M2` / `CONTINUE_PHASE_3` / 等待 n=150 完成并 checker

### LOG-033 — 2026-08-19 — M2 gated n=150 未过对照门槛（GATE_HOLD）

- 判定前状态：`3-M2` / `CONTINUE_PHASE_3`
- 类型：`eval`
- 对应方案：Phase 3 / M2 + M2C1 + M2C2 / pilot n=150
- 代码：同 LOG-030
- 配置：WebQSP start=0 limit=150；fusion=gated；path `path_full_ae5435360a_20260819_094201` hash `ae5435360ae3`；并行 GPU 5/6/7。B0 复用 `..._n150_20260819_003407/`；M1 对照 `..._rerank_top6_gated_n150_20260819_022256/`
- 产物：同 stamp `..._n150_20260819_110904/`：M2 `..._gated_path_...`；M2C1 `..._shuffle_gated_path_...`；M2C2 `..._irrelevant_gated_path_...`。checker 三组 PASS（M2：378 events / 476 hits / 197 order_changed；无增删 relation）
- 结果：

| 组 | EM | F1 | calls | 秒/题 | tokens | gold_sel | gold_cand |
|---|---|---|---|---|---|---|---|
| B0 | 0.8467 | 0.7401 | 10.34 | 27.25 | 7074 | 0.7905 | 0.9865 |
| M1 gated | **0.8667** | **0.7562** | 10.32 | 22.87 | 6766 | 0.8041 | 0.9865 |
| **M2 gated** | 0.8467 | 0.7366 | 9.29 | 22.32 | 5907 | 0.8041 | 0.9865 |
| M2C1 shuffle | 0.8533 | 0.7463 | 10.68 | 24.84 | 6917 | 0.8041 | 0.9865 |
| M2C2 irrelevant | 0.8533 | 0.7400 | 9.01 | 21.67 | 5709 | 0.8041 | 0.9865 |

成对 EM vs B0：+4 / −4（净 0）。vs M1：+5 / −8。M2 命中行 476：2-hop 391 / 1-hop 85。depth1 analyze hits 257 / reorder 47。gold_sel 相对 B0 **+1.36pp**，与 M1 及 M2C1/M2C2 **相同**。
- 异常：M2 EM 低于 path 对照 1 题（23 vs 22 错）。效率相对 B0 更好（calls −1.05、秒/题 −4.93、tokens −1167），不构成效率恶化。未升 `ROLLBACK`（M1 仍成立，且无 recall 损失）
- 结论：**M2 未优于 M2C1/M2C2**，相对 B0 EM 持平。按 §18 / H4 记 `GATE_HOLD`：只诊断，禁止 M3 / reflection / Self-Play。M1 gated 门槛保持
- 判定后状态：`3-M2` / `GATE_HOLD` / 诊断 M2 未优于对照的原因

### LOG-034 — 2026-08-19 — M2 GATE_HOLD 诊断：金第一跳几乎无 path 命中

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / M2 / GATE_HOLD 诊断，不是 M3
- 代码：未改推理
- 配置：对照 LOG-033 三组 + 同一 B0/M1；gold 来自 `data/WebQSP.json` 的 InferentialChain 第一跳
- 产物：无新 run_dir；离线统计基于 M2 `..._gated_path_n150_20260819_110904/pog_trace.jsonl`
- 结果：depth1 prune 中 gold 第一跳出现 170 次：**struct=0 占 87.1%（148/170）**；有记忆命中仅 22 次且全是 2-hop。Gold 结构分均值 0.12，竞争边命中均值 0.87。Gold 在列表中的位次 6.54→4.44（gated 有前移），但 **gold_sel 与 M2C1/M2C2 同为 0.8041**。M2 相对 C1 独错 5 题、C1 独错 4 题；相对 C2 各 5/4，净差 1 题
- 异常：path 库只有 40 type / 179 first-hop key，多数 WebQSP gold 第一跳不在库中。把 2-hop coverage 压到 first-hop 上，等于给「常见后继」的边打高分，打乱这些分（C1）并不改变 gold 是否被选中
- 结论：**覆盖不足 + first-hop 折叠使 M2 无法相对对照归因。** 保持 `GATE_HOLD`，禁止 M3。未升 `ROLLBACK`（无 recall 损失，M1 仍有效）。若修，应留在 M2：提高 gold 第一跳覆盖，或让 2-hop 服务后续跳/reflection，而不是再加一层 first-hop 频率
- 判定后状态：`3-M2` / `GATE_HOLD` / 可选 M2 诊断切片修复；禁止 M3 / Self-Play

### LOG-035 — 2026-08-19 — M2 根因定位：覆盖退化 + first-hop 折叠 + 打分无 path 项

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / M2 / GATE_HOLD 诊断，不是 M3
- 代码：未改任何代码；纯离线统计两个冻结库与 n=150 trace
- 配置：`path_full_ae5435360a_20260819_094201` vs `schema_full_ee55ef9f17_20260818_114056`；检索参数同 LOG-033（min_confidence 0.6、validated_only 1）
- 产物：无新文件
- 结果（三条根因，均有量化证据）：

**R1 覆盖退化：M2 的键空间是 M1 的真子集。**

| 库 | first-hop 键 | validated | min_conf 0.6 后可用 | type | 方向 |
|---|---|---|---|---|---|
| M1 schema | 3529 | 1477 | 687 | 150 | incoming 1570 / outgoing 1959 |
| M2 path | 179 | 175 | 105 | 40 | **outgoing 179 / incoming 0** |

本切片 gold 第一跳共 80 个不同 relation：schema 库覆盖 49，path 库只覆盖 11，**path-only = 0**。prune 事件命中率 M1 66% → M2 48%，有索引类型 80% → 63%。

**R2 first-hop 折叠丢掉 79% 记忆。** 843 条模板按 `(type,direction,first_hop)` 折叠成 179 个键，丢弃 664 条；单个第一跳最多带 19 条模板。保留下来的那条是「confidence 最高的后继」，等于比 M1 更偏的频率先验。

**R3 打分里没有任何 path 项。** `structural_score` 仍只用 coverage/confidence/support/branching/explored，与 M1 同一公式；`target_type`、`relation_path[1]`、`hop_length` 只出现在 prompt 行、trace 和 2-hop 择优 tiebreak 里，**rerank 分数完全没用到第二跳**。分数分布也与 M1 同量级（struct 均值 0.70 vs 0.63，sd 0.26）。

- 异常：无。R3 意味着方案 H1 里「2-hop 模板区分第一跳」的机制**从未真正进入打分路径**，M2 实际是「用更少的数据重跑 M1」，因此与 C1/C2 打平是预期结果而非偶然
- 结论：M2 失败不是「path template 方向不成立」，而是实现把 path 压成 first-hop 频率。**保持 `GATE_HOLD`**，禁止 M3（当前 M2 ⊂ M1，M3 等价 M1）。不升 `ROLLBACK`。修复须同时解决 R1/R2/R3，然后重走 n=20 → n=150
- 判定后状态：`3-M2` / `GATE_HOLD` / 等待选定 M2 修复切片；禁止 M3 / reflection / Self-Play

### LOG-036 — 2026-08-19 — M2 修复切片 v2：双向全 type 构建 + 不折叠 + 尾部语义打分（仅代码）

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`code`
- 对应方案：Phase 3 / M2 `GATE_HOLD` 修复切片，同时针对 LOG-035 的 R1 / R2 / R3。**不是 M3**，不动 M1 冻结配置
- 代码（4 个文件）：
  - `kg_probe.py`：`two_hop_exists` 增加 `first_outgoing`，支持第一跳为入边
  - `build_path_templates.py`：新增 `--directions {outgoing,both}`（full 默认 `both`，smoke 默认 `outgoing`）；full 默认 `n_types` 40 → **150**；seed / discovery / validation / `query_template_id` 全部按方向分别执行；`directions` 进入 config hash
  - `kg_memory_retrieval.py`：bank 新增 `variants`（一个 first-hop 键保留全部模板）与 `record_kinds`；新增 `lookup_variants` / `retrieve_relation_variants` / `tail_text` / `tail_semantic_norm` / `score_relation_variants`。**path_template 的结构分改为 `max_t( structural_score(t) × tail_sem(t) )`**，`tail_sem` 是问题与「第二跳 + target_type」的 min-max 归一相似度，下限 `TAIL_SEM_FLOOR=0.1`（保证「命中 ⟺ struct>0」，gated 逻辑不变）。C2（irrelevant）走同一套打分，构成「机制相同、类型错误」的对照
  - `check_kg_memory_build.py`：2-hop witness replay 支持 incoming 第一跳
- M1 保护：`infer_bank_memory_kind` 非 path 库时仍走原 `retrieve_relation_stats` 单条查找，公式与择优顺序不变；`by_type` 排序键保持 `(-confidence, relation)`。新增单测 `schema_scores_keep_m1_formula` 断言 M1 结构分等于原公式
- 结果：`check_kg_memory_m1.py` **13/13 PASS**（新增 `path_tail_semantics_discriminate`：两条统计完全相同、只有第二跳不同的第一跳，必须被问题拉开且都保持 struct>0；`path_no_extra_or_drop` 的断言从「hop_length=2」改为「n_variants=2」，因为不再折叠）。双向 smoke 构建 `path_smoke_342edfa79a_20260819_133957` 65 条 / validated 59，checker `replay_ok=20/20 PASS`
- 异常：首次 smoke checker 报 `replay_ok=11/20`，原因是 2-hop witness replay 把第一跳写死为出边；修 `check_kg_memory_build.py` 后 20/20
- 结论：修复切片代码就绪。下一步全量重建 `path_full_1f16016919_*`（150 type / 双向），checker 通过后才允许 n=20 冒烟
- 判定后状态：`3-M2` / `GATE_HOLD` / 全量重建进行中；仍禁止 M3 / reflection / Self-Play

### LOG-037 — 2026-08-19 — path 库 v2 全量重建：150 type / 双向 / 6479 条模板

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`build`
- 对应方案：Phase 2 重建，服务 LOG-036 的 M2 修复切片。schema 库不变，M1 冻结配置不变
- 代码：LOG-036 的四处改动，未再改
- 配置：`--mode full --directions both`，n_types 150、disc 30、val 20、max_seed_rels 8、max_r2 8、max_templates_per_type 20、min_support 3、min_coverage 0.2；`schema_build_config_hash=ee55ef9f17`
- 产物：`PoG/kg_memory/path_full_1f16016919_20260819_134058/`，`build_config_hash=1f1601691982c1a9`，耗时约 37 分钟，SPARQL 220913 次 / cache_hits 45871
- 结果：

| | v1（LOG-029） | **v2（本条）** | M1 schema |
|---|---|---|---|
| 模板条数 | 843 | **6479**（validated 5708） | 3529 |
| first-hop 键 | 179 | **1208** | 3529 |
| min_conf 0.6 后可用键 | 105 | **662** | 687 |
| type | 40 | **150** | 150 |
| 方向 | 出 179 / 进 0 | **出 724 / 进 484** | 出 1959 / 进 1570 |
| n=150 切片 gold 第一跳覆盖（共 80） | 11 | **28** | 49 |

checker `PASS`：hash 自洽、`replay_ok=20/20`、150 type 均有 discovery/validation、无负事实状态。`check_kg_memory_m1.py` 换到 v2 冻结库后仍 **13/13 PASS**

- 异常：gold 覆盖 28 仍低于 M1 的 49。原因是 path 模板只从每个 type 的 top-8 schema 关系起跳（`max_seed_rels=8`），长尾第一跳进不来。本轮不动该参数，先看 R2/R3 修复本身的效果
- 结论：R1 已显著缓解（可用键 105 → 662，与 M1 的 687 同量级）。冻结 v2 为 M2 库：`FROZEN_PATH_FULL_DIR` 与 `run_PoG_kg_memory_test.sh` 的 `PATH_MEMORY_PATH` 已切换。下一步 n=20 冒烟（不用于判定）
- 判定后状态：`3-M2` / `GATE_HOLD` / 允许 M2 修复切片 n=20 冒烟；禁止 M3 / reflection / Self-Play

### LOG-038 — 2026-08-19 — M2 v2 n=20 冒烟：机制确认生效（不用于判定）

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`smoke`
- 对应方案：Phase 3 / M2 修复切片验证。**n=20 不得用于任何门槛判定**
- 代码：LOG-036 冻结版本，未再改
- 配置：`GROUP=M2|M2C1|M2C2`、`KG_MEMORY_FUSION=gated`、`START=0 LIMIT=20`、库 `path_full_1f16016919_20260819_134058`，其余同 Phase 0 冻结表
- 产物：`result/..._gated_path_n20_20260819_142447/`（M2）、`..._shuffle_gated_path_n20_20260819_142943/`（M2C1）、`..._irrelevant_gated_path_n20_*`（M2C2）
- 结果：EM M2 **0.95 (19/20)** / M2C1 0.85 (17/20) / M2C2 0.90 (18/20)。机制侧（M2 trace）：

| 指标 | v1（LOG-031/033） | **v2** |
|---|---|---|
| prune 事件命中率 | 48%（n=150） | **90%**（39 事件 35 命中） |
| 每个命中第一跳可见模板数 | 1（折叠） | **均值 8.7，最大 70** |
| `tail_semantic` 分布 | 不存在 | **均值 0.769，跨满 0.10–1.00** |
| 被选中模板 hop 分布 | 几乎全 2-hop（强制偏好） | **2-hop 109 / 1-hop 66** |

`check_kg_memory_m1.py --run_dir` **PASS**：无增删关系、无硬过滤标记、LLM 候选集合与 `order_after` 一致

- 异常：无
- 结论：R2/R3 的修复在真实 trace 上确实生效（不再折叠、尾部语义在真正区分模板、hop 选择由问题决定而非硬偏好）。EM 方向也对，但 **n=20 不判定**。进入 n=150
- 判定后状态：`3-M2` / `GATE_HOLD` / n=150 pilot 待跑；禁止 M3 / reflection / Self-Play

### LOG-039 — 2026-08-19 — M2 v2 n=150 pilot：胜 C1、与 C2 打平，仍 `GATE_HOLD`

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / M2 修复切片效果门槛。**不是 M3**
- 代码：LOG-036 冻结版本
- 配置：`GROUP=M2|M2C1|M2C2`、`KG_MEMORY_FUSION=gated`、`START=0 LIMIT=150`、库 `path_full_1f16016919_20260819_134058`；B0/M1 用既有 n=150 结果
- 产物：`result/..._gated_path_n150_20260819_144102/`（M2）、`..._shuffle_gated_path_n150_20260819_154847/`（M2C1）、`..._irrelevant_gated_path_n150_20260819_171515/`（M2C2）
- 结果：

| 组 | EM | F1 | 错题 | calls | 秒/题 | tokens |
|---|---|---|---|---|---|---|
| B0 | 0.8467 | 0.7401 | 23 | 10.34 | 27.25 | 7074.4 |
| M1 (gated) | **0.8667** | 0.7562 | 20 | 10.32 | 22.87 | 6766.2 |
| M2 v1 | 0.8467 | 0.7366 | 23 | 9.29 | 22.32 | 5907.2 |
| **M2 v2** | **0.8600** | 0.7485 | 21 | **8.90** | 27.03 | **5665.5** |
| M2C1 v2 (shuffle) | 0.8467 | 0.7392 | 23 | 9.07 | 30.00 | 6057.1 |
| M2C2 v2 (irrelevant) | **0.8600** | 0.7485 | 21 | 11.49 | 32.99 | 7851.6 |

成对错题：M2v2 vs C1 修好 6 / 弄坏 4（净 **+2**）；vs C2 修好 6 / 弄坏 6（净 **0**，EM/F1 完全相同但错题集不同）；vs B0 净 +2；vs M1 净 −1；vs M2 v1 净 +2。

机制侧（depth1 gold 第一跳，共 185 行）：

| | M2 v1 | **M2 v2** | M1 |
|---|---|---|---|
| gold `struct=0` 比例 | 83.2% | **74.6%** | 74.1% |
| gold 结构分均值 | 0.157 | 0.186 | 0.230 |
| **竞争边命中结构分均值** | 0.870 | **0.582** | 0.870 |
| gold 平均位次 | 5.595 | 5.551 | 5.503 |

- 异常：C2 与 M2 的 EM/F1 完全相同（0.8600 / 0.7485），但 calls 11.49 vs 8.90、tokens 7851 vs 5665，路径明显不同、代价高 39%。n=150 下 ±2 题差异无统计显著性，不能据此宣称任一方向
- 结论：修复切片**部分见效**——M2 v1 → v2 EM +1.33pp，尾部语义把竞争边的结构分从 0.870 压到 0.582（这正是 R3 想要的效果），且已稳定优于 shuffle 对照 C1。但**未优于错误类型对照 C2**，按 §18 / H4 效果门槛仍记 `GATE_HOLD`。不升 `ROLLBACK`（gold_sel 无损失，M1 gated 仍成立，效率是全组最优）
- 剩余瓶颈：74.6% 的 gold 第一跳仍完全没有模板，与 M1 同一水平。原因是 path 模板只从每个 type 的 top-8 schema 关系起跳（`max_seed_rels=8`）；长尾第一跳进不了库，尾部语义再准也无从作用
- 判定后状态：`3-M2` / `GATE_HOLD` / 唯一允许的下一步见「下一步」；禁止 M3 / reflection / Self-Play

### LOG-040 — 2026-08-19 — 冻结校验切片 `hard150_v1`：双系统易错题，不再用 test 前 150

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`code`
- 对应方案：评测协议；不改变 M2 判定，不进入 M3
- 代码：`eval_slices.py`、`build_hard_eval_slice.py`；`main_freebase.py` 增加 `--questions_file`（先按文件选题，再应用 `--start/--limit`）；`output_paths.py` / `reference_utils.py` 把切片 id 写入 `run_meta` 与 run 目录名（`_slice-hard150-v1`）；`run_PoG_kg_memory_test.sh` / `run_PoG_kg_memory_pilot.sh` 默认 `QUESTIONS_FILE=eval_slices/hard150_v1.json`
- 配置：未改推理、fusion、memory 库
- 产物：
  - `PoG/eval_slices/hard150_v1.json`（`questions_sha256=09ad7d10f566b791…`）
  - `PoG/eval_slices/hard150_v1.questions.txt`
- 来源与抽题规则（可复现）：
  1. 原始 PoG：`PoG-main/PoG/PoG_webqsp_gpt-3.5-turbo-0125.jsonl`（1639 题，EM 0.8066，错 317）
  2. relation+decomposition 记忆：`result/webqsp_..._decompmem-prompt_top2_n1639_20260814_032036/`（EM 0.8414，错 260）
  3. 评测与本实验相同：`eval_run.evaluate_run_results` 的 exact match
  4. 取 **两份结果都错** 的 177 题，按 WebQSP test 下标排序后 **均匀抽 150**（`even_subsample`），**不是 test[:150]**
- 结果：切片 150 题；test 下标 min=6 / q1=413 / 中位=798 / q3=1258 / max=1638；与旧 prefix150 只重叠 **13** 题。`select_questions` 单测：LIMIT=20 得到切片前 20 条，LIMIT=150 得到全切片
- 异常：无。两系统都错 ⇒ 该切片上原始 PoG 与 rel+decomp 的 EM 均为 0；新 B0 预期远低于 prefix150 的 0.8467，这是切片变难，不是回归
- 结论：**此后 KG-memory 的校验与门槛判定必须使用 `hard150_v1`。** prefix150（LOG-003–039）数字冻结为档案，禁止混比。M1 gated 在 prefix150 上的过门槛不自动迁移。下一步：在 hard150 上重跑 B0 n=150，再做 M2 对照。禁止 M3 / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / 校验切片已冻结；等待 hard150 上的 B0

### LOG-041 — 2026-08-19 — 启动 hard150 n=150 五组并行（同卡 GPU3）

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / hard150 重基线 + M2 对照。**不是 M3**
- 代码：未改推理；`run_PoG_kg_memory_pilot.sh` 把 `KG_MEMORY_FUSION` 传给子进程，并行启动可 `STAGGER_SEC` 错开加载
- 配置：`QUESTIONS_FILE=eval_slices/hard150_v1.json`、`START=0 LIMIT=150`、`KG_MEMORY_FUSION=gated`、五组 `B0 M1 M2 M2C1 M2C2` 全部 `GPU_IDS=3`（物理 GPU3，空闲 24GB）。库与 fusion 同 LOG-039 冻结项
- 产物：启动中；run 目录名应含 `_slice-hard150-v1` 与 `_n150_`
- 结果：并行 5 路（多于 4）。B0 不走 KG memory 打分但仍加载同一 SentenceTransformer。预期该切片 EM 远低于 prefix150，不得与 LOG-026/039 混比
- 异常：无（启动记录）
- 结论：按 LOG-040 在 hard150 上重跑门槛对照。完成后才能判定 M2。禁止 M3 / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / hard150 n=150 五组进行中

### LOG-042 — 2026-08-20 — hard150 n=150 五组完成：M2 低于 B0 与 C1/C2，H4 未过

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / M2 vs B0/M1/M2C1/M2C2 / hard150 pilot。**不是 M3**
- 代码：未改
- 配置：同 LOG-041；切片 `hard150_v1`
- 产物：
  - B0 `..._slice-hard150-v1_n150_20260819_203151`
  - M1 `..._gated_slice-hard150-v1_n150_20260819_203158`
  - M2 `..._gated_path_slice-hard150-v1_n150_20260819_203206`
  - M2C1 `..._shuffle_gated_path_slice-hard150-v1_n150_20260819_203214`
  - M2C2 `..._irrelevant_gated_path_slice-hard150-v1_n150_20260819_203222`
- 结果：

| 组 | EM | F1 | 错题 | calls | 秒/题 | tokens |
|---|---|---|---|---|---|---|
| B0 | 0.1733 | 0.1607 | 124 | 14.96 | 43.29 | 9877 |
| M1 gated | **0.1933** | 0.1609 | 121 | 15.41 | 52.39 | 10393 |
| **M2** | **0.1467** | 0.1336 | 128 | 16.86 | 56.31 | 11340 |
| M2C1 shuffle | **0.1933** | 0.1737 | 121 | 16.41 | 55.38 | 10815 |
| M2C2 irrelevant | 0.1800 | 0.1549 | 123 | 16.30 | 46.91 | 11064 |

成对（相对 M2）：vs B0 修好 12 / 弄坏 16（净 **−4**）；vs M1 净 −7；vs C1 净 −7；vs C2 净 −5。M1 vs B0：修好 16 / 弄坏 13（净 **+3**）。depth1 `gold_sel` 五组 **同为 0.8844**（130/147），无 recall 损失。

- 异常：M2 效率也差（calls/时延/token 全组最高）。M1 与 M2C1 的 EM 数值相同（0.1933）但是不同记忆，不能当成 M1 过了 path 对照。未升 `ROLLBACK`（gold_sel 未掉，M1 相对 B0 仍略正）
- 结论：**H4 未过。** 真实 path 记忆差于 shuffle 与错误类型对照，也差于 B0。path template 在 hard150 上当前实现有害。禁止 M3 / reflection / Self-Play。不得与 prefix150 的 0.85 混比
- 判定后状态：`3-M2` / `GATE_HOLD` / 诊断 M2 为何低于 C1；禁止 M3 / Self-Play

### LOG-043 — 2026-08-20 — hard150 诊断：tail_sem 放大「像问题的错误第一跳」，gold 位次并不更差

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / M2 GATE_HOLD 诊断，优先 `tail_sem`。不是 M3
- 代码：未改推理；离线对照 LOG-042 的 M2 / M2C1 / B0 / M1 trace
- 配置：同 LOG-042
- 产物：无新 run_dir
- 结果：

**R4 修正：不是 gold 被 `tail_sem` 压下去，而是错误边被抬成高置信第一跳。**

| 指标（depth1） | B0 | M1 | M2 | M2C1 |
|---|---|---|---|---|
| gold 进 LLM 候选 | 0.884 | 0.884 | 0.884 | 0.884 |
| gold top1 / top6 | 0.102 / 0.408 | 0.143 / 0.476 | **0.143 / 0.476** | 0.156 / 0.476 |
| gold 均值位次 | 22.16 | 20.41 | **20.25** | 20.20 |
| 第一跳不是 gold | 0.860 | 0.820 | 0.820 | 0.807 |
| **top1 且 struct≥0.5（高置信错边）** | 0.000 | 0.140 | **0.147** | **0.093** |
| gold 有 path 命中时 struct=0 比例 | — | — | 67.7% | 67.7% |
| 命中时 gold `tail_sem` 均值 | — | — | **0.925** | 0.925 |
| 竞争边 `tail_sem` 均值 | — | — | 0.769 | 0.769 |

Gold 有模板时 fused 仍平均低于最强竞争边 **−0.118**（42 次命中里只有 10 次 gold fused 更高）。`tail_sem` 对 gold 本身是高分，挡不住「更像问题的错误 2-hop」。

**相对 C1 的 20 道独错里，depth1 几乎没改：** 18/20 top1 相同，8/20 LLM 选出的 relation 集合相同，11/20 **两边第一跳都已经选出了 gold**。净 −7 题有一大部分是难题上后续跳/答案抽取噪声。真正 top1 不同的只有 2 题，都是高 `tail_sem` 的错边：
- `what new movies is robert pattinson in?` → M2 把 `film.actor.film -> film.performance.actor`（tail=0.997, struct=0.955）顶到第一
- `where did cs lewis wrote?` → M2 把 `book.written_work.author`（tail=1.0, struct=0.894）顶到第一，gold 是 `people.person.places_lived`

Shuffle 把「问题词 ↔ 第二跳文本」的配对打散后，高置信错边从 14.7% 降到 9.3%，EM 反而更高——这就是 H4 失败的机制证据。

- 异常：M2 的 gold 位次 **好于 B0**（top1 0.143 vs 0.102），EM 却更差。说明硬切片上「把 gold 往前排」抵不过「把像问题的错边排得更自信」。未升 `ROLLBACK`（gold_sel 未掉）
- 结论：R4 成立，形式是 **有害的问题条件化结构分**，不是覆盖不足、也不是 first-hop 折叠。保持 `GATE_HOLD`。下一步若修，只关 `tail_sem`（M2-notail），不要扩库、不要 M3
- 判定后状态：`3-M2` / `GATE_HOLD` / 允许 M2-notail 诊断切片；禁止 M3 / Self-Play

### LOG-044 — 2026-08-20 — M2-notail：关闭 tail_sem 并启动 hard150 n=150

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`code` + `eval`
- 对应方案：Phase 3 / LOG-043 唯一允许切片。**不是 M3**
- 代码：`--kg_memory_use_tail_sem`（默认 1）。为 0 时 `tail_norm={}`，结构分 = `max_t(structural_score)`；trace 的 `tail_semantic` 为 null。`GROUP=M2NOTAIL` 固定 path 库 + gated + tail_sem=0。run 目录带 `_notail`。单测 `path_notail_ignores_second_hop`：统计相同、第二跳不同的两条第一跳结构分必须相等。`check_kg_memory_m1.py` 14/14 PASS。未改 fusion、库、seed、type 数
- 配置：`GROUP=M2NOTAIL KG_MEMORY_FUSION=gated QUESTIONS_FILE=eval_slices/hard150_v1.json START=0 LIMIT=150 GPU_IDS=3`。n=20 作为该 run 的前 20 题，不单独开跑、不用于判定
- 产物：启动中，目录名应含 `gated_path_notail_slice-hard150-v1_n150`
- 结果：待跑完。对照冻结为 LOG-042 的 B0 / M2 / M2C1
- 异常：无（启动）
- 结论：只关 `tail_sem`。完成后才能判断 R4。禁止 M3 / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / M2-notail n=150 进行中

### LOG-045 — 2026-08-20 — M2-notail hard150 n=150：回到 B0，仍未过 H4；放弃 path first-hop

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval` + `decision`
- 对应方案：Phase 3 / LOG-044 完成对照。**不是 M3**
- 代码：未改；配置 `kg_memory_use_tail_sem=0`，path 库与 gated 同 LOG-042
- 配置：`GROUP=M2NOTAIL`，切片 `hard150_v1`，n=150，GPU 3
- 产物：`PoG/result/webqsp_gpt-3.5-turbo-0125_kgmem-relation_rerank_top6_gated_path_notail_slice-hard150-v1_n150_20260820_005444/`（`evaluation.total=150`，`updated_at=2026-08-20T02:41:13`）。trace 中 `tail_semantic` 全为 null（5564/5564）
- 结果：对照冻结为 LOG-042。

| 组 | EM | F1 | 错题 | calls | 秒/题 | tokens |
|---|---|---|---|---|---|---|
| B0 | 0.1733 | 0.1607 | 124 | 14.96 | 43.29 | 9877 |
| M1 gated | **0.1933** | 0.1609 | 121 | 15.41 | 52.39 | 10393 |
| M2（有 tail_sem） | 0.1467 | 0.1336 | 128 | 16.86 | 56.31 | 11340 |
| **M2-notail** | **0.1733** | 0.1576 | 124 | 16.07 | 38.06 | 10838 |
| M2C1 shuffle | **0.1933** | 0.1737 | 121 | 16.41 | 55.38 | 10815 |
| M2C2 irrelevant | 0.1800 | 0.1549 | 123 | 16.30 | 46.91 | 11064 |

成对（相对 notail）：vs B0 修好 12 / 弄坏 12（净 **0**）；vs M1 净 **−3**；vs M2 净 **+4**；vs C1 净 **−3**；vs C2 净 **−1**。

gold 进入 LLM 候选（retrieved）六组同为 **0.8811**（126/143）。selected_recall：B0 0.4476 / M1 0.4266 / M2 0.4545 / **notail 0.4825** / C1 0.4615 / C2 0.4476。无 recall 损失。depth1 高置信错边（top1 且 struct≥0.5）：notail **0.161** 与有 tail_sem 的 M2 **相同**（23/143），C1 0.112，B0 0。

R4 部分成立：关掉 `tail_sem` 把 EM 从 0.1467 拉回 B0（含 LOG-043 的 Pattinson 题），但 **max_t(structural_score) 仍制造同等比例的高置信错第一跳**，不足以优于 M1 或 C1/C2。

- 异常：notail 秒/题低于 B0（38 vs 43），但 calls/tokens 仍高于 B0。未升 `ROLLBACK`（retrieved 未掉，selected 反而最高；M1 相对 B0 仍净 +3）
- 结论：**H4 仍未过。** 按已约定规则：notail ≤ M1 且 ≤ C1/C2 → **放弃 path template 做 first-hop rerank**，冻结 M1 gated。禁止再修 M2 first-hop，禁止 M3 / reflection / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / 若继续则只跑 hard150 上 M1 的 C1/C2

### LOG-046 — 2026-08-20 — 启动 hard150 上 M1 的 C1/C2（并行）

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / LOG-045 唯一允许切片。schema H4，**不是 M2C1/M2C2，不是 M3**
- 代码：未改推理。`GROUP=C1/C2` 默认加载冻结 schema 库
- 配置：`QUESTIONS_FILE=eval_slices/hard150_v1.json START=0 LIMIT=150 KG_MEMORY_FUSION=gated`；库 `schema_full_ee55ef9f17_20260818_114056`；C1 `ablation=shuffle` GPU 3；C2 `ablation=irrelevant` GPU 4
- 产物：
  - C1 `..._shuffle_gated_slice-hard150-v1_n150_20260820_085418`（无 `_path`）
  - C2 `..._irrelevant_gated_slice-hard150-v1_n150_20260820_085418`（无 `_path`）
- 结果：启动中。对照冻结为 LOG-042 的 B0 EM 0.1733、M1 EM 0.1933。完成后才判定 M1 在 hard150 是否过 H4
- 异常：无（启动）
- 结论：只补 schema 对照。禁止用 path 对照冒充。禁止 M3 / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / 等待 C1/C2 n=150 完成

### LOG-047 — 2026-08-20 — hard150 M1 vs C1/C2：EM 过对照，gold_sel 未过，保持 GATE_HOLD

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / schema H4 on hard150。**不是 M3 / Phase 4**
- 代码：未改
- 配置：同 LOG-046；对照冻结 LOG-042 的 B0 / M1
- 产物：
  - C1 `..._shuffle_gated_slice-hard150-v1_n150_20260820_085418`
  - C2 `..._irrelevant_gated_slice-hard150-v1_n150_20260820_085418`
- 结果：

| 组 | EM | F1 | 错题 | calls | 秒/题 | tokens | gold_sel | gold_ret |
|---|---|---|---|---|---|---|---|---|
| B0 | 0.1733 | 0.1607 | 124 | 14.96 | 43.29 | 9877 | 0.4476 | 0.8811 |
| **M1 gated** | **0.1933** | 0.1609 | 121 | 15.41 | 52.39 | 10393 | **0.4266** | 0.8811 |
| C1 shuffle | 0.1733 | 0.1622 | 124 | 14.40 | 37.97 | 10054 | **0.4685** | 0.8811 |
| C2 irrelevant | 0.1800 | **0.1665** | 123 | 13.95 | 32.73 | 9334 | 0.4615 | 0.8811 |

成对 EM（相对 M1）：vs B0 修好 16 / 弄坏 13（净 **+3**）；vs C1 净 **+3**；vs C2 净 **+2**。gold 进入 LLM 候选四组同为 0.8811（126/143）。selected_recall：M1 **61/143**，B0 64、C1 67、C2 66。

- 异常：M1 秒/题与 tokens 高于对照。C1/C2 日志有 LLM 超时后重试，exit 0、`evaluation.total=150`。未升 `ROLLBACK`（EM 仍优于 B0 与 C1/C2）
- 结论：**任务指标 H4（EM）过，机制门槛未过。** EM 高于 shuffle / 错误类型，但 gold 第一跳选中率低于 B0（−2.1pp）与 C1（−4.2pp），F1 也略低于对照。按 §16.1 不得把 prefix150 的 M1 过门槛迁到 hard150，也不得进入 Phase 4。禁止 M3 / Self-Play
- 判定后状态：`3-M2` / `GATE_HOLD` / 诊断 M1 gold_sel 为何低于对照

### LOG-048 — 2026-08-20 — hard150 诊断：M1 vs C1 的 EM 翻盘与 first-hop 无关

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`eval`
- 对应方案：Phase 3 / LOG-047 唯一允许的离线诊断。不是 M3 / Phase 4
- 代码：未改推理。复用 `diagnose_m1_gate_hold.py`，对照 LOG-042/047 trace
- 配置：同 LOG-047
- 产物：`..._gated_slice-hard150-v1_n150_20260819_203158/kg_memory_gate_hold_diagnosis_hard150.json`
- 结果：

Gold 进入 LLM 候选四组同为 126。selected：B0 64 / M1 **61** / C1 67 / C2 66。M1 相对 C1 少选 gold **10** 题、多选 **4** 题（净 −6）。

M1 depth1：promote 19 / demote 7；gold `struct=0` **88/130（68%）**。gated 仍在把无 hit 的 gold 往前推，但 LLM 选中率没跟上。

**相对 C1 的 EM 翻盘题，depth1 top1 全部相同：** M1 独对 15 题、C1 独对 12 题，**27/27 第一跳 top1 一致**。其中 M1 独对题双方 gold_sel 同为 7。即净 +3 题发生在后续跳 / 答案抽取，不是 schema rerank。

C1 选了 gold、M1 没选的 10 题里，列出的样例 depth1 top1 也与 C1 相同（如 Ziva / Ronaldo / Chagall）。gold_sel 差距同样不是「shuffle 把 gold 排到第一、真记忆没排到」。

- 异常：未升 `ROLLBACK`（retrieved 未掉；EM 仍 ≥ B0）。不据此改 fusion
- 结论：**hard150 上 M1 的 EM 优势不能归因于结构记忆。** H4 机制未过。禁止进 Phase 4 / M3 / 再调 first-hop。保持 `GATE_HOLD`
- 判定后状态：`3-M2` / `GATE_HOLD` / 不再新开 first-hop 跑数

### LOG-049 — 2026-08-20 — 显式判定 `STOP_DIRECTION`（停止扩大本实验）

- 判定前状态：`3-M2` / `GATE_HOLD`
- 类型：`decision`
- 对应方案：判定算法 `STOP_DIRECTION`；§18 最小闭环第 6–8 步；H4；§16.3。**不是 Phase 4 试跑**
- 代码：未改
- 配置：校验切片仍为 `hard150_v1`。prefix150 的 LOG-026 只作档案
- 产物：无新 run。本条为方向判定
- 结果：证据链（只引用已记录数字，不新开跑数）

| 问题（§18） | 冻结切片上的答案 | 证据 |
|---|---|---|
| KG 结构统计能否改善 relation 选择？ | 否（无归因） | LOG-047/048：M1 EM 相对 C1 净 +3，但 27/27 翻盘题 depth1 top1 相同；gold_sel 61 < C1 67 / B0 64 |
| 多跳模板是否提供额外价值？ | 否，且有害 | LOG-042/045：M2 EM 0.1467 < B0；notail 回到 B0 0.1733，仍 ≤ C1/C2 |
| 同一批结构证据能否改善 reflection？ | 不得检验 | §18 要求 relation 记忆先确认有效；hard150 未确认 |
| 增益是否真正来自记忆内容？（H4） | 否 | 真记忆不优于对照的可归因机制；shuffle 在 first-hop 上与 M1 同 top1 |
| 是否值得 Self-Play？ | 否 | §16.2 / §18 第 8 步未满足 |

**为何是 `STOP_DIRECTION` 而不是继续 `GATE_HOLD`：** `GATE_HOLD` 的「只诊断」已做完（LOG-043/048），允许切片上已无下一组 first-hop 跑数。继续只能换注入点（Phase 4）或再调 rerank，二者都是扩大范围。

**为何不是 `ROLLBACK`：** §16.3 未整条触发。gold 进入 LLM 候选未因 rerank 持续下降（四组 retrieved 同为 0.8811）；记忆可重放；不拆除 retrieval。效率变差也不是主因。EM 数值上 M1 仍 ≥ B0，但 LOG-048 已说明该差值不可归因，故不能靠 EM 数字挡住停扩。

**停止范围：** 本实验按 §18 的扩大路径（M3、Phase 4 reflection、Phase 5 Self-Play、以及任何新的 first-hop fusion/库/tail_sem 变体）。schema/path 库与注入代码保留档案。

- 异常：无。不把 prefix150 EM≈0.85 的 M1 过门槛迁回 hard150
- 结论：**判定 `STOP_DIRECTION`。** 结构记忆以 first-hop rerank 改善 PoG 的方向，在冻结难切片上不成立。停止扩大
- 判定后状态：`3-M2` / `STOP_DIRECTION` / 无下一组跑数

---

### LOG-050 — 2026-08-20 — 修订实验计划：停止 first-hop rerank，转入 V2 reflection-only 分支

- 判定前状态：`3-M2` / `STOP_DIRECTION`
- 类型：`plan_change`
- 对应方案：旧方案 §18、§16.1–§16.3；新方案 `experimental_plan_kg_memory_v2.md`
- 代码：未改；本条只记录实验计划和推进门槛变更
- 产物：
  - 新实验计划：`clue_on_graph/experimental_plan_kg_memory_v2.md`
  - 旧方案：`clue_on_graph/experimental_plan_kg_memory_from_gpt56.md`，保留为历史版本

#### 变更依据

1. hard150 上 M1 的 EM 相对 B0 有净 +3，但 M1 的 gold first-hop selected recall 为 61/143，低于 B0 的 64/143、C1 的 67/143 和 C2 的 66/143。
2. M1 与 C1 的 EM 翻盘题中，27/27 道题的 depth1 top1 相同，M1 的 EM 差异不能归因于 first-hop relation rerank。
3. M1 中 gold `struct=0` 为 88/130（68%），说明当前证据覆盖和无证据 fallback 仍不足。
4. M2 的 EM 为 0.1467，低于 B0 的 0.1733；关闭 `tail_sem` 后 M2-notail 回到 B0 水平，但仍未优于真实对照，且高置信错误 first-hop 仍然存在。
5. 当前实验没有测试 reflection Decision A/B，因此不能由 first-hop 分支失败推出 KG structural memory 的整体方向失败。

#### 具体变更

- 将旧结论的范围从 `STOP_DIRECTION` 收窄为：

  ```text
  STOP_FIRST_HOP_RERANK
  ```

- 关闭 M3、所有新的 M1/M2 first-hop fusion 变体、`tail_sem` 变体和 first-hop memory 扩库。
- 不再要求 relation memory 先通过才能启动 reflection；relation selection 与 reflection 改为独立实验分支。
- V2 的主线改为 reflection-only：不改变 first-hop relation 顺序，只在 Decision A/B 前提供 validated KG structural evidence。
- 新增 R0/R1/R2/R3/RC1/RC2 实验矩阵，分别测试无 memory、Decision A、Decision B、联合 reflection、shuffle 和 irrelevant evidence。
- hard150 从 V2 起只作为 `development_stress` 切片；必须新增并冻结 `final_unseen`，才可形成最终主结果。
- V2-0 审计完成前，不得启动 R1/R2/R3，也不得进行新的正式评测。
- Self-Play 仍然禁止，只有在 V2 reflection-only 在 `final_unseen` 上形成可归因正结果后才重新评估。

#### V2-0 唯一允许的下一步

1. 准备并冻结 `final_unseen`；
2. 统一 timeout、retry、max-token、seed 和异常记录；
3. 解释 relation-valid 分母 143 与全题分母 150；
4. 检查 `semantic_filter_relations()` 与 reflection evidence 的先后顺序；
5. 补齐 Decision A/B 的 evidence、decision、post-decision outcome trace；
6. 通过 memory-off 与 B0 等价性、evidence gate 和 shuffle/irrelevant checker。

- 异常：无。本条没有新增实验数据。
- 结论：接受 V2 方案变更。当前研究状态由“停止扩大旧 first-hop 实验”转为“完成 V2-0 审计后，独立评估 reflection-only structural evidence”。
- 判定后状态：`V2-0` / `PLAN_REVISION` / 等待协议审计完成

### LOG-051 — 2026-08-20 — 按 V2 决定下一步：先冻结切片，不启动 reflection 评测

- 判定前状态：`V2-0` / `PLAN_REVISION`
- 类型：`decision`
- 对应方案：`experimental_plan_kg_memory_v2.md` §4、§10 V2-0。**不是 R1/R2/R3，不是 first-hop**
- 代码：未改
- 配置：沿用冻结推理（gpt-3.5-turbo-0125，temp 0.3/0.3，depth 4，`max_length` 4096）。first-hop rerank 保持关闭
- 产物：无新 run
- 结果：对照 V2 阶段顺序，当前唯一允许的下一步是 **V2-0 第一项**，不是 V2-1 单测，更不是 smoke。

| 候选动作 | 判定 |
|---|---|
| 再开 M1/M2 first-hop | 禁止（V2 §1.1 / §3.2） |
| 直接实现并跑 R1/R2 | 禁止（V2-0 未验收） |
| 在 hard150 上选阈值当主结果 | 禁止（hard150 只是 development_stress） |
| 先跑一遍 final_unseen 摸底 | 禁止（看过结果再冻方法） |
| **冻结 `final_unseen_v1` + 标记 hard150 角色** | **唯一允许** |

`final_unseen_v1` 抽取规则（实现时必须按此，不得改用 both-wrong / M1 错题）：

1. 全集：WebQSP test（1639）
2. 去掉 `hard150_v1` 的 150 题
3. 去掉 prefix150（test[:150]）；二者重叠 13，并集约 287 题
4. 剩余按 test index 排序后 `even_subsample` 到 150
5. 不读取任何 KG-memory run 的对错；不读取 original PoG / rel+decomp 的 error 集合来偏向难/易
6. 写入 `PoG/eval_slices/final_unseen_v1.json` 与 `.questions.txt`，记录 `questions_sha256`；`role=final_unseen`；**在 V2-3 方法冻结前不得对该切片跑 LLM**

hard150 只加 `role=development_stress`，不改题目集合。

V2-0 其余项（timeout/retry、143/150 分母、semantic_filter 顺序、Decision A/B trace、B0 等价）排在切片冻结之后，仍在 V2-0 内，不得跳到 R1。

- 异常：无
- 结论：按 V2，下一步实验是 **协议审计的切片冻结**，不是 reflection 注入跑数。判定 `CONTINUE_PHASE_V2_0`
- 判定后状态：`V2-0` / `CONTINUE_PHASE_V2_0` / 构建并冻结 `final_unseen_v1`

### LOG-052 — 2026-08-20 — 冻结 `random150_v1` 为能力主评估；hard150 只作压力测试

- 判定前状态：`V2-0` / `CONTINUE_PHASE_V2_0`
- 类型：`build` + `decision`
- 对应方案：V2 §4 修订。用户要求：hard150 过难，能力评估改为 WebQSP 均匀随机 150 题
- 代码：`PoG/build_random_eval_slice.py`；`eval_slices.py` 增加 `FROZEN_RANDOM150_V1`。未改推理，未跑 LLM
- 配置：`seed=42`，从 `WebQSP.json` 全部 1639 题 `random.sample` 150，再按 test index 排序落盘。不看任何系统对错，不剔除 hard150 / prefix150
- 产物：
  - `PoG/eval_slices/random150_v1.json`（`questions_sha256=a033f485c941…`，index min=13 / max=1622）
  - `PoG/eval_slices/random150_v1.questions.txt`
  - `hard150_v1.json` 增加 `role=development_stress`，题目集合与 sha256 `09ad7d10f566b791…` 未改
- 结果：与 hard150 重叠 **15**，与 prefix150 重叠 **13**（随机样本的期望重叠约 14，属正常）。LOG-051 的「去掉 hard+prefix 再均匀抽」作废，改由本条规则冻结
- 异常：无评测。方法冻结前禁止对该切片跑 LLM
- 结论：V2 主评估切片是 **`random150_v1`**；hard150 只用于 V2-2 选阈值与压力测试。判定 `CONTINUE_PHASE_V2_0`，下一步做 V2-0 剩余审计，不是 R1
- 判定后状态：`V2-0` / `CONTINUE_PHASE_V2_0` / timeout-retry 与 trace schema 审计

### LOG-053 — 2026-08-20 — 将 `hard150_v1` / `random150_v1` 写入 V2 实验计划

- 判定前状态：`V2-0` / `CONTINUE_PHASE_V2_0`
- 类型：`plan_change`
- 对应方案：`experimental_plan_kg_memory_v2.md` 升为 V2.1，§4 / §10 / §11 / §12 / §13
- 代码：未改推理。切片文件同 LOG-052
- 结果：计划中两切片角色、sha256、seed=42、重叠 15/13、V2-2 只用 hard150、V2-3 只在 random150 上一次跑，已与日志对齐
- 异常：无跑数
- 结论：执行依据已同步。下一步仍是 V2-0 剩余审计，不对 `random150_v1` 跑 LLM
- 判定后状态：`V2-0` / `CONTINUE_PHASE_V2_0` / timeout-retry 与 trace schema 审计

### LOG-054 — 2026-08-20 — V2-0 协议审计通过：分母、timeout/retry、evidence 顺序、trace、B0 等价

- 判定前状态：`V2-0` / `CONTINUE_PHASE_V2_0`
- 类型：`code` + `eval`（离线 checker，无 LLM 跑数）
- 对应方案：V2 §4.3 / §9 / §10 V2-0。**不是 R1/R2/R3，不是 random150 评测**
- 代码：HEAD `b3ff1c0` 上工作树修改。新增 `PoG/v2_protocol.py`、`PoG/reflection_structural_memory.py`、`PoG/check_v2_protocol.py`；修改 `utils.py`（Decision A/B 接 evidence，mode=none 为 no-op）、`kg_memory_retrieval.py`（reflection stage gate、raw records、trace 回写）、`output_paths.py`（run_meta 写入 timeout/retry）、`main_freebase.py` / `freebase_func.py`（`relation_semantic_top_k` 默认改为冻结值 40，与 hard150 B0 脚本一致）
- 配置：冻结 `max_length=4096`，`OPENAI_TIMEOUT=180`，`OPENAI_MAX_RETRIES=5`，temp 0.3/0.3，depth 4，`relation_semantic_top_k=40`，seed=42
- 产物：`python check_v2_protocol.py` → ALL CHECKS PASSED。无新 run_dir
- 结果：

分母（WebQSP `InferentialChain` 第一跳；缺失是标注空缺，不是 memory bug）：

| 切片 | n_all（EM/F1 分母） | n_relation_valid（first-hop recall 分母） | 缺 gold 第一跳 |
|---|---|---|---|
| `hard150_v1` | 150 | **143** | 7 |
| `random150_v1` | 150 | **149** | 1 |

hard150 缺 gold 第一跳的 7 题：`who plays blaine in batman?`；`which country was justin bieber born in?`；`what does wh smith stand for?`；`what state is barack obama from?`；`what did james k polk do before he was president?`；`what years did joe montana win super bowl?`；`what was franklin d roosevelt's job before president?`。random150 缺 1 题：`what was franklin d roosevelt's job before president?`（与 hard150 重叠）。

顺序：`relation_search_prune` 先 `semantic_filter_relations` 再 `apply_relation_kg_memory`；`if_finish_list` 不调用语义过滤、不 rerank first-hop；reflection evidence 只在 Decision A/B 前附加。`mode=none` 时 prompt 与 B0 前缀相同；`mode=reflection` 不打开 relation 阶段。

- 异常：无。未对 `random150_v1` 跑 LLM。`relation_semantic_top_k` argparse 默认从 20 改为 40，与已冻结 B0 脚本/run_meta 对齐，不改变已完成 hard150 数字
- 结论：V2-0 验收全部通过。判定 `ACCEPT_PHASE_V2_0_AND_ADVANCE`。下一步是 V2-1 单测，不是 smoke
- 判定后状态：`V2-1` / `ACCEPT_PHASE_V2_0_AND_ADVANCE` / 跑 reflection evidence 单元测试

### LOG-055 — 2026-08-20 — V2-1 reflection evidence 单测通过

- 判定前状态：`V2-1` / `ACCEPT_PHASE_V2_0_AND_ADVANCE`
- 类型：`code` + `eval`（单元测试，无 LLM 跑数）
- 对应方案：V2 §5.2 / §6 / §10 V2-1。**不是 V2-2 smoke，不是 R1 评测**
- 代码：`PoG/check_reflection_memory.py`；复用 LOG-054 的 `reflection_structural_memory.py`。M1 checker `--skip_frozen 1` 仍 ALL CHECKS PASSED
- 配置：product gate `confidence * applicability >= 0.36`；high-branching 阈值 8.0；seed=42。无 SPARQL / 无 LLM
- 产物：`python check_reflection_memory.py` → ALL CHECKS PASSED
- 结果：

| 检查 | 结果 |
|---|---|
| 有 witness 的 validated 正例 | 正向干预，四段 summary，无 continue/stop/backtrack 结论句 |
| 无 witness | 归入 unknown，prompt 回退 B0 |
| 低 confidence（0.2） | 不通过 gate，不干预 |
| 已探索 path | 不进入 validated_unexplored，不干预 |
| evidence/utility score | `cov*conf*app`；branching 越大 utility 越低；≥8 进 high_cost |
| shuffle / irrelevant | 候选数与 n_evidence_items 不变，四段标题不变；shuffle 打乱数值配对；irrelevant 只改 path/type |
| memory-off fallback + trace | mode=none 不改 B0 前缀；evidence 写入 `kg_memory.reflection_judge/select` |

- 异常：无。未跑 hard150，未对 `random150_v1` 跑 LLM
- 结论：V2-1 验收通过。判定 `ACCEPT_PHASE_V2_1_AND_ADVANCE`。下一步是 V2-2 smoke（hard150 `START=0 LIMIT=20` 的 R0/R1/R2/RC1/RC2），不是主评估
- 判定后状态：`V2-2` / `ACCEPT_PHASE_V2_1_AND_ADVANCE` / hard150 LIMIT=20 smoke

### LOG-056 — 2026-08-20 — 启动 V2-2 smoke：hard150 LIMIT=20 五组并行

- 判定前状态：`V2-2` / `ACCEPT_PHASE_V2_1_AND_ADVANCE`
- 类型：`run`
- 对应方案：V2 §10 V2-2 smoke。**不是 n=150，不是 random150，不是 R3 主评估**
- 代码：`PoG/run_PoG_reflection_memory_v2.sh`；`analyze_reflection_memory_run.py`；`check_reflection_run.py`；reflection retrieval 每实体 top-24；run 目录 tag 区分 `a`/`b`/`a-b`
- 配置：`QUESTIONS_FILE=eval_slices/hard150_v1.json`，`START=0 LIMIT=20`，`GPU_IDS=3`，`STAGGER_SEC=15`，五组 `R0 R1 R2 RC1 RC2`。LLM gpt-3.5-turbo-0125，temp 0.3/0.3，depth 4，max_length 4096，timeout 180，retries 5，semantic top-k 40，schema 库 `schema_full_ee55ef9f17_20260818_114056`。R1=`reflection_judge`，R2=`reflection_select`，RC1/RC2=A+B + shuffle/irrelevant
- 产物：启动中；目录名应含 `_slice-hard150-v1_n20_`
- 结果：待跑完。完成后先跑 `check_reflection_run.py` 与 `analyze_reflection_memory_run.py`，协议通过才允许 n=150
- 异常：无（启动记录）。脚本拒绝未授权的 random150
- 结论：按 V2-2 固定顺序先 smoke。判定 `CONTINUE_PHASE_V2_2`
- 判定后状态：`V2-2` / `CONTINUE_PHASE_V2_2` / hard150 LIMIT=20 五组进行中

### LOG-057 — 2026-08-20 — smoke 启动失败：bash 只读变量 `GROUPS` 被当成组名

- 判定前状态：`V2-2` / `CONTINUE_PHASE_V2_2`
- 类型：`incident`
- 对应方案：V2-2 smoke。不是评测
- 代码：`run_PoG_reflection_memory_v2.sh` 把循环变量从 `GROUPS` 改为 `GROUPS_LIST`/`V2_GROUPS`
- 结果：父进程立刻 `GROUP must be R0... (got 10002)` 并退出。`GROUPS` 是 bash 只读数组（用户 GID），`for g in $GROUPS` 展开成 GID 而不是 R0/R1。无 run_dir，无 LLM 调用
- 异常：协议未开始。已修复
- 结论：自我修正后重跑同一 smoke。判定仍 `CONTINUE_PHASE_V2_2`
- 判定后状态：`V2-2` / `CONTINUE_PHASE_V2_2` / 重跑 hard150 LIMIT=20 smoke

### LOG-058 — 2026-08-20 — V2-2 smoke 完成：协议通过，阶段隔离成立

- 判定前状态：`V2-2` / `CONTINUE_PHASE_V2_2`
- 类型：`eval`
- 对应方案：V2-2 smoke n=20。**不是门槛判定，不是 V2-3**
- 代码：未再改推理。`python check_reflection_run.py` ALL CHECKS PASSED
- 配置：同 LOG-056/057；GPU3 五组并行
- 产物：
  - R0 `..._slice-hard150-v1_n20_20260820_135845`
  - R1 `..._kgmem-reflection_a_top6_slice-hard150-v1_n20_20260820_135859`
  - R2 `..._kgmem-reflection_b_top6_slice-hard150-v1_n20_20260820_135914`
  - RC1 `..._kgmem-reflection_a-b_top6_shuffle_slice-hard150-v1_n20_20260820_135929`
  - RC2 `..._kgmem-reflection_a-b_top6_irrelevant_slice-hard150-v1_n20_20260820_135945`
- 结果：五组均 n=20、exit 0、timeout 标记 0、first-hop `n_relation_order_changed=0`。

| 组 | EM | F1 | calls | 秒/题 | tokens | A_vis | B_vis |
|---|---|---|---|---|---|---|---|
| R0 | 0.20 | 0.1969 | 15.30 | 48.14 | 10715 | 0/15 | 0/13 |
| R1 | 0.15 | 0.1448 | 17.80 | 54.13 | 14913 | **17/20** | **0/14** |
| R2 | **0.30** | 0.2767 | 13.75 | 46.20 | 10266 | **0/14** | **11/12** |
| RC1 shuffle | 0.10 | 0.0868 | 15.15 | 49.88 | 13411 | 13/18 | 13/13 |
| RC2 irrelevant | 0.20 | 0.1778 | 20.10 | 57.08 | 16016 | 15/22 | 15/15 |

协议：R0 无可见 evidence；R1 只改 Decision A；R2 只改 Decision B；shuffle/irrelevant 有可见 evidence 且未动 first-hop。n=20 不得当门槛。

- 异常：无协议错误。RC2 tokens 最高。不得用 smoke EM 进 V2-3
- 结论：smoke 协议通过。判定 `CONTINUE_PHASE_V2_2`，下一步 hard150 n=150 development
- 判定后状态：`V2-2` / `CONTINUE_PHASE_V2_2` / 启动 hard150 n=150

### LOG-059 — 2026-08-20 — 启动 V2-2 development：hard150 n=150 五组并行

- 判定前状态：`V2-2` / `CONTINUE_PHASE_V2_2`
- 类型：`run`
- 对应方案：V2-2 development n=150。**不是 random150 / V2-3**
- 代码：同 LOG-058
- 配置：`START=0 LIMIT=150`，其余同 smoke；`GPU_IDS=3`，`STAGGER_SEC=15`，R0/R1/R2/RC1/RC2
- 产物：启动中；目录名应含 `_slice-hard150-v1_n150_`
- 结果：待跑完后按 §11.1 判定。hard150 数字只作进门，不是能力主结果
- 异常：无（启动记录）
- 结论：协议已过，扩大到 development 切片全量。不对 `random150_v1` 跑 LLM
- 判定后状态：`V2-2` / `CONTINUE_PHASE_V2_2` / hard150 n=150 五组进行中

### LOG-060 — 2026-08-20 — V2-2 development 完成：R2 过 §11.1，冻结 R2 进 V2-3

- 判定前状态：`V2-2` / `CONTINUE_PHASE_V2_2`
- 类型：`eval` + `decision`
- 对应方案：V2 §10 V2-2 development / §11.1。实验组 R0/R1/R2/RC1/RC2；规模 hard150 n=150。**hard150 只作进门，不是能力主结果**
- 代码：HEAD `b3ff1c0` 脏工作树（V2-0/V2-1/V2-2 实现未提交）。未改推理、未改阈值、未改 prompt
- 配置：同 LOG-056–059。GPU 3 五组并行。schema 库 `schema_full_ee55ef9f17_20260818_114056` hash `ee55ef9f175d`。timeout 180 / retries 5 / semantic top-k 40 / seed=42
- 产物：`python check_reflection_run.py` ALL CHECKS PASSED；`analyze_reflection_memory_run.py` 已写 `reflection_decision_metrics.json`
  - R0 `result/webqsp_gpt-3.5-turbo-0125_slice-hard150-v1_n150_20260820_142449`
  - R1 `..._kgmem-reflection_a_top6_slice-hard150-v1_n150_20260820_142504`
  - R2 `..._kgmem-reflection_b_top6_slice-hard150-v1_n150_20260820_142519`
  - RC1 `..._kgmem-reflection_a-b_top6_shuffle_slice-hard150-v1_n150_20260820_142534`
  - RC2 `..._kgmem-reflection_a-b_top6_irrelevant_slice-hard150-v1_n150_20260820_142549`
- 结果：五组均 n=150、timeout 标记 0、first-hop `n_relation_order_changed=0`。阶段隔离成立（R1 只 A，R2 只 B，R0 无可见 evidence）。

| 组 | EM | F1 | calls | 秒/题 | tokens | A_vis | B_vis | continue |
|---|---|---|---|---|---|---|---|---|
| R0 | 0.1800（27/150） | 0.1601 | 14.41 | 41.99 | 10289 | 0/114 | 0/85 | 0.7456 |
| R1 | 0.1733（26/150） | 0.1664 | 16.57 | 54.11 | 12328 | **102/123** | **0/88** | 0.7154 |
| R2 | **0.2000（30/150）** | **0.1792** | **12.81** | **36.34** | **9083** | **0/105** | **71/74** | 0.7048 |
| RC1 shuffle | 0.1800（27/150） | 0.1613 | 17.77 | 55.22 | 13234 | 94/109 | 78/81 | 0.7431 |
| RC2 irrelevant | 0.1733（26/150） | 0.1537 | 16.12 | 49.31 | 12295 | 108/119 | 87/96 | 0.8067 |

相对 R0 净翻盘：R1 −1（12/13）；R2 **+3**（17/14）；RC1 0（12/12）；RC2 −1（14/15）。R2 vs RC1 净 +3；R2 vs RC2 净 +4。

归因（不把 +3 EM 当机制证明）：17 道 R2 翻对中仅 **7** 题有 Decision B `prompt_visible_evidence`（另 10 题多为 depth-1，未走到 B）；14 道翻错中 5 题有 B 可见 evidence。在 R2 实际露出 B evidence 的 45 题子集上：R0/RC1/RC2 各对 10 题，R2 对 12 题（净 +2）。R1 翻对 12 / 翻错 13，其中 A 可见 6 vs 8，有害 ≥ 挽救。

§11.1：R2 满足「real evidence 明显优于 RC1/RC2」（EM/F1 均更高，且 calls/tokens/时延更低）；效率也优于 R0，不属于「只加 token」。R1 不满足任一条。未出现明显 harmful 相对挽救率上升（B 可见子集 7 挽救 / 5 有害）。未改阈值/prompt。R3 不在 V2-2 正式矩阵，无 n=150，不冻结。

- 异常：题级 EM 翻盘与 B 干预只部分对齐（与 T=0.3 及大量 depth-1 未触发 B 有关）。这削弱「EM +3 可由 Decision B 解释」，但不否定对照+效率条款。RC1/RC2 仍是 A+B 注入（与 runner 定义一致，冻结时不改成 B-only 对照，避免看完 development 后改设计）
- 结论：判定 `ACCEPT_PHASE_V2_2_AND_ADVANCE`。冻结方法 = **R2**。V2-3 只跑 R0/R2/RC1/RC2。不跑 R1（未过门槛）、不跑 R3（无 development）。hard150 数字不得写入论文主结果
- 判定后状态：`V2-3` / `ACCEPT_PHASE_V2_2_AND_ADVANCE` / 冻结后对 `random150_v1` 一次性跑 R0/R2/RC1/RC2

### LOG-061 — 2026-08-20 — 启动 V2-3：冻结后 `random150_v1` 一次性主评估

- 判定前状态：`V2-3` / `ACCEPT_PHASE_V2_2_AND_ADVANCE`
- 类型：`run`
- 对应方案：V2 §10 V2-3 / §11.1 通过后的一次性 `final_unseen`。实验组 **R0 / R2 / RC1 / RC2**（不跑 R1/R3，见 LOG-060）
- 代码：与 LOG-060 相同冻结；未改阈值、prompt、memory hash、top_k
- 配置：`V2_ALLOW_RANDOM150=1`，`QUESTIONS_FILE=eval_slices/random150_v1.json`（sha256 `a033f485c941…`），`START=0 LIMIT=150`，`GPU_IDS=6`（GPU 3 占用升高，改用空闲卡，不改推理），`STAGGER_SEC=15`，`V2_GROUPS="R0 R2 RC1 RC2"`。LLM/memory/timeout 与 V2-0 冻结一致
- 产物：启动中；目录名含 `_slice-random150-v1_n150_`
  - R0 `result/webqsp_gpt-3.5-turbo-0125_slice-random150-v1_n150_20260820_174057`
  - R2 `..._kgmem-reflection_b_top6_slice-random150-v1_n150_20260820_174108`
  - RC1 `..._kgmem-reflection_a-b_top6_shuffle_slice-random150-v1_n150_20260820_174123`
  - RC2 `..._kgmem-reflection_a-b_top6_irrelevant_slice-random150-v1_n150_20260820_174138`
  - 日志 `PoG/logs/v2_3_random150_n150_20260820.log`
- 结果：待跑完后只报 random150 主结果。hard150 不得替代、不得混报
- 异常：无（启动记录）。四组均已选中 150 题并开始跑
- 结论：方法已冻结，禁止中途看数调参。判定保持 `ACCEPT_PHASE_V2_2_AND_ADVANCE`，当前动作是跑完 V2-3
- 判定后状态：`V2-3` / `ACCEPT_PHASE_V2_2_AND_ADVANCE` / random150 R0/R2/RC1/RC2 进行中

### LOG-062 — 2026-08-20 — V2-3 主评估完成：R2 低于 R0 且不优于 RC1

- 判定前状态：`V2-3` / `ACCEPT_PHASE_V2_2_AND_ADVANCE`
- 类型：`eval` + `decision`
- 对应方案：V2 §10 V2-3 / §13。切片 `random150_v1`（sha256 `a033f485c941…`），n=150 / relation-valid 149。**这是能力主结果；hard150 不得替代、不得混报**
- 代码：冻结与 LOG-060/061 相同，未改阈值/prompt
- 配置：GPU 6；R0/R2/RC1/RC2 并行；约 17:40–19:26 CST；timeout 标记 0
- 产物：`python check_reflection_run.py` ALL CHECKS PASSED；first-hop `n_relation_order_changed=0`
  - R0 `result/webqsp_gpt-3.5-turbo-0125_slice-random150-v1_n150_20260820_174057`
  - R2 `..._kgmem-reflection_b_top6_slice-random150-v1_n150_20260820_174108`
  - RC1 `..._kgmem-reflection_a-b_top6_shuffle_slice-random150-v1_n150_20260820_174123`
  - RC2 `..._kgmem-reflection_a-b_top6_irrelevant_slice-random150-v1_n150_20260820_174138`
- 结果：

| 组 | EM | F1 | calls | 秒/题 | tokens | A_vis | B_vis | continue |
|---|---|---|---|---|---|---|---|---|
| R0 | **0.8533（128/150）** | **0.7239** | 15.62 | 41.95 | 9709 | 0/72 | 0/43 | 0.5972 |
| R2 | 0.8067（121/150） | 0.7032 | 15.79 | 42.00 | 10407 | 0/77 | 52/57 | 0.7403 |
| RC1 shuffle | 0.8133（122/150） | 0.7028 | **10.21** | **31.03** | **7969** | 55/59 | 40/44 | 0.7458 |
| RC2 irrelevant | 0.7867（118/150） | 0.6891 | 12.03 | 33.50 | 8331 | 57/62 | 48/50 | 0.8065 |

R2 vs R0：净 **−7**（4 翻对 / 11 翻错）。RC1 vs R0 净 −6；RC2 vs R0 净 −10。R2 vs RC1 净 −1（8 翻对 / 9 翻错）。在 R2 露出 B evidence 的 30 题上：R0 21 / R2 16 / RC1 19 / RC2 18。翻对 4 题中仅 2 题有 B 可见 evidence；翻错 11 题中 7 题有。Decision A continue 率 R0 0.60 → R2 0.74（hard150 上曾下降，此处反向）。

对照解读：真实 Decision B evidence 在独立随机切片上 **伤害准确率**，且 **不优于 shuffle**。hard150 上 R2 的 +3 EM / 更省 token **未复现**，不能当主结果。协议本身成立（阶段隔离、未动 first-hop、无 timeout）。

- 异常：无协议错误、无泄漏、无中途调参。RC1 为 A+B shuffle（与冻结 runner 一致），效率好于 R2 不能解释为「真实 B 有效」。触发 §11.2：与 RC1 无实质正向差异；continue 上升同时 EM 下降；memory 未减少无效搜索
- 结论：V2-3 **失败**。判定 `GATE_HOLD`。停止当前 reflection 变体扩大。不得进 V2-4 / Self-Play。不得把 hard150 R2 0.2000 写成能力结论。§11.3 全方向停止尚不成立（trajectory/fusion 未做）
- 判定后状态：`V2-3` / `GATE_HOLD` / 只允许诊断或整理负结果，不得扩大

### LOG-063 — 2026-08-21 — GATE_HOLD 离线诊断：B evidence 与问题无关，分数几乎不驱动选择

- 判定前状态：`V2-3` / `GATE_HOLD`
- 类型：`eval`（离线诊断，无 LLM）
- 对应方案：V2 §11.2 回到诊断；H3/H4。切片 `random150_v1` 已有 R0/R2/RC1/RC2 trace；hard150 R0/R2 仅作对照，不是主结果
- 代码：新增 `PoG/diagnose_v2_3_gate_hold.py`（只读 trace）。未改推理、未改阈值、未重跑
- 配置：无
- 产物：`PoG/result/v2_3_gate_hold_diagnosis_20260821.json`
- 结果：

**1. −7 EM 从哪来（同一 150 题划分）**

| 子集 | n | R0 对 | R2 对 | 净差 |
|---|---|---|---|---|
| 双方都未进入 reverse | 101 | 92 | 90 | −2（无 A/B，T=0.3） |
| 仅 R0 进入 reverse | 6 | 5 | 4 | −1 |
| 仅 R2 进入 reverse | 10 | 8 | 7 | −1 |
| 双方都进入 reverse | 33 | 23 | 20 | −3 |
| 其中 R2 露出 B evidence | 30 | 21 | 16（RC1 19 / RC2 18） | **相对 R0 −5，相对 shuffle −3** |

可归因伤害集中在 **30 道真正注入 Decision B evidence 的题**。101 道从未 reverse 的题上也有 −2，属温度噪声，不是 memory。

**2. continue 率 0.60→0.74 的来源**

R2 **不**向 Decision A 注入 evidence（A_vis=0）。首次 Decision A 分歧主要是 T=0.3：双方都有首次 A 的 33 题中 26 题一致（R0 停→R2 续 5；反向 2）。首次 A continue 率 0.51→0.63。**第二次及以后** Decision A continue 0.70→0.88：这才是 B 加实体之后的间接效应。R2 每题回溯加实体 0.74，R0 0.17，RC1 0.27。

首次 A 一致的 26 题上 EM 打平（16/16）。其中双方都 continue 的 16 题 EM 仍打平（8/8），但 R2 加实体 5.75 vs R0 1.0——多搜了，没有多对。

**3. 翻错题上的 evidence 内容**

B 可见题上，prompt 里 top-8 路径与问句 token 重叠率 **0.008**（93.5% 的题重叠为 0）。RC2 为 0（设计如此）。翻错且 B 可见的高频路径是 `people.person.gender` / `date_of_birth` / `place_of_birth`、`location.location.geolocation` / `contains`——高覆盖通用 schema，不是 governor / marry / team / high school。例：`who is the governor of virginia 2011?` 选出 `Virginia`，证据是 geolocation/contains。21/30 道 B 可见题把 **topic 实体**加回去。

每个 Decision B 事件平均 **866** 条 evidence_items（中位 24，最大 5790；候选实体均 45、最大 349）。prompt 只展示每段 top-8，但排序赢家是通用高覆盖关系。

**4. R2 vs RC1：真实分数几乎不改变选择**

双方首次 B 都选出实体的 13 题，选实体 Jaccard **0.77**，**10/13 完全相同**。shuffle 打乱 coverage/confidence/utility 后，LLM 仍选同一名字。H4 在机制层不成立：进入决策的是「多了一段结构证据 prompt + 更容易加实体」，不是校准过的结构分数。

**5. 为何 hard150 曾像过门槛（对照，不是主结果）**

hard150 路径–问句重叠同样 ≈0。首次 A 一致的 39 题上 R0 对 7、R2 对 12。难切片上「多加实体 / 多搜」可能碰到 R0 搜不够的题；random150 上 R0 已是 0.8533，同样行为变成过搜。hard150 +3 EM **不能**解释为结构 evidence 校准了 Decision B。

- 异常：无新协议错误。`n_positive_interventions` 把每实体 top-24 里过 gate 的记录全算进去，数值极大，不能当「有效干预次数」
- 结论：当前 reflection 变体失败原因是 **问题无关的 schema 流行度被当成 Decision B 证据，分数不驱动选择，却增加加实体与后续 continue**。判定保持 `GATE_HOLD`。不得进 V2-4 / Self-Play，不得据此改 prompt 后重跑 random150
- 判定后状态：`V2-3` / `GATE_HOLD` / 整理负结果或等待明确的 `PLAN_REVISION`，不得扩大当前变体

### LOG-064 — 2026-08-21 — 用户要求恢复原 PoG 栈并在两切片上复跑（非 V2 reflection）

- 判定前状态：`V2-3` / `GATE_HOLD`
- 类型：`decision` + `run`
- 对应方案：用户显式要求，不是 V2-4 / 不是改 R2。方法 = `run_PoG_test.sh` 默认栈：supervised relation memory + constraint compilation。`kg_memory_mode=none`
- 代码：`run_PoG_test.sh` 增加 `QUESTIONS_FILE` 与显式 `--kg_memory_mode none`。未改 V2 reflection 推理
- 配置：LLM gpt-3.5-turbo-0125，temp 0.3/0.3，depth 4，max_length 4096，semantic top-k 40。`relation_memory_mode=prompt` top_k=2 hybrid stages=relation；库 `memory/webqsp_gpt-3.5-turbo-0125_train_n600_20260703_231525`。`constraint_pushdown=on`，`constraint_routing=auto`（routing auto 时 **不注入** 旧 decomposition memory）。`START=0 LIMIT=150`。GPU 4
- 产物：启动中
  - hard150 `result/webqsp_gpt-3.5-turbo-0125_mem-prompt_top2_hybrid_stages-relation_decompmem-prompt_top2_slice-hard150-v1_n150_20260821_002352`
  - random150 `..._slice-random150-v1_n150_20260821_002358`
  - 日志 `PoG/logs/orig_relmem_constraint_hard150_20260821.log`、`..._random150_20260821.log`
  - 已加载 833 relation memory + 600 decomposition memory（routing=auto 时 decomp **不注入** prompt）
- 结果：20260814 全量 n=1639（同一方法）在两切片上的**旧预测**子集评测（不是本次复跑）：random150 EM **0.8533（128/150）** F1 0.7417；hard150 EM **0.0000（0/150）** F1 0.0152。后者是切片定义：hard150 = orig PoG 与该 20260814 跑数 **都错** 的 150 题。全量该跑数 EM 0.8414（1379/1639）。本次复跑是当前代码 + 同配置的温度复现，不得与 V2 R0/R2 混报
- 异常：无（启动记录）
- 结论：判定 `PLAN_REVISION` 仅用于原栈切片评测。V2 reflection 仍 `GATE_HOLD`，不得扩大
- 判定后状态：`V2-3` / `PLAN_REVISION` / 原栈 hard150+random150 进行中

---
