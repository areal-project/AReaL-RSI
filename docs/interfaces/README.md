# 接口

`ResearchSystem(root, runtime_factory=...)` 是核心入口；生产环境使用默认 `ResearchRuntime`，测试可以提供相同 Runtime 协议的实现。UI 不拥有调度状态。

| 方法 | 结果 / 约束 |
| --- | --- |
| `create(config, message=None, run_id=None, simulated=False)` | 返回 run ID。message 开启 Interaction intake；不传 message 可直接 start |
| `start(run_id)` | 固定配置与 Git HEAD，开始目标执行 |
| `send_message(run_id, text)` | 启动前发 Interaction，运行中发 Leader；legacy 开始后拒绝修改 |
| `pause(run_id)` / `resume(run_id)` | 暂停模型回合或恢复原消息、会话；预算耗尽拒绝 resume |
| `retry(run_id, agent_id="rsi")` | 重试失败角色，保持操作幂等身份 |
| `stop(run_id)` | 停止 run 和受管理的未完成作业 |
| `detail(run_id, after=0)` | 状态、goal、预算、事件增量、候选、工作区和 worker 状态 |
| `export(run_id, destination)` | 向不存在的新目录导出验收快照 |
| `close()` | 释放本进程会话和锁，持久作业保留供恢复 |

控制操作按 run 加锁，持久 flock 拒绝另一个进程修改同一 run。固定内部数据使用 dataclass；JSON 配置通过 `api/control.py` 验证，未知字段和模式直接报错。

## 基础能力

`api/records.py` 定义 Model/Harness/Artifact/Solution 版本、Task、TaskResult、VerifierSpec 和轨迹。`api/interfaces.py` 定义 ContentStore、EvidenceStore、VersionManager、TrajectoryStore、Memory、HarnessRuntime、Builder、TrainingBackend、Verifier 和 Evaluation 等协议。版本追加创建，研究中的 best/selected 指针属于 run；正式验证绑定候选内容摘要与固定协议。轨迹既可本地存储，也可接入配置的外部来源。

`api/execution.py` 定义 ApprovedCommand、JobSpec/Record 等执行记录。`ResearchExecution` 向角色开放已配置命令，固定参数通过 argv 传递；验证器输出经过 `read_trusted_result` 检查，不能由模型直接写一个分数替代。

## 后续 Team 契约

`TeamTask` 表示 Leader 下发的局部任务，`TaskReport` 表示 Researcher 的返回。`AgentTeam` 预留 delegate/interrupt/collect；当前无实现、无注册工具和运行模式。该协议不包含 run 准出方法，Researcher 完成 task 与 Leader 完成 goal 分开处理。
