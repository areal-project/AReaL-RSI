# 统一研究架构

```text
DSH TUI (Interaction) → MCP ─┐
Web ─┼─ HTTP API → ResearchApp → ResearchEngine
CLI ─┘              （CLI 直接装配同一个 ResearchApp）
                           │
          Interaction ↔ RSI ↔ Research Agents
                           │
                执行工具 / JobManager
                           │
           本地 Task Agent / EnvArena / GPUQ
                           │
                  可信验证 → 冻结结果
```

## 职责

- `ResearchApp`：创建研究、配置、用户消息、启动控制、恢复和导出。
- `ResearchEngine`：持续会话调度、事件交付、并发、预算、重试与取消。
- `Coordinator`：分派、报告、消息及研究状态转换。
- `ResearchStore` / `FileRunStore`：run 内记录和不可变候选；内容存储做去重。
- `JobManager`：独立子进程、队列、作业身份和恢复。它不解释研究结论。
- 验证器：绑定冻结候选和固定任务条件，只有合法验证结果能更新成绩。

TUI 使用原生 DSH plugin 承担前台 Interaction，自然对话由 DSH 处理；MCP 直接调用新研究 HTTP API，不再创建另一个后台 Interaction 来转述对话。Web 仍提供自己的 Interaction 入口。Web 与 TUI 共用后台执行进程；前台 CLI 使用同一个引擎并持有相同执行锁，其他界面对该研究只读。没有固定 `propose → build → evaluate → select` 工作流、旧 MCP 控制器或 Runtime package 桥接。

## 数学与 LCB

`adapters/harness_cases.py` 将已有 Benchmark 和 Task Runtime 组装为 ResearchConfig 的 `develop` 与 `verify` 命令。它不调度研究员、不实现另一个研究循环。

- 数学：固定目标连接、题池和初始 Harness；研究员只修改 `prompt.md`，Task Agent 在独立 macOS 沙箱解题，宿主评分器读取私有答案。
- LCB：固定远端 Benchmark / Reward / Model；候选 prompt 通过 ArenaHarnessBuilder 发布为新 Harness 版本，ArenaHarnessRuntime 保存远端 job 回执并读取真实评分。
- 训练作业公开输出和反馈；search/final 不公开原始题目、答案和任务轨迹，只向研究角色返回聚合结果。

研究角色的基础文件／shell 工具运行于宿主用户权限；工作区和报告引用不是操作系统级安全隔离。只运行可信研究任务。Task Agent 沙箱及远端评分隔离与研究角色的工具权限是不同边界。

## 取消与恢复

研究会话、消息交付身份和作业状态持久化。退出 TUI 不停止服务器；关闭执行服务保留状态。显式 stop 才取消所属作业。不能凭本地超时认定远端作业未提交，重连使用原回执。

配置 finalizer 时，探索时间耗尽进入收尾；没有 finalizer 的研究在预算耗尽后暂停等待处理。手动暂停不会自动启动收尾。
