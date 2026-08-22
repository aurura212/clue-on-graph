# SP0 第一轮实验报告

> 报告目录：`self-play/reports/sp0/`  
> 计划版本：SP0-PLAN 1.4  
> 协议版本：`sp-protocol-v1`  
> 验收结论：**PASS**  
> 报告日期：2026-08-22

本报告只覆盖 Self-Play 实验的第一轮：**SP0 实验空间、协议与数据契约冻结**。本轮不调用 LLM，不跑 KGQA EM/F1，不生成或注入 memory。

## 1. 本轮做了什么

按照 `00_experiment_overall_requirements.md` 与 `01_SP0_protocol_workspace_and_data_contract.md`，在 `self-play/` 内实现并冻结：

1. 唯一写入空间与只读输入边界；
2. 任务 / 可见状态 / 动作 / 轨迹 / Run Manifest 数据契约；
3. Actor/Critic O0 与 Verifier O4 的可见性隔离；
4. 合法动作、预算和确定性 replay；
5. 输入 registry 与 benchmark exclusion registry 结构；
6. WebQSP/CWQ 一次性固定评测集（20 / 150 / 50）；
7. 一键检查入口 `scripts/run_sp0_checks.py`。

`self-play/` 根目录下的原 PoG 代码保持为基线，本轮未改这些文件。

## 2. 有效运行

| 项 | 值 |
|---|---|
| 有效 Run 1 | `sp0-20260821T163544Z-59a85acf`（首次构建固定评测集） |
| 有效 Run 2 | `sp0-20260821T163555Z-fec2b10a`（只校验、不重抽） |
| Git commit | `1ac662cc15ceefde6614a416183f3a8aae6d0b49` |
| 工作树 | dirty（本轮新增 self-play 实验代码与产物，未提交） |
| 配置 | `configs/sp0_protocol_v1.json` |
| 配置 SHA-256 | `2b97a232b5fa43a3dc827c63a280355ed48ee64206aeefa5a74d49f5d40618e3` |
| 单元测试 | 33 通过 / 0 失败 / 0 skip |

两次运行均退出码 0，各自拥有独立 `self-play/runs/<run_id>/` 目录和 `manifest.json`。第二次运行未改变三个固定评测文件的哈希。`python3 scripts/sample_eval_sets.py --mode build` 在冻结后返回退出码 2，拒绝重抽。

## 3. 实验结论（E0.1–E0.7）

| 实验 | 结论 | 要点 |
|---|---|---|
| E0.1 写入隔离 | PASS | 4 条合法路径可写；7 条越界路径全部拒绝；`data/`、`cope_alias/`、原 PoG 基线文件哈希无变化 |
| E0.2 Schema/动作 | PASS | 合法样例 12/12 接受；非法样例 13/13 拒绝，含版本冲突与 8 类 violation code |
| E0.3 Oracle 隔离 | PASS | 8/8 注入泄漏被阻断；O0 敏感字段数 0；Verifier 可判分；O4 不能当作 O1-O3 反馈 |
| E0.4 Registry | PASS | 连续两次 registry 内容哈希一致；源哈希变化可发现；exclusion 重复项被拒绝 |
| E0.5 Replay | PASS | 5 个 fixture 各重复 3 次，同输入一致率 100%；改动作/snapshot/预算后 hash 变化 |
| E0.6 评测冻结 | PASS | 20/150/50 数量准确；WebQSP smoke 与 model-compare 无重叠；重复检查不改文件 |
| E0.7 一键复现 | PASS | 成功运行退出 0；缺失固定集的失败 fixture 按预期失败且不覆盖成功 run |

## 4. 指标

| 指标 | 目标 | 实际 | 是否通过 |
|---|---:|---:|---|
| 越界写入拒绝率 | 100% | 100% | 是 |
| 共享输入文件哈希变化数 | 0 | 0 | 是 |
| 合法 Schema/动作接受率 | 100% | 100% | 是 |
| 非法 Schema/动作拒绝率 | 100% | 100% | 是 |
| Oracle 泄漏检测率 | 100% | 100% | 是 |
| O0 view 敏感字段数 | 0 | 0 | 是 |
| 同输入 replay 一致率 | 100% | 100% | 是 |
| Registry 重建一致率 | 100% | 100% | 是 |
| 关键检查通过率 | 100% | 100% | 是 |
| 未分类异常数 | 0 | 0 | 是 |
| WebQSP 冒烟样本量 | 20 | 20 | 是 |
| WebQSP 模型对比样本量 | 150 | 150 | 是 |
| CWQ 模型对比样本量 | 50 | 50 | 是 |

覆盖说明：非法动作至少覆盖 `INVISIBLE_ENTITY`、`INVISIBLE_RELATION`、`INVALID_DIRECTION`、`INVALID_BACKTRACK_TARGET`、`UNOBSERVED_ANSWER`、`BUDGET_EXCEEDED`、`SCHEMA_VERSION_MISMATCH`、`INVALID_ABSTAIN_REASON`。泄漏注入覆盖顶层字段、嵌套 metadata、答案 ID/文本、logical query、future neighbors，以及把 O4 当作离线反馈。

## 5. 冻结评测数据

