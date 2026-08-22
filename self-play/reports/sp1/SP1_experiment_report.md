# SP1 实验报告：原 PoG 决策点适配与最小环境绑定

> 报告目录：`self-play/reports/sp1/`  
> 计划版本：SP1-PLAN 1.3  
> 协议版本：`sp-protocol-v1`  
> 总体要求：SP-GENERAL 1.8（收口登记后升级为 1.9）  
> 验收结论：**PASS**  
> 报告日期：2026-08-22

本报告只覆盖 Self-Play 实验的第二步：**SP1 原 PoG 决策点适配与最小环境绑定**。本轮不调用真实 LLM，不把 live Freebase 作为验收条件，不生成或注入 memory，不报告 WebQSP 20/150 或 CWQ 50 的 KGQA 效果。

## 1. 研究目标

验证 SP0 冻结的 `sp-protocol-v1` 能否在不改变原 PoG 基线行为、不调用真实 LLM、不依赖 live Freebase、不给 Actor/Critic 泄漏 Oracle 信息的情况下，准确、确定且可重放地表达 PoG 的搜索状态和基础动作（`EXPAND`、`SELECT_FRONTIER`、`CONTINUE`、`STOP`、`ABSTAIN`）。`BACKTRACK(state)` 明确 unsupported。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/02_SP1_pog_adapter_and_environment_binding.md` SP1-PLAN 1.3 |
| 协议 | `sp-protocol-v1` |
| 配置 | `configs/sp1_adapter_v1.json` |
| 配置 SHA-256 | `955d38383901e02e4c97d3ff6d4b7830ec5dd65dca9656f7a3161a77f6164266` |
| 规范化 | `sp1-canonical-v1` / `sp1-question-normalization-v1` |
| `adapter_enabled_default` | false |
| `allow_llm` / `allow_live_kg` | false / false |
| `backtrack_state_policy` | unsupported |

## 3. 有效和无效运行

| 项 | 值 |
|---|---|
| 有效 Run | `sp1-20260822T030044Z-8cb155e0`（E1.1–E1.12 全部 PASS，退出码 0） |
| 无效 Run | `sp1-20260822T025810Z-13c293aa`（INVALID：冒烟集主题名含子串答案时 O0 审计误报，随后 `summarize_metrics` KeyError；已修复，不作为证据） |
| 预期失败 Run | `sp1-20260822T030056Z-a37d9e5a`（`--fail-fixture`，退出码 1） |
| Git commit | `81943feddfe2b2f89dc05cae457998596099a7cc` |
| 工作树 | dirty（本轮新增 self-play 适配代码与产物，未提交） |
| 单元测试 | 49 通过 / 0 失败 / 0 skip |

失败 run 写入独立目录，没有覆盖成功 run。成功 run 的 `summary.txt` 在失败 fixture 之后仍完整。

## 4. 实验设置

- 适配层默认关闭；检查项显式设置 `adapter_enabled`。
- Environment 只消费人工 fixture / recorded 形状的返回值，不发起 SPARQL。
- Actor 决策用人工 Action 或预制 reasoning 文本；`run_llm` 以 fail-fast guard 替换。
- WebQSP smoke 20 只做公共字段状态投影和 O0 泄漏检查。
- WebQSP 150 与 CWQ 50 只校验哈希并进入正式 exclusion registry。
- 不修改 `../data/`、`../cope_alias/`，也不修改原 PoG 基线文件。

相对计划的文件对应：

| 计划文件 | 实际文件 |
|---|---|
| `src/sp_memory/pog_adapter.py` | 同左，并含 snapshot 工厂与原函数 replica |
| `src/sp_memory/environment_binding.py` | 同左 |
| `src/sp_memory/answer_submission.py` | 同左 |
| `src/sp_memory/sp1_checks.py` | 同左（E1.1–E1.12） |
| — | 额外：`question_normalization.py`、`llm_guard.py`；`registry.py` 增加正式 220 条 exclusion 构建 |

SP0 的 `checks.py` E0.4 改为把 2 条 fixture 写到 `benchmark_exclusion_fixture_only_v1.json`，避免覆盖正式 registry。这不改变 SP0 验收门槛。

## 5. E1.1–E1.12 结论

| 实验 | 结论 | 要点 |
|---|---|---|
| E1.1 基线与 adapter-disabled 等价 | PASS | 6 个原 PoG 文件哈希无变化；`select_relations` / `entity_search` / `extract_reason_and_anwer` replica 与 disabled passthrough 一致 |
| E1.2 决策边界与零 LLM | PASS | 决策点地图覆盖四阶段；适配源码未调用 `run_llm` / `relation_search_prune` / `reasoning`；guard 调用数 0 |
| E1.3 VisibleState 投影 | PASS | 合法投影 100%；缺字段与 schema/Oracle 字段均拒绝 |
| E1.4 canonicalization / state_id | PASS | 同语义重复与打乱顺序一致；改 frontier 后 hash 变化 |
| E1.5 O0 泄漏 | PASS | 7/7 注入被拦；smoke 20 Actor/Critic 敏感字段 0；Verifier 20/20 可读标签且不回写 O0 |
| E1.6 EXPAND 双向映射 | PASS | HEAD/TAIL 规范三元组正确；空结果、literal、重复、`[FINISH_ID]` 过滤均正确；非法动作拒绝 |
| E1.7 continue/stop/答案提交 | PASS | 已观察 ID/名称/literal 可构造 STOP；未观察、歧义、空答案、malformed 均分类失败 |
| E1.8 recovery / backtrack | PASS | 历史实体映射为 `SELECT_FRONTIER`；`BACKTRACK(state)` 误成功 0，拒绝后状态和预算不变 |
| E1.9 预算 | PASS | 枚举 kg+2 且不计 step；成功 EXPAND step/kg/depth +1；空 EXPAND 不加 depth；超限未执行；LLM/Critic 计数为 0 |
| E1.10 Environment 分类 | PASS | 空结果与 timeout/malformed/未知异常分离；未分类异常 0 |
| E1.11 fixture 审计与 replay | PASS | 7 条 hand fixture 来源完整；各 replay 3 次一致；Oracle fixture 未进入 Actor 用途 |
| E1.12 固定数据、exclusion、一键复现 | PASS | 20/150/50 哈希不变；normalization 向量 8/8；正式 registry 220 且重建 hash 一致；2 条 SP0 fixture 未混入；隔离失败 fixture 按预期失败 |

## 6. 指标

| 指标 | 目标 | 实际 | 是否通过 |
|---|---:|---:|---|
| E1.1–E1.12 通过率 | 100% | 100% | 是 |
| 非预注册基线 hash 变化数 | 0 | 0 | 是 |
| adapter-disabled 行为等价率 | 100% | 100% | 是 |
| 真实 LLM 调用数 | 0 | 0 | 是 |
| SP1 正式 live KG 调用数 | 0 | 0 | 是 |
| O0 泄漏检测率 | 100% | 100% | 是 |
| WebQSP smoke Actor/Critic 敏感字段数 | 0 | 0 | 是 |
| HEAD/TAIL 映射正确率 | 100% | 100% | 是 |
| 方向反转数 | 0 | 0 | 是 |
| 未观察或歧义 STOP 接受数 | 0 | 0 | 是 |
| unsupported backtrack 误成功数 | 0 | 0 | 是 |
| budget delta 正确率 | 100% | 100% | 是 |
| 未分类异常数 | 0 | 0 | 是 |
| fixture replay 一致率 | 100% | 100% | 是 |
| question normalization vectors 通过率 | 100% | 100% | 是 |
| exclusion registry 记录数 | 220 | 220 | 是 |
| 固定评测文件 hash 变化数 | 0 | 0 | 是 |

## 7. 数据与 exclusion registry

固定评测文件哈希与 SP0 冻结值一致，未重抽。

| 文件 | n | SHA-256 |
|---|---:|---|
| `artifacts/datasets/webqsp_smoke_20.jsonl` | 20 | `e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393` |
| `artifacts/datasets/webqsp_model_compare_150.jsonl` | 150 | `37276867bb297991e83c335a6d4bb4f5657642fae2c77fb16eeac56eb310628c` |
| `artifacts/datasets/cwq_model_compare_50.jsonl` | 50 | `fa5f957de02ac804253d722fc1cc1a22652450a0480a1b5b4bd582ab4c5cb25b` |
| `eval_set_manifest_v1.json` `manifest_hash` | — | `f6dd56a5b9a2937ad5e1964a25570a410e9be8720254551c78ca7f69e28226be` |

正式 exclusion：`artifacts/registries/benchmark_exclusion_registry_v1.json`

- `record_scope=formal_benchmark`
- `question_normalization_version=sp1-question-normalization-v1`
- `count=220`
- `content_hash=228a3372453fabf632f88da83acfa3e371411572d7bc9dbdfd6947dc0f80062f`
- 文件 SHA-256：`a756b5204282cb65235ed7e5204ee109b76fd5dff0acd9eea96a7ddb7c2f48e9`

SP0 的 2 条 fixture 保留在 `tests/fixtures/exclusion_records_sp0.json`，不计入 220。CWQ 50 仍是含 `WebQTrn` / `WebQTest` ID 的实验对比子集，不称为标准 CWQ test benchmark。

## 8. 原 PoG 基线

实验前后哈希与 SP0 登记一致，文件未被修改：

| 文件 | SHA-256 |
|---|---|
| `main_freebase.py` | `5e7e19083ffc774ee725a16686d28b519f67ab0fe05022062dc0c172bd3a5e16` |
| `freebase_func.py` | `b2e780762def28f0c68d71d9b41e61c9c015e7511810415a17a96169464ba31c` |
| `utils.py` | `78bdcccd2f5dc41fff61c04a947a52c0e9881eb91e67d6d45230ec0b717ddc81` |
| `prompt_list.py` | `2dfe1bd1ba8e3a978114d215f22da97a485251dba4219ddf6001eaa54b304841` |
| `data_split.py` | `f8087dacf4c70ad2461444b83d32d1f3879596ca135314d51058b303f1b9156f` |
| `pog_w.sh` | `952a06dd87836b760b93af71ada312b2cde8cfa0ab524032f5d4e41a2f814452` |

`pog_w.sh` 含密钥，只在原地哈希，不复制进 run 产物或本报告正文。

决策点地图：`artifacts/protocol/pog_decision_map_v1.json`（SHA-256 `a2c651db52914f5f240eb3d3df3c8a69d9d45d459177d8d4d4a185b30052bc72`）。

## 9. 失败分类与异常

| 情况 | 处理 |
|---|---|
| 不可见实体/关系、错误方向 | `action_space_failure` / 既有 `ProtocolError` |
| `BACKTRACK(state)` | `action_space_failure` + `UNSUPPORTED_BACKTRACK_STATE` |
| 合法空 EXPAND | 成功，结果为空，不计 depth |
| timeout / malformed / 未知环境异常 | `system_failure`，保存 traceback |
| 答案无法映射 | `answer_extraction_failure` |
| 预算不足 | `budget_insufficient` / `BUDGET_EXCEEDED`，执行前拒绝 |

未分类异常数 0。INVALID run 仅用于排错，不进入有效证据。

## 10. 产物索引

全部位于 `self-play/`：

```text
self-play/src/sp_memory/pog_adapter.py
self-play/src/sp_memory/environment_binding.py
self-play/src/sp_memory/answer_submission.py
self-play/src/sp_memory/sp1_checks.py
self-play/src/sp_memory/question_normalization.py
self-play/configs/sp1_adapter_v1.json
self-play/scripts/run_sp1_checks.py
self-play/tests/test_pog_adapter.py
self-play/tests/test_environment_binding.py
self-play/tests/test_answer_submission.py
self-play/tests/test_question_normalization.py
self-play/tests/fixtures/sp1/
self-play/artifacts/protocol/pog_decision_map_v1.json
self-play/artifacts/registries/benchmark_exclusion_registry_v1.json
self-play/runs/sp1-20260822T030044Z-8cb155e0/
self-play/reports/sp1/            # 本报告目录
```

未向 `data/`、`cope_alias/`、`PoG/` 写入。本轮无 `artifacts/recorded_io/sp1/`，因为没有录制 live KG I/O。

## 11. 未解决风险

| 风险 | 状态 | 后续 |
|---|---|---|
| fixture replay 不能代表真实 Freebase | 已知，不夸大 | SP2-A live KG |
| 无 recorded I/O | 已知，不阻止 PASS | SP2-A |
| 原 PoG 无真正 state backtrack | SP1 已标 unsupported | 后续单独计划 |
| CWQ 50 含混合 train/test ID | 保持冻结表述 | 正式报告时说明 |
| 工作树 dirty | 以本报告文件哈希为准 | 全阶段 |
| `pog_w.sh` 含 API key | 未复制 | 全阶段 |

O0 审计对“答案字符串是公开主题名的真子串”做了放行（例如 topic `Crimean War` 与答案 `Crimea`）。这只避免把公开字段误判为泄漏，不允许把答案作为独立字段写入 Actor/Critic。

## 12. 验收与后续边界

SP1 判定 **PASS**。阶段收口完成。不生成 SP2 计划。SP1 之后仍不得直接调用 LLM、生成 memory 或运行 150/50 效果对比。若后续决定启动下一步，应是 SP2-A：预制合法动作接入 live KG。

本实验与 `experiment_log_kg_memory.md` 中的 V2-5 Self-Play 不是同一条线。SP1 只完成 `self-play/` 协议适配，不改变 V2 reflection 的 `GATE_HOLD`。
