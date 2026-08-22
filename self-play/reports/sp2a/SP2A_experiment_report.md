# SP2-A 实验报告：真实 KG 环境验证

> 报告目录：`self-play/reports/sp2a/`  
> 计划版本：SP2A-PLAN 1.0  
> 协议版本：`sp-protocol-v1`  
> 总体要求：SP-GENERAL 1.10（收口登记后升级为 1.11）  
> 验收结论：**PASS**  
> 报告日期：2026-08-22

本报告只覆盖 Self-Play 实验的 SP2-A：**真实 KG 环境验证**。本轮不调用真实 LLM，不生成或注入 memory，不运行 WebQSP 20/150 或 CWQ 50 的 KGQA 效果评测，也不把 live KG 结果写成 Self-Play 经验。

## 1. 研究目标

在不调用真实 LLM、不生成或读取 memory、不给 Actor/Critic 提供 Oracle 信息的条件下，验证 SP1 adapter 能否把预制合法动作稳定地转换为真实 KG 请求，并将真实返回正确地转换为 PoG 可见状态。得到的是环境可用性与协议正确性证据，不是 KGQA 或 memory 效果。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/03_SP2A_live_kg_environment_validation.md` SP2A-PLAN 1.0 |
| 协议 | `sp-protocol-v1` |
| 配置 | `configs/sp2a_live_kg_v1.json` |
| 配置 SHA-256 | `46cb85863055ba94698dcde966168ecd5e77f465e6a06bd07b2e78de80d60023` |
| 开发任务 registry | `artifacts/registries/sp2a_development_task_registry_v1.json` |
| registry SHA-256 | `e85513a0c9ac1a1cbbb9586e033aaae1d121bcc21c85e2dd7e62a403b7906301` |
| endpoint / snapshot | `http://localhost:8890/sparql`（只读 POST，无凭证） |
| HTTP | urllib `application/x-www-form-urlencoded`，`format=json` |
| timeout / retries | 20s / max_retries=2 / backoff 0.5s, 1.0s |
| `allow_llm` / `allow_memory` / `allow_live_kg` | false / false / true |
| `backtrack_state_policy` | unsupported |

相对计划的文件对应：

| 计划功能 | 实际文件 |
|---|---|
| Live KG binding | `src/sp_memory/live_environment.py` |
| Request builder / response normalizer | `src/sp_memory/kg_sparql.py` |
| State transition adapter | `src/sp_memory/pog_adapter.py`（`stage=sp2a`） |
| Budget/counter ledger | `src/sp_memory/budget_ledger.py` |
| Recorded I/O writer/replayer | `src/sp_memory/recorded_io.py` |
| No-LLM/no-memory guard | `src/sp_memory/sp2a_guards.py` |
| Check runner | `src/sp_memory/sp2a_checks.py`、`scripts/run_sp2a_checks.py` |

## 3. 有效和无效运行

| 项 | 值 |
|---|---|
| 有效 Run | `sp2a-20260822T082704Z-28a5bc97`（E2A.1–E2A.7 全部 PASS，退出码 0） |
| 无效 Run | 无 |
| Git commit | `09fd3a5657889e1f986b7e22021b92a429695cce` |
| 工作树 | dirty（本轮新增 self-play SP2-A 代码与产物，未提交） |
| 单元测试 | 58 通过 / 0 失败 / 0 skip |

## 4. 实验设置

- 只执行预注册开发任务上的预制 `EXPAND`；非法动作在 KG 调用前由 SP1 validator 拒绝。
- Actor 决策不来自 LLM。`run_llm` 仍由 fail-fast guard 覆盖。
- live 查询使用独立登记的公开 Freebase MID（Obama `m.02mjmr`、Honolulu `m.02hrh`），不读取冻结评测集答案生成轨迹。
- timeout / malformed / endpoint failure / retry / 超预算使用 scripted transport，明确标注 `mode=scripted_transport`，不称为 live endpoint 成功。
- 不修改 `../data/`、`../cope_alias/`，也不修改原 PoG 基线文件。

## 5. E2A.1–E2A.7 结论

