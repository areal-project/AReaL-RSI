# 研究与执行接口

## 一个研究入口

`ResearchConfig` 定义 TaskBrief、模型连接引用、seed 目录、执行命令和验证器。TUI/Web/CLI 共用 `ResearchApp`。

- TaskBrief：目标、指令、并发及预算、可选 VerificationContract。
- ResearchIteration：任务节点及报告、材料引用，不要求产生版本或成绩。
- ResearchReport / EvidenceRecord：研究结果、依据及局限。
- CandidateSnapshot：不可变目录快照。
- VerifiedResult：候选、比较条件、验证阶段和结果绑定；基础设施失败没有数值成绩。

`FileRunStore` 只接受当前 `runs/<id>/run.json` 格式。旧 `run-states`、`legacy-run://`、EvolutionResult 和旧 Workflow API 已移除。

## 执行命令

操作员配置 `ApprovedCommand` 的可执行文件、参数模板、时间/GPU约束及公开输出目录。研究员通过 `run_job` 提交实验，不能在参数中替换固定可执行文件。`verify_candidate` 对冻结候选运行操作员验证器。

`{workspace}`、`{output}` 与验证阶段 `{phase}` 由宿主绑定。作业环境继承服务环境，因此模型和远端认证需在启动服务前配置。实际模型密钥不会写入公开研究配置；Web/TUI 提交的研究密钥保存在 run 内受限权限文件。

验证输出 `evaluation.json` 包含 status、simulated、phase、metric、score 以及预先固定的协议/数据身份。通用 content adapter 还校验 candidate_id 和 candidate_digest；不把进程成功退出、stdout 或 Agent 自报数字当作分数。

## 保留的任务层接口

Benchmark 提供公开 Task；TaskResult 引用输出和轨迹；Verifier 接收 VerificationRequest；ContentStore 保存内容；SolutionVersion 仍可在任务执行适配器内组合固定 Model / Harness。它们不决定研究步骤。

数学适配器复用 Arithmetic/GSM8K 的任务与评分实现。LCB 复用 Arena 的固定版本、发布候选、批量作业、回执和评分实现。KernelBench 使用自己的 GPU 执行协议。这些都通过同一个 JobManager 调用。

## HTTP API

`GET /api/bootstrap` 获取 CSRF token；写请求附带 `X-RSI-Token` 和 JSON body。

| 接口 | 用途 |
| --- | --- |
| `POST /api/runs` | 用 message 开始对话，或传完整 config 创建研究 |
| `GET /api/runs` | 研究列表 |
| `GET /api/runs/<id>?after=<cursor>` | 当前状态、消息、图和增量事件 |
| `POST /api/runs/<id>/chat` | text、message_id；同一发送重试保持 message_id |
| `POST /api/runs/<id>/start` | 开始研究 |
| `POST /api/runs/<id>/pause`、`resume`、`stop` | 控制同一研究 |
| `POST /api/runs/<id>/delete` | 删除已停止的研究 |

TUI 的 `/start`、`/pause` 等命令直接使用这些 API，不另建 Agent 或实验状态。
