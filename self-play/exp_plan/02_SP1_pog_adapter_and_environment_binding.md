# SP1：原 PoG 决策点适配与最小环境绑定

> 文档编号：SP1-PLAN  
> 版本：1.0  
> 制定日期：2026-08-22  
> 状态：计划已建立，待实施  
> 前置必读：00_experiment_overall_requirements.md、01_SP0_protocol_workspace_and_data_contract.md  
> 前置结论：SP0 PASS（协议 `sp-protocol-v1`，固定评测集已冻结）

## 1. 本步骤定位

SP1 把已经冻结的 `sp-protocol-v1` 接到 `self-play/` 下现有原 PoG 基线，建立决策点适配层和最小 Environment 绑定。本步骤仍不以提高 KGQA 准确率为目标。

SP1 通过前，不得：向原 PoG 注入 memory；运行 20/150/50 模型对比；生成正式规模 synthetic discovery 数据；调用 LLM 做 Explorer/Critic rollout。

## 2. 具体目标

1. 在不修改评测数字口径的前提下，标明原 PoG 三个决策点与协议动作的对应关系：relation selection、continue/stop、backtrack/recovery。
2. 实现适配层，能把原 PoG 运行中的可见实体、可见关系、已观察三元组和预算，投影为 `VisibleState`。
3. 实现最小 Environment 绑定：把协议 `EXPAND` 映射到现有 KG 查询函数的输入输出形状，但 SP1 正式检查仍以 fixture 或录制回放为主，不把 Freebase 可用性当作验收门槛。
4. 保持 Actor/Critic 为 O0；Oracle 字段不得进入适配层输出。
5. 所有新增代码和产物仍只写入 `self-play/`。
6. 复用 SP0 冻结的评测集和 manifest；不得重抽、补题或覆盖。

## 3. 本步骤明确不做

- 不调用 LLM；
- 不生成或 promotion memory；
- 不跑 WebQSP 20/150 或 CWQ 50 的效果评测；
- 不修改 `../data/`、`../cope_alias/`；
- 原则上不修改原 PoG 基线文件。若必须改 `main_freebase.py` / `freebase_func.py` / `utils.py`，须在本文件列出最小补丁范围、理由、验证和回退方式，并保持基线行为默认关闭适配层。

## 4. 预期产物

~~~text
self-play/
├── src/sp_memory/
│   ├── pog_adapter.py
│   └── environment_binding.py
├── configs/sp1_adapter_v1.json
├── scripts/run_sp1_checks.py
├── tests/test_pog_adapter.py
├── artifacts/protocol/
└── reports/sp1/
~~~

## 5. 需要实现的代码功能

### 5.1 决策点地图

记录并测试：

| 协议决策 | 原 PoG 位置 | 适配输出 |
|---|---|---|
| relation selection | `freebase_func.py:relation_search_prune` | 可见关系候选上的 `EXPAND` / 评分接口 |
| continue/stop | `freebase_func.py:reasoning` 与 `main_freebase.py` stop 标志 | `CONTINUE` / `STOP` / `ABSTAIN` |
| backtrack/recovery | `utils.py:if_finish_list`、`freebase_func.py:add_pre_info` | `BACKTRACK` / `SELECT_FRONTIER` |

适配层默认关闭，不影响原 PoG 直接运行。

### 5.2 状态投影

从原 PoG 的 `topic_entity`、`ent_rel_ent_dict`、`cluster_chain_of_entities` 和 call/token 计数构造 `VisibleState`。投影后必须通过 SP0 的泄漏审计。

### 5.3 最小 Environment 绑定

定义 `EXPAND(entity, relation, direction)` 到 `entity_search` / `execurte_sparql` 形状的纯函数接口。SP1 用 fixture 或录制的确定性 I/O 验证，不要求本步骤接通 live Freebase。

### 5.4 检查入口

`run_sp1_checks.py` 必须先校验 SP0 固定评测集与 manifest 哈希，失败则停止且不得重抽。

## 6. SP1 检查实验

- **E1.1** 决策点地图完整，且默认关闭时不改变原 PoG 基线文件哈希，或经预注册的最小补丁可回退。
- **E1.2** 用 fixture 投影出的 ActorView 敏感字段为 0，VerifierView 可读取任务标签。
- **E1.3** 协议动作到原 PoG 函数参数的往返一致。
- **E1.4** 复用 SP0 冻结数据集；读取 20/150/50 文件哈希与 SP0 报告一致。

## 7. 验收门槛

1. E1.1-E1.4 全部通过；
2. 不调用 LLM，不写入 `data/` 或 `cope_alias/`；
3. O0 泄漏检测率 100%；
4. SP0 固定评测集哈希不变；
5. 本文件日志区记录实现、命令、产物和结论。

Oracle 泄漏、越界写入或重抽评测集时必须 FAIL。

## 8. 完成后的下一阶段规则

SP1 通过后仍不得直接注入 memory。必须先生成 SP2 计划并在总体文件中登记。SP2 候选方向是受控 synthetic task / Oracle witness 生成，或单决策点的离线动作记录，以当时计划为准。

## 9. 实验日志区

### 9.1 日志索引

| 日志 ID | 日期时间 | 类型 | Run ID/Commit | 状态 | 简述 |
|---|---|---|---|---|---|
| 待填写 | 待填写 | 实现/测试/变更/验收 | 待填写 | 待填写 | 待填写 |

### 9.2 SP1 最终验收记录

- **验收日期：** 待填写
- **结论：** 待判断
- **是否允许准备下一步计划：** 待判断