| 实验 | 结论 | 要点 |
|---|---|---|
| E2A.1 连通性、只读、schema | PASS | endpoint HTTP 200；name EXPAND 状态 `literal`；写 SPARQL 尝试 0；network_used=true |
| E2A.2 HEAD/TAIL 与 canonical triple | PASS | HEAD SPARQL 为 `entity relation ?x`，TAIL 为 `?x relation entity`；方向反转 0。Obama HEAD `place_of_birth` 返回 `m.02hrh0_`；登记的 Honolulu `m.02hrh` TAIL 为空，但 SPARQL/三元组方向仍正确 |
| E2A.3 VisibleState 转移 | PASS | 单跳与连续两跳均改变 state_id；frontier/triples 规范化可重放；live 重复执行 state_id 一致 |
| E2A.4 空结果、literal、重复、缺字段 | PASS | live：non-empty / empty / literal；scripted：duplicate 与 malformed_response。空结果不是 system_failure；未分类 0 |
| E2A.5 timeout、retry、预算 | PASS | timeout 后成功：logical=1 physical=2 retry=1；耗尽 retry physical=3；非法动作 physical=0；超预算不发第二次物理请求 |
| E2A.6 recorded I/O replay | PASS | 23 条脱敏记录；关闭网络 replay 空结果 case，状态/计数/state_id 一致率 100% |
| E2A.7 与原 PoG entity_search 语义 | PASS | SPARQL 模板与 `if head:` 分支与 `freebase_func.py` 一致；同一 bindings 上 replica 与 adapter 目标均为 `m.02hrh0_`。HTTP 客户端为 urllib，不是 SPARQLWrapper |

## 6. 指标

| 指标 | 目标 | 实际 | 是否通过 |
|---|---:|---:|---|
| E2A.1–E2A.7 通过率 | 100% | 100% | 是 |
| 真实 LLM 调用数 | 0 | 0 | 是 |
| memory 读/写次数 | 0 | 0 | 是 |
| Oracle/test 标签进入 Action view | 0 | 0 | 是 |
| HEAD/TAIL 映射正确率 | 100% | 100% | 是 |
| 合法状态转移正确率 | 100% | 100% | 是 |
| logical/physical/retry 计数正确率 | 100% | 100% | 是 |
| 预算边界处理正确率 | 100% | 100% | 是 |
| recorded I/O replay 一致率 | 100% | 100% | 是 |
| secret 写入产物次数 | 0 | 0 | 是 |
| 评测集用于轨迹生成次数 | 0 | 0 | 是 |
| 固定评测文件 hash 变化数 | 0 | 0 | 是 |
| 原 PoG 基线非预注册变化 | 0 | 0 | 是 |
| 未分类异常数 | 0 | 0 | 是 |

## 7. 数据、exclusion 与暴露

固定评测文件只校验哈希，未用于生成 live 轨迹。

| 文件 | n | SHA-256 |
|---|---:|---|
| `artifacts/datasets/webqsp_smoke_20.jsonl` | 20 | `e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393` |
| `artifacts/datasets/webqsp_model_compare_150.jsonl` | 150 | `37276867bb297991e83c335a6d4bb4f5657642fae2c77fb16eeac56eb310628c` |
| `artifacts/datasets/cwq_model_compare_50.jsonl` | 50 | `fa5f957de02ac804253d722fc1cc1a22652450a0480a1b5b4bd582ab4c5cb25b` |

正式 exclusion 仍为 220。开发任务 `m.02mjmr` 与 exclusion 的 topic/answer MID 有重叠，已写入 `artifacts/registries/sp2a_development_exposure_registry_v1.json`，后续 memory discovery 必须排除；本轮未用评测题生成轨迹。

## 8. 原 PoG 基线

实验前后哈希与 SP0/SP1 登记一致，文件未被修改：

