# PR #29 删除范围复核

复核原则：删除旧研究编排不等于删除执行后端、存储、工具桥接或扩展接口。

## 已撤销的过度删除

| 范围 | 恢复内容 | 新架构中的用途 |
| --- | --- | --- |
| Runtime package | runtime_package、runtime_worker、runtime_dsh、vendor wheel、配置示例与 optional extras | 可组合的 AgentSession，以及新数学/KernelBench Task 执行工厂 |
| Runtime 状态与资产 | runtime_storage.py、schema、对应测试 | fsync 日志、状态与环境快照、失败关闭、轨迹导出 |
| 通用 Agent 工具 | agents/common.py | AgentSession 的 JSON 解析与格式重试，不依赖旧研究循环 |
| Runtime 工具桥接 | runtime_mcp.py、schema、工具授权测试 | 候选 MCP 与便携/原生 Runtime 工具调用 |
| Remote 后端接口 | remote/base.py、registry.py | 后端选择与只读远端作业投影，不选择研究工作流 |
| Arena 装配 | remote/envarena/backend.py | 组合固定基线、Benchmark、HarnessRuntime、Builder、Verifier、Evaluation；LCB 通过此层执行 |
| 回归覆盖 | Runtime storage/package/execution/tool-grants 测试 | 保留底层能力验证，只去掉依赖旧 LocalApp 的固定工作流端到端测试 |

RemoteBackend 不再创建旧 propose/select ResearchAgent。Arena 的 system_prompt、skills、tools、mcp 修改能力仍通过通用 HarnessBuilder 提供；内置 LCB 验收任务刻意只修改 prompt，不能把它等同于整个 remote 接口。

## 保持删除的内容

- LocalApp / RemoteApp、固定演化 workflows，以及旧 propose/select 研究员：它们实现的是已被替换的研究编排。
- 旧 ExperimentController / worker / workspace / 只读看板，以及相应旧接口测试：新 ResearchApp 已承担研究生命周期。
- 旧 local-app 注册器和 local 配置选择：其工厂契约返回旧实验服务，依赖旧计划/工作流；不将这一旧 AppFactory 当作新的执行后端接口恢复。
- 旧固定 workflow 的 API 协议和历史路径兼容：不再承诺加载旧固定计划。

DSH TUI plugin 已在此前修复中恢复，本次继续保留。3D 不新增启动入口、不纳入本次验收，保留既有任务与验证适配器。remote 验收使用更直接的 LCB 路径，本地 Task Runtime 使用数学。

## 验证范围

- Runtime journal 的落盘失败、完整性和崩溃尾记录测试。
- 实际 package worker 的 Fake/Entrypoint 会话与轨迹、Remote stub wire、工具授权。
- Anthropic SDK 对本机脚本化模型服务的工具往返、取消终态和候选 MCP；不调用付费模型。
- Arena 版本/候选/回执/重连/取消/评分测试，以及恢复后 provider 装配的批量执行。
- TargetGateway 三种 API 的固定模型、认证、共享调用预算和输出上限。
- 使用真实 LCB 与配置为 package DSH 的数学任务复验，结果另记录于验收文档。

可选原生 Codex、Claude、Hermes 等后端依赖实际安装或原有 opt-in 开关；未将未执行的后端声称为已完成真实验收。
