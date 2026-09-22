# ResearchApp 接口

研究应用只使用 `ResearchApp`。任务执行、评分与存储的可复用接口不负责研究编排。

## 应用

`ResearchApp.create` 接受需求或完整 `ResearchConfig`；`start`、`call("chat", ...)`、
`pause`、`resume`、`stop` 控制研究。Web 与 CLI 使用相同应用和数据目录。
HTTP 路由见[仓库 README](../../README.md#cli-与-api)。

`ResearchConfig` 包含 TaskBrief、协作模式、模型连接、可选 seed、ApprovedCommand、
verifier 和 finalizer。只接受当前字段，不支持旧字段自动迁移。

### 需求更新

Interaction 的 `update_brief` 使用结构化的 `objective`、`instructions`、`limits` 和
`verification` 参数，按当前协作模式生成定义。协作模式提供当前支持的预算字段；pass@k
隐藏 token 总量、验证次数和累计节点数上限，由后端将这些字段保持为
`null`。工具仍提供研究员数量、研究时限、并发作业数、GPU 和可修改路径。
服务端使用同一份 schema 校验，并在写入任何需求记录前验证完整研究配置。
旧的 `brief_json` 参数不再接受；已完成工具调用的回放记录保留。
用户要求与所选模式冲突时，Interaction 应说明冲突，让用户选择切换模式或取消对应
预算，不自行放宽限制。验证 `identity` 由后端保留，接入工具负责其配置。

## Agent 与原生运行时

`modules/agent_runtime.py` 的 `AgentRuntime` 是研究回合边界：`turn` 接收持久化消息 ID、
角色 prompt、已授权工具、工作区和事件回调，返回 `TurnResult`。
`ResearchRuntime` 将其映射到 AReaL Agent Runtime；原生会话与工具能力不由应用重写。

`ToolCall.call_id` 在会话与逻辑消息内稳定。不同回合可以使用相同的供应商工具 ID；
同一回合的重试不能用同一个 ID 执行不同参数。

## 研究记录

- `ResearchPlan`：研究目标、节点预算、研究员数量。
- `ResearchRun`：执行状态、研究员、需求与基线/最佳/最终成绩引用。
- `ResearchIteration`：任务分配、报告、引用及时间戳，不绑定任务类型。
- `CandidateSnapshot`：不可变目录摘要与来源。
- `VerifiedResult`：绑定研究契约和候选摘要的 search/final/diagnostic 结果；基础设施错误不产生有效数值分数。

完整记录保存在 `base/runs.py` 管理的 `runs/<id>/` 下。应用给模型的视图经过权限检查和
裁剪，历史资料通过明确的记录引用读取。

## 实验命令与评分

`ApprovedCommand` 由操作员提供固定 argv 模板，模型只能填写声明的参数。
`JobManager` 管理进程组、日志、并发资源、超时及取消。`ResearchExecution` 将作业
关联到研究员、节点和候选，并在终态后发布获准的反馈目录。

可信验证器的 `evaluation.json` 必须包含候选 ID/摘要、协议和数据身份、phase、
指标名、分数及 `simulated=false`。`coordination/verification.py` 再次核对冻结输入。
需要最终确认的任务不能用探索成绩直接 `finish`。

`ApprovedCommand.parameter_descriptions` 按参数名说明路径、单位或允许值，随
`get_commands` 返回；内部 `workspace`/`output` 由系统填写，不因此改变参数校验。
配置 `auto_verify_output` 后，成功作业产出的指定目录会被冻结为候选，再由可信验证器
验证并关联原作业。未配置或没有产物时不自动验证，普通 stdout 回执不能冒充正式成绩。

没有自动达标目标且只登记实验命令时，可由研究员读取脚本反馈；自动判定达标仍需可信
verifier。完整执行配置在启动前校验和冻结，不要求 RSI 重复接入环境。

`metadata.time_limit_scope` 默认为 `total`（包含收尾），也可设为 `exploration`（最终验证
另计）。后者向收尾命令传入 `RSI_EXPLORATION_FINISHED_AT`，适配器按截止时间选择产物。
收尾通过 `RSI_CANDIDATE_SELECTION` 获取冻结选优清单：`schema_version=1`、`run_id`、
`withdrawn_candidates`；撤回项含候选 ID、摘要、原因、证据及文件摘要映射。
核心拒绝已撤回的完整快照；按文件组合的适配器也必须排除相应文件，不能用最终反馈继续优化。

## Task 执行与 Benchmark

可选的 harness 研究需要独立 Task 模型。这层复用 `api/modules.py` 的接口：

- `Benchmark.list_tasks` 只返回公开 Task，不包含答案。
- `HarnessRuntime.run` / `BatchHarnessRuntime.run_many` 执行固定模型和能力快照。
- `Verifier.verify(VerificationRequest)` 检查一个实际 TaskResult。
- `Evaluation.evaluate` 按固定规则汇总已完成的验证；执行失败不伪装成有效的零分样本。
- `ContentStore`、`VersionManager`、`TrajectoryStore` 保存不可变内容、版本和完整轨迹。

数学与 LCB 通过 ResearchApp 登记的验证命令调用上述组件。EnvArena 发布版本不可变，
候选只修改允许的能力字段，模型、运行底座、题目和 Reward 始终固定。

## 优化对象与可复用领域接口

`ResearchConfig.evolution_target` 可选值为 `model`、`harness`、`artifact`，由操作员配置，
保存到研究配置和原生会话配置记录；未声明时为 `null`，不根据文件名猜测。
数学/LCB 的接入声明 `harness`，KernelBench 产物接入声明 `artifact`。
分类本身不选择另一套 Workflow、不授予权限，也不自动提供训练能力；任务说明、
`allowed_paths`、登记命令和可信评测器仍决定具体执行与验证。模型训练需要接入真实后端。

以下通用 API 与旧 Workflow 无关，保留原有导入路径：

| API | 职责与实现 |
| --- | --- |
| `EvolutionTarget` | 优化对象的领域分类 |
| `HarnessChange / ModelChange / ArtifactChange / ResearchChange` | 候选修改或训练请求的数据契约 |
| `ArtifactBuilder / TrainingBackend` | 产物构建和训练接口；`LocalArtifactBuilder` 可独立构建产物，训练接口不等于已实现训练链路 |
| `EvidenceFile / EvidenceBundle / EvidenceStore` | 证据清单及显式文件授权；实现为 `FileEvidenceStore`，分页读取工具为 `FileReader` |
| `MemoryRecord / MemoryHit / Memory` | 可复用的记忆存储；实现为 `FileMemory`，不自动注入研究员上下文 |
| `LocalModelCatalog` | 按内容复用模型及基线版本 |
| `BenchmarkCatalog / BenchmarkRegistry / BenchmarkAdapter / BenchmarkBundle` | 评测元数据、适配器注册和装配，不负责研究编排 |

这些接口可以由集成方独立使用，不表示 ResearchApp 自动调用了所有组件。
`ResearchLimits.max_model_calls` 保留可空字段，当前仅接受 `null`；非空值明确报错，
不会静默忽略模型调用次数上限。工作台与需求工具不展示无法执行的限制。

旧 `api.workflow`、`WorkflowContext`、`RsiWorkflow`、`ExperimentService`、
绑定 Workflow 的 `ExperimentPlan / ExperimentRun` 和 `ResearchAgent.propose/select`、
`RsiAgent.chat/prune` 协议不恢复；当前应用使用 ResearchApp 和原生回合接口。