| 文件 | SHA-256 |
|---|---|
| `main_freebase.py` | `5e7e19083ffc774ee725a16686d28b519f67ab0fe05022062dc0c172bd3a5e16` |
| `freebase_func.py` | `b2e780762def28f0c68d71d9b41e61c9c015e7511810415a17a96169464ba31c` |
| `utils.py` | `78bdcccd2f5dc41fff61c04a947a52c0e9881eb91e67d6d45230ec0b717ddc81` |
| `prompt_list.py` | `2dfe1bd1ba8e3a978114d215f22da97a485251dba4219ddf6001eaa54b304841` |
| `data_split.py` | `f8087dacf4c70ad2461444b83d32d1f3879596ca135314d51058b303f1b9156f` |
| `pog_w.sh` | `952a06dd87836b760b93af71ada312b2cde8cfa0ab524032f5d4e41a2f814452` |

`pog_w.sh` 含密钥，只在原地哈希，不复制进 run 产物或本报告正文。

## 9. 失败分类与异常

| 情况 | 处理 |
|---|---|
| 非法未枚举关系 | `action_space_failure`，物理 KG 请求 0 |
| 合法空 EXPAND | 成功，结果为空，不计 depth |
| timeout / malformed / endpoint failure | `system_failure`，保留每次物理请求 |
| 预算不足 | `budget_insufficient`，执行前拒绝且不再发物理请求 |

未分类异常数 0。无 INVALID run。

## 10. 产物索引

全部位于 `self-play/`：

```text
self-play/src/sp_memory/kg_sparql.py
self-play/src/sp_memory/live_environment.py
self-play/src/sp_memory/budget_ledger.py
self-play/src/sp_memory/recorded_io.py
self-play/src/sp_memory/sp2a_guards.py
self-play/src/sp_memory/sp2a_checks.py
self-play/configs/sp2a_live_kg_v1.json
self-play/scripts/run_sp2a_checks.py
self-play/tests/test_sp2a_live_kg.py
self-play/tests/fixtures/sp2a/fault_responses.json
self-play/artifacts/registries/sp2a_development_task_registry_v1.json
self-play/artifacts/registries/sp2a_development_exposure_registry_v1.json
self-play/artifacts/recorded_io/sp2a/sp2a_recorded_io_v1.json
self-play/artifacts/protocol/sp2a_check_result.json
self-play/runs/sp2a-20260822T082704Z-28a5bc97/
self-play/reports/sp2a/            # 本报告目录
```

未向 `data/`、`cope_alias/`、`PoG/` 写入。recorded I/O bundle SHA-256 `5137e8d97d14d7a91742827a7d4a2ddaea80cc235c5c3fb67006f3d59d1498b5`。

## 11. 未解决风险

| 风险 | 状态 | 后续 |
|---|---|---|
| 登记 Honolulu MID `m.02hrh` 与 live Obama 出生地 `m.02hrh0_` 不一致 | 已知；TAIL/两跳第二跳因此为空 | SP2-B 若依赖具体 MID 须按 live 结果重登开发任务，不得改评测集 |
| HTTP 客户端是 urllib 不是 SPARQLWrapper | 查询文本与前缀剥离一致，传输库不同 | SP2-B 若遇格式差异再对照 |
| `BACKTRACK(state)` 仍 unsupported | SP1 已标 | 后续单独计划 |
| 工作树 dirty | 以本报告与 run 哈希为准 | 全阶段 |
| `pog_w.sh` 含 API key | 未复制 | 全阶段 |
| `m.02mjmr` 与 exclusion 重叠 | 已暴露登记 | 不得进入 memory discovery |

## 12. 验收与后续边界

SP2-A 判定 **PASS**。阶段收口完成。不生成 SP2-B 计划。SP2-A 证明了预制合法动作下的 live KG 查询、方向、规范化、状态转移、异常分类、计数、预算和 recorded I/O replay。这不是 LLM 推理基线，也不是 memory 增强证据。

若后续决定启动下一步，应是 SP2-B：无 memory 的 LLM+KG 端到端 rollout。在此之前仍不得调用 LLM 做关系/答案决策，不得注入 memory，也不得运行 150/50 效果对比。

本实验与 `experiment_log_kg_memory.md` 中的 V2-5 Self-Play 不是同一条线。SP2-A 只完成 `self-play/` 真实 KG 环境验证，不改变 V2 reflection 的 `GATE_HOLD`。