抽样方法：对各自源文件按 task ID 排序后，用 seed `20260821` 无放回 shuffle，再切片。WebQSP smoke 取 `[0:20]`，WebQSP model-compare 取 `[20:170]`，二者不相交。CWQ 从独立总体取 `[0:50]`。后续运行只校验这些文件，不得重抽。

| 固定数据集 | n | 用途 | 文件 SHA-256 |
|---|---:|---|---|
| `artifacts/datasets/webqsp_smoke_20.jsonl` | 20 | 仅冒烟测试 | `e8e6c393fecffcca9063b036c4802f50f0a86b0e0d1c219f50ca061e67585393` |
| `artifacts/datasets/webqsp_model_compare_150.jsonl` | 150 | 不同模型/基线/memory 对照 | `37276867bb297991e83c335a6d4bb4f5657642fae2c77fb16eeac56eb310628c` |
| `artifacts/datasets/cwq_model_compare_50.jsonl` | 50 | 不同模型/基线/memory 对照 | `fa5f957de02ac804253d722fc1cc1a22652450a0480a1b5b4bd582ab4c5cb25b` |

源文件：

- `data/WebQSP.json` SHA-256 `3057f5b9cbdaf8580b0e971fbbf78000b4905670c92e5b6a38c9a59750bcf0d1`
- `data/cwq.json` SHA-256 `147e7e1ee5f73c1d9ceba7a031c23b27a1ecddd57c55efff4541f74da41e97df`

Manifest：`artifacts/datasets/eval_set_manifest_v1.json`，`manifest_hash=f6dd56a5b9a2937ad5e1964a25570a410e9be8720254551c78ca7f69e28226be`。

这些固定集含评测标签（答案字段），只供后续 Verifier / 评测脚本读取。正式 Explorer/Critic 必须处于 O0，不能把这些标签送进模型上下文。WebQSP 与 CWQ 必须分别统计，不得合并成单一指标。20 条冒烟集不得替代 150/50 对照集。

## 6. 原 PoG 基线

登记文件（实验前后哈希一致）：

| 文件 | SHA-256 |
|---|---|
| `main_freebase.py` | `5e7e19083ffc774ee725a16686d28b519f67ab0fe05022062dc0c172bd3a5e16` |
| `freebase_func.py` | `b2e780762def28f0c68d71d9b41e61c9c015e7511810415a17a96169464ba31c` |
| `utils.py` | `78bdcccd2f5dc41fff61c04a947a52c0e9881eb91e67d6d45230ec0b717ddc81` |
| `prompt_list.py` | `2dfe1bd1ba8e3a978114d215f22da97a485251dba4219ddf6001eaa54b304841` |
| `data_split.py` | `f8087dacf4c70ad2461444b83d32d1f3879596ca135314d51058b303f1b9156f` |
| `pog_w.sh` | `952a06dd87836b760b93af71ada312b2cde8cfa0ab524032f5d4e41a2f814452` |

后续 memory 计划接入的决策点：relation selection（`relation_search_prune`）、continue/stop（`reasoning`）、backtrack/recovery（`if_finish_list` / `add_pre_info`）。接口形态预定为适配层 + prompt/action score，SP0 尚未实现。

`pog_w.sh` 含密钥，只在原地哈希，不复制进 run 产物或本报告正文。

## 7. 产物位置

全部位于 `self-play/`：

```text
self-play/src/sp_memory/          # 协议实现
self-play/configs/sp0_protocol_v1.json
self-play/scripts/run_sp0_checks.py
self-play/artifacts/registries/
self-play/artifacts/datasets/
self-play/artifacts/protocol/
self-play/runs/sp0-20260821T163544Z-59a85acf/
self-play/runs/sp0-20260821T163555Z-fec2b10a/
self-play/reports/sp0/            # 本报告目录
```

未向 `data/`、`cope_alias/`、`PoG/` 写入。输入 registry 对已登记的 10 个只读文件做现场复哈希，mismatch = 0。

## 8. 相对计划的文件名调整

计划中的职责均已落地。额外拆出 `errors.py`、`hashing.py`、`config.py`、`registry.py`、`baseline.py`、`checks.py`，避免单文件过长。这些调整不影响协议字段和验收门槛。

## 9. 问题与限制

1. Replay 环境是人工 fixture 图，不是 Freebase。它只证明协议可重放，不能当作未来 memory 证据。
2. Exclusion registry 目前只用 fixture 验证结构，尚未完成正式 benchmark 隔离名单。
3. 固定评测集已冻结，但本轮没有跑任何模型。
4. 工作树 dirty：本轮代码和产物尚未提交。复现时以本报告中的文件哈希为准。
5. 本实验与 `experiment_log_kg_memory.md` 中的 V2-5 Self-Play 不是同一条线。SP0 只冻结 `self-play/` 协议，不改变 V2 reflection 的 `GATE_HOLD`。

## 10. 验收与下一步

SP0 判定 **PASS**。允许准备下一步计划，不允许在未登记下一步计划的情况下注入 memory 或跑 20/150/50 效果评测。

下一步计划文件：`self-play/exp_plan/02_SP1_pog_adapter_and_environment_binding.md`。SP1 目标是把 `sp-protocol-v1` 接到原 PoG 的三个决策点，并做最小环境绑定；仍不注入 memory，仍不跑模型对比。
