# SP3 实验报告：Self-Play 候选经验发现与生成

> 报告目录：`self-play/reports/sp3/`  
> 计划版本：SP3-PLAN 1.0  
> 协议版本：`sp-protocol-v1`  
> 总体要求：运行时 SP-GENERAL 1.17（收口登记后升级为 1.18）  
> 验收结论：**PASS**  
> 报告日期：2026-08-23  
> 报告 SHA-256：见同目录 `metrics.json` 的 `report_sha256`

本报告只覆盖独立 `self-play/` 实验的 SP3：**在独立 discovery 数据上生成可审计、可重放的 `candidate_experience`**。结论不是 KGQA EM/F1，不是 memory 增益，也不是正式 promotion。候选经验在本阶段只写不读，不得注入 Explorer。不得把本报告写成 V2-5 Self-Play，不得把 G2 teacher 写成 G1 O0 Self-Play。

## 1. 研究目标

回答计划中的四个问题：在不让 Oracle 指导在线 Explorer/Critic 的条件下，能否形成完整 Self-Play 轨迹；O0 Critic 能否产出可记录纠错；经验能否去实体化；O1–O3 离线 teacher 是否能加速发现。本阶段不进入 SP4。

## 2. 计划、协议与配置

| 项 | 值 |
|---|---|
| 计划 | `exp_plan/05_SP3_candidate_experience_discovery.md` SP3-PLAN 1.0 |
| 协议 | `sp-protocol-v1` |
| 运行时 overall | SP-GENERAL 1.17 |
| 配置 | `configs/sp3_candidate_discovery_v1.json` SHA-256 `20cc5dbe0a0cf00f5f388d7d856f5fe52e7add030eef93c17f9e60606e7ba720` |
| Explorer prompt | `prompts/sp3_explorer_v1.json` SHA-256 `fa20f2dc8d94822120f30ec57935b1d17c423932d3280f8367c91e5e6be94c32` |
| O0 Critic prompt | `prompts/sp3_critic_o0_v1.txt` SHA-256 `6a046d32eb5c14fde01075aaa9f7ac344e9d224df529e28cf98c441fe9635f84` |
| teacher prompt | `prompts/sp3_critic_teacher_v1.txt` SHA-256 `fe698a9d8419b9e4c31ef08525321406670c48302a7a8bedc77fd4c7e11015b8` |
| discovery manifest | hash `5ecd9719b781d3aa2bcae4eef9d6f7d5cb654ed88b9dc2a03576a48aa017381f` |
| D0 / D1 / H | n=12 / 60 / 20；文件 SHA-256 `c1b17f94…` / `c77261eb…` / `fb250702…` |
| exclusion registry | `dc7fe94274fc6f5f2fc98f7966bd17719841b83ad0d56a7da8167969edcb85db` |
| 前置 SP2-B | `sp2b-20260822T131350Z-b70a898b`；报告 SHA-256 `4ad722f64668af9b4de38ea474857fa8ebc1caf019aa3117e38fe8e5b2c4879c` |
| endpoint | `http://localhost:8890/sparql`（只读 POST） |
| LLM | `gpt-3.5-turbo-0125`，temperature 0.3/0.3 |
| 密钥 | 仅进程环境 `OPENAI_API_KEY`；未写入配置快照或本报告 |
| 预算 | depth 4 / steps 28 / kg 88 / llm 44 / critic_rounds 2 / frontier 80 |
| memory | `allow_self_play_experience_memory_read=false`；`allow_candidate_injection=false`；candidate 只写 |
| seed | 20260822 |

未使用冻结 WebQSP 20/150 或 CWQ 50 生成经验。

## 3. 有效运行

| 项 | 值 |
|---|---|
| preflight | `sp3-20260822T144305Z-bb131774`（无 LLM/KG） |
| D0 | `sp3-20260822T145537Z-0c4deb09`；14:55:37Z–14:57:25Z；SUCCESS |
| D1 G0–G3 | `sp3-20260822T160827Z-941c23fe`；16:08:27Z–16:43:37Z；SUCCESS |
| holdout | `sp3-20260822T164407Z-ce2cf6e6`；16:44:07Z–16:47:12Z；SUCCESS |
| 无效 Run | 首次 D0 因环境无 API key 退出 2，未写 live 轨迹；随后 D0 成功，不覆盖 |
| Git | `75d660f61701da82ff554254209745c8834f6c7f` dirty |
| 单元测试 | 89 通过 / 0 失败 / 0 skip（preflight） |

