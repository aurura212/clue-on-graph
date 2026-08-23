# SP4 code generation prompt (copied from SP4-PLAN 2.1 §9)

你是本项目的代码实现工程师。请在当前仓库中实现 SP4：经验资格验证、前置能力补齐、同状态反事实验证、经验蒸馏和 promotion。实验根目录是 clue_on_graph/self-play/。先阅读并遵守：
1. clue_on_graph/self-play/exp_plan/00_experiment_overall_requirements.md
2. clue_on_graph/self-play/exp_plan/01_SP0_protocol_workspace_and_data_contract.md
3. clue_on_graph/self-play/exp_plan/02_SP1_pog_adapter_and_environment_binding.md
4. clue_on_graph/self-play/exp_plan/03_SP2A_live_kg_environment_validation.md
5. clue_on_graph/self-play/exp_plan/04_SP2B_llm_kg_baseline_rollout.md
6. clue_on_graph/self-play/exp_plan/05_SP3_candidate_experience_discovery.md
7. clue_on_graph/self-play/exp_plan/06_SP4_precondition_counterfactual_distillation_promotion_v2.md

你的任务是实现代码，不是改写实验结论，也不是直接运行正式 WebQSP/CWQ 评测。开始编码前必须检查现有目录、入口脚本、配置格式、数据 schema、动作协议、Verifier、KG adapter、日志和测试；优先复用现有接口，只有确实缺失时才新增最小兼容层。所有新增或修改的实验代码必须位于 clue_on_graph/self-play/ 下，并保持现有原 PoG 行为不变。

【总体功能】
实现一个由以下阶段组成的、可单独运行和可重放的 SP4 pipeline：
A. preflight：检查代码/配置/prompt/数据/registry 的 hash，检查目录写边界、schema round-trip、Oracle projection、secret scan 和 deterministic replay；
B. synthetic task generator：在固定 KG snapshot 上采样 source entity 和 1–4 跳路径，执行路径得到可判定答案，生成不泄漏 relation ID、答案名、显式路径和 future state 的任务，并建立 discovery/validation/holdout split、manifest 和 exposure registry；
C. multi-trajectory/Critic runner：对 discovery task 运行 actor-only 和多 seed 探索轨迹，在 STOP 失败、无新 frontier、重复状态、预算临界和 branching 激增时触发 O0 Critic，最多进行两轮 correction，并保存成功、失败、恢复、预算耗尽和协议失败证据；
D. action/backtrack validator：解析、校验、执行和 replay relation selection、continue、stop、backtrack，backtrack 只能指向当前可见 frontier 或已观察状态；
E. candidate audit/injection：审计 SP3 候选的 schema、来源、隐私、Oracle 泄漏和 replay；在 memory_read=false 时完全不读候选，在开启时只检索 eligible 候选，并对注入前后做 secret scan；
F. same-state counterfactual runner：固定同一初始状态、相同预算和相同环境，比较原始动作、候选动作、随机合法动作，必要时支持 sham/irrelevant 对照，输出可比较的 win/tie/harm/invalid 和成本证据；
G. held-out validator：在冻结的 SP4-V1 验证原始候选，在冻结的 SP4-V2 验证蒸馏规则，统计 entity/task/paraphrase/path-signature held-out 泛化；
H. distiller：只对通过 schema、privacy、leakage、replay 且具有反事实证据的候选生成实体无关、答案无关、路径无关的抽象规则；
I. promotion evaluator：根据 SP4 计划中的固定门槛判定 promoted_memory、validated_candidate、rejected_harmful 或 deferred，并生成只读 memory manifest 和 SHA-256；
J. report/metrics/replay：为每个 run 保存配置、输入、状态、动作、输出、错误、seed、预算、模型、endpoint 和 hash，输出 JSONL/JSON/Markdown 产物，失败时结构化退出。

【必须实现的模块职责】
请按现有项目风格拆分模块；可以使用不同文件名，但最终必须有清晰对应关系：
- `synthetic_tasks`：固定 snapshot 校验、实体/路径采样、路径执行、答案计算、verbalizer、去重、歧义过滤、难度分层、split 和 exposure registry；
- `critic_runner`：上下文压缩、O0 public-state 投影、结构化 Critic 调用、schema fallback、timeout/retry、failure classification、correction budget；
- `action_protocol`：动作 schema、合法性校验、backtrack 可见性校验、state hash、replay；
- `candidate_audit`：候选 schema/source/privacy/leakage/replay 检查、拒绝原因和审计报告；
- `candidate_retrieval`：按 decision stage、question intent、answer type、state signature、动作兼容性和 budget/progress 检索，处理空匹配、冲突匹配和低置信 fallback；
- `counterfactual_runner`：状态克隆或等价恢复、CF0/CF1/CF2 配对执行、随机合法动作采样、sham 对照、结果归因和成本统计；
- `distiller`：从多条候选/轨迹/反事实证据抽取规则，删除或泛化实体、答案、ID、完整问题、witness、gold path、future state、O4 和单题常量；
- `promotion`：固定阈值判定、分层统计、conflict/harm 检查、状态分类、不可变输出和 manifest；
- `audit_and_io`：write-boundary、secret scan、JSONL 原子写入、文件 hash、run metadata、NOT_GENERATED 记录。

【不可违反的实验约束】
1. 不得读取或使用以下 benchmark 数据来生成候选、蒸馏规则、调 prompt、调阈值、调 promotion 门槛或挑选结果：
   - artifacts/datasets/webqsp_smoke_20.jsonl
   - artifacts/datasets/webqsp_model_compare_150.jsonl
   - artifacts/datasets/cwq_model_compare_50.jsonl