Holdout 使用独立 run，避免覆盖 D1 的 `sp3_check_result.json`。候选库是全局 JSONL，holdout 只读观察、不注入。

## 4. 实验设置

- 包装原 PoG adapter stage `sp2b`；不改 `main_freebase.py` 等基线文件。
- Explorer 与在线 Critic 只读 `public_task_view`（O0）。JSONL 中的 Oracle 字段仅供 Verifier / 离线 teacher。
- G0：Explorer-only。G1：O0 Critic。G2：对 G0 轨迹的 `oracle_guided_offline_teacher`（O1–O3，禁止 O4）。G3：随机合法动作，无额外 Critic LLM。
- Candidate store 去实体化、schema 与泄漏审计；`counterfactual_status=deferred_to_sp4`。
- Replay：KG 用 recorded I/O；LLM 用缓存。

## 5. 分层结果（不合并为 EM/F1）

### 5.1 D0 Explorer-only（n=12）

轨迹完整率 / replay / pipeline_ok = 1.0；未分类 0。失败：`answer_extraction_failure` 5、`budget_insufficient` 2、无失败 5。真实 LLM 60。未提取候选。

### 5.2 D1 G0 Explorer-only（n=60）

完整率 / replay / pipeline_ok = 1.0；未分类 0。失败：`answer_extraction_failure` 32、`budget_insufficient` 1、无失败 27。真实 LLM 336；mean 5.6。候选 0。

### 5.3 D1 G1 O0 Critic（n=60）

完整率 / replay = 1.0；未分类 0；pipeline_ok 0.483。失败：`system_failure` 31、`none` 18、`critic_recovery_failure` 6、`budget_insufficient` 4、`answer_extraction_failure` 1。真实 LLM 410；mean 6.83。接受候选 **24** 条，来自 **24** 个 task；触发阶段均为 `relation_selection`；失败类型覆盖 `explorer_failure` 与 `answer_extraction_failure`。

31 条 `system_failure` 均为已分类的 `protocol_error` / `SCHEMA_ERROR`：O0 Critic 请求超出 `gpt-3.5-turbo-0125` 16k 上下文。轨迹仍完整可 replay，非法 KG 为 0。这是发现流程限制，不是未分类异常，也不是 Oracle 泄漏。

G0→G1 配对恢复：G0 失败 33 题中恢复 2 题（`WebQTest-618`、`WebQTest-502`），恢复率 0.061。不作为准确率提升证据。

### 5.4 D1 G2 offline teacher（n=60）

标注 `oracle_guided_offline_teacher`，**不是** O0 Self-Play。真实 teacher LLM 60；接受 38，拒绝 22。候选覆盖 `relation_selection` 25、`answer_submission` 8、`continue_stop` 5。离线反馈 `artifacts/feedback/sp3_o1_o2_o3_feedback_v1.jsonl` SHA-256 `e153b6c01c06aa30c4e817e8a54e3fdc211e305244e450e98835f08391bac523`。

增量成本：G1 约 17.1 LLM/候选（410/24）；G2 增量约 1.58 LLM/候选（60/38）。G2 含 G0 探索成本则为 (336+60)/38 ≈ 10.4。G2 发现更快、更便宜，不得写成 G1 能力。

### 5.5 D1 G3 随机合法动作（n=60）

完整率 / replay / pipeline_ok = 1.0；未分类 0。失败：`critic_recovery_failure` 31、`none` 25、`budget_insufficient` 3、`explorer_failure` 1。真实 LLM 425；mean 7.08。接受候选 **57** 条（全部 `random_critic` / `relation_selection` / `explorer_failure`）。

G3 候选数高于 G1，说明“多一次干预就能写候选”本身会抬高条数。G3 用作对照，不与 G1 合并。

### 5.6 Holdout（n=20）

Explorer-only；完整率 / replay = 1.0；未分类 0。失败：`none` 12、`answer_extraction_failure` 8。真实 LLM 108。`promotion=false`。`candidate_trigger_rate=1.0` 仅按 `question_type` 是否已有候选匹配，**不是**注入后的效果，也不能当作泛化增益。

## 6. 候选经验池