2. SP4-SYN、SP4-CF、SP4-V1、SP4-V2 必须有固定 manifest、seed、题目 hash、source/answer entity hash、path signature、exposure registry 和 SHA-256；V1/V2 不得参与候选生成。
3. ActorView 与隐藏 Oracle/witness 必须物理分离。O0 Critic 只能读 public state；G2 必须标为 offline teacher，G3 必须标为 random negative control，不能把它们并入 O0 Self-Play 结论。
4. `memory_read=false` 时不得读取、解析或缓存候选；candidate injection 不得新增原 PoG 不允许的 relation、entity、答案或事实。
5. 不得把答案、relation ID、显式 gold path、witness、future state、O4 字段、secret 或完整题目常量写入候选规则。
6. 不得删除失败样本、静默覆盖旧产物、伪造空成功文件或在失败后继续执行依赖该产物的阶段；缺失产物必须记录 `NOT_GENERATED` 和原因。
7. 默认 fail-closed：配置缺失、schema 错误、hash 不匹配、split 污染、越界写入、secret scan 命中或 replay 不一致时，返回结构化错误并停止相关阶段。
8. 不得改变原 PoG 的 KG 查询、动作合法性校验、Verifier 或题内工作记忆；Self-Play 只能通过受控读取、匹配、提示注入或动作评分影响决策接口。

【关键数据契约】
所有 JSON/JSONL 记录必须带有版本字段和最小可追溯字段。至少定义并校验：
- `task`：task_id、split、snapshot_id/hash、source_entity_hash、answer_entity_hash、path_signature、question_hash、difficulty、oracle_level；
- `trajectory`：run_id、task_id、seed、temperature、stage、initial/final_state_hash、actions、failure_type、critic_source、recovery_status、budget、replay_status；
- `candidate`：candidate_id、source_trace_hash、decision_stage、abstract_state、recommended_action 或 forbidden_action、preconditions、negative_constraints、evidence_refs、privacy/leakage/replay status；
- `counterfactual`：pair_id、state_hash、budget、CF0/CF1/CF2 action/result、outcome、cost、invalid_reason、new_relevant_triples、control_type；
- `memory_rule`：rule_id、rule_version、decision_stage、abstract_state、action policy、applicability、support counts、CF/validation statistics、source hashes、status。
字段名如与仓库既有 schema 冲突，必须写 adapter 或 schema version mapping，不能悄悄改变旧数据含义。

【反事实和 promotion 的固定逻辑】
- CF0 是原始动作，CF1 是候选动作，CF2 是随机合法动作；三者必须在同状态、同预算、同环境条件下比较。
- 至少记录 success/local progress/new relevant triples/invalid expansion/loop/early stop/over-continue/cost，并能区分 win、tie、harm、invalid。
- promotion 至少检查：审计通过率 100%；来自至少 3 个独立 discovery task；至少 5 个有效反事实状态；`win_rate - harm_rate >= 0.20`；`harm_rate <= 0.10`；invalid 不高于基线；V1 至少触发 5 个 task；V1 成功率不低于基线，或持平且平均搜索成本下降至少 10%；收益不由单一实体/题型/异常样本主导；G3/sham 不得达到同等或更高收益；memory、prompt、检索阈值和 promotion 配置已冻结。
- 未通过但证据部分充分的规则输出 `validated_candidate`；稳定有害输出 `rejected_harmful`；证据不足输出 `deferred`；所有状态都必须保留原因和证据引用。

【实现与测试要求】
1. 先提交实现计划和仓库审计结果，再修改代码；列出每个新增/修改文件及其职责。
2. 先实现纯函数和 schema，再实现 runner、adapter 和 CLI；避免把 LLM/KG 调用写死在业务逻辑中，使用可注入接口和 fake backend。
3. 为每个模块提供单元测试；至少覆盖：路径/任务去重、split 污染、答案/路径泄漏、Oracle projection、Critic schema fallback、backtrack 越界、memory_read=false、候选冲突 fallback、同状态 replay、随机动作合法性、蒸馏去实体去答案、promotion 边界、hash/原子写入和失败结构化退出。
4. 提供无 LLM、无真实 KG 的 deterministic smoke test，能够在干净环境下验证端到端数据流；真实 API、真实 KG 和正式 benchmark 运行必须通过配置显式开启。
5. 为 CLI 提供 `preflight`、`generate-synthetic`、`run-critic`、`audit-candidates`、`run-counterfactual`、`distill`、`validate`、`promote`、`report` 或等价子命令，并支持 `--dry-run`、`--seed`、`--config`、`--run-id` 和输出目录参数。
6. 不要在代码中硬编码本机绝对路径、密钥、模型响应或实验结果；配置、prompt、数据和运行参数必须可追溯。
7. 完成后输出：实现摘要、文件清单、数据契约、CLI 用法、测试命令及结果、已知限制、未实现项。没有实际运行的部分必须明确写 `NOT RUN`，不得声称通过。

【交付验收】
代码大模型生成的实现只有在以下条件全部满足时才算完成：
- 可在无 LLM/KG 条件下通过 deterministic preflight 和 smoke test；
- 所有写入均位于允许的 self-play 输出边界，旧产物不被覆盖；
- schema round-trip、secret scan、Oracle projection、replay 和 hash 检查有测试证据；
- 能生成或明确记录 `NOT_GENERATED` 的 SP4 预期产物；
- 能输出 promotion decision、失败原因和 memory manifest；
- 不声称运行了真实实验，不声称提升了 WebQSP/CWQ EM/F1；
- 若仓库现状阻碍实现，先报告具体阻塞接口和最小补丁，不得用伪实现掩盖问题。