| 项 | 值 |
|---|---|
| 条数 | 119 |
| 方法 | random_critic 57 / teacher 38 / o0_critic 24 |
| 支持 task | 60（D1 全覆盖；G1 独有 24） |
| 决策阶段 | `relation_selection`、`continue_stop`、`answer_submission` |
| schema / leakage | 1.0 / 1.0 |
| status | 全部 `candidate` |
| counterfactual | `deferred_to_sp4` |
| 路径 | `artifacts/candidates/sp3_candidate_experience_v1.jsonl` SHA-256 `dcead529ff32f7a5aa4c3e653dc29cee90c4e3c85f7eeb09826645a50fe6a1dd` |

无 schema 拒绝文件：本轮进入 store 的记录均通过审计；teacher 的 22 条拒绝发生在解析阶段，未写入 candidate store。Explorer 检索次数 0。

## 7. 验收指标

| 指标 | 门槛 | 实际 | 是否通过 |
|---|---|---|---|
| preflight + D0/D1/H 冻结 | 是 | 是；未抽评测集 | 是 |
| D0 完整率 / replay | 100% | 100% | 是 |
| D0 失败可分类 | 是 | 是 | 是 |
| D1 有效 rollout replay | ≥95% | G0/G1/G3 均为 100% | 是 |
| D1 未分类异常 | 0 | 0 | 是 |
| G1 候选 | ≥10 条、≥5 task、≥2 阶段或失败类型 | 24 条、24 task、2 失败类型 | 是 |
| schema / leakage | 100% | 100% | 是 |
| G0–G1 配对恢复 + G1/G2 成本 | 有记录 | 恢复率 0.061；成本见 §5.4 | 是 |
| 候选注入 / memory 读 | 0 | 0 | 是 |
| Actor/Critic O4 | 0 | 0 | 是 |
| 非法 KG | 0 | 0 | 是 |
| baseline / `data/` / `cope_alias/` | 未改 | 未改 | 是 |
| secret | 0 | 0 | 是 |
| G2 不冒充 G1 | 是 | 分列报告 | 是 |
| 阶段报告 | 本文件 | 本文件 | 是 |

## 8. 失败分类与未解决风险

已分类：`answer_extraction_failure`、`budget_insufficient`、`critic_recovery_failure`、`explorer_failure`、`system_failure`（G1 上下文超长）。未出现未分类异常。

未解决（不阻塞本阶段 PASS，进入 SP4 前必须处理）：

1. G1 Critic prompt 把可见状态塞进 16k 上下文，31/60 题变成 `system_failure`；O0 候选只覆盖 `relation_selection`。
2. G0→G1 配对恢复很低（2/33）；SP3 不要求证明准确率提升。
3. G3 随机干预写出的候选多于 G1，promotion 前必须用反事实区分。
4. Holdout `trigger_rate` 按题型粗匹配，不是注入实验。
5. `BACKTRACK(state)` 仍 unsupported；无独立 rejection JSONL（本轮无 schema 拒绝）。
6. SentenceTransformer 仍缺失。

## 9. 产物索引

```text
self-play/configs/sp3_candidate_discovery_v1.json
self-play/prompts/sp3_explorer_v1.json
self-play/prompts/sp3_critic_o0_v1.txt
self-play/prompts/sp3_critic_teacher_v1.txt
self-play/artifacts/datasets/sp3_discovery_{d0_12,d1_60,holdout_20}.jsonl
self-play/artifacts/datasets/sp3_discovery_manifest_v1.json
self-play/artifacts/registries/sp3_{discovery,exclusion,exposure}_registry_v1.json
self-play/artifacts/candidates/sp3_candidate_experience_v1.jsonl
self-play/artifacts/feedback/sp3_o1_o2_o3_feedback_v1.jsonl
self-play/runs/sp3-20260822T144305Z-bb131774/
self-play/runs/sp3-20260822T145537Z-0c4deb09/
self-play/runs/sp3-20260822T160827Z-941c23fe/
self-play/runs/sp3-20260822T164407Z-ce2cf6e6/
self-play/reports/sp3/SP3_experiment_report.md
self-play/reports/sp3/metrics.json
```

## 10. 结论

SP3 **PASS**。独立 discovery 上完成了 Explorer-only、O0 Critic、离线 teacher 与随机对照，以及 holdout 观察；轨迹可 replay，候选可审计且未注入正式推理。本阶段不证明 memory 提升 EM/F1，不 promotion，不启动 SP4，也不构成 V2-5。
