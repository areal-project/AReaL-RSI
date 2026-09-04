# AReaL-RSI

AReaL-RSI 是一个面向日常研发的通用 RSI Infra。该分支使用 Python 从零设计，现阶段只包含最小接口与领域对象，尚未实现运行逻辑。

通用 API 支持三类自进化对象：Model、Harness 和用户 Artifact（例如游戏、应用或其他研发产物）。第一阶段仍以 KernelBench 的 Harness evolution 为牵引。

第一阶段目标：

- 使用 DeepSeek Harness Python SDK 构建 RSI Agent、Task Agent 和 ResearchAgent；
- 使用本地文件保存 Artifact、Memory、Trajectory、版本和运行状态；
- 固定模型，只优化 Harness 的 Prompt、Skill、Tool 和 MCP；
- 使用固定 Python Workflow 编排三轮实验，不引入 Workflow DSL；
- 为多 ResearchAgent 并发搜索保留接口，但第一版只实现单 ResearchAgent；
- 使用 train、validate 两种标准数据分区，禁止验证标签进入 Harness 和 ResearchAgent。

## 接口结构

```text
src/areal_rsi/
└── api/
    ├── base.py      核心数据对象
    ├── modules.py   基础模块 Protocol
    └── workflow.py  Agent 角色与固定 Workflow Protocol
```

## 当前状态

接口设计完成后必须经过用户确认，确认之前不实现 File Store、DeepSeek Harness Adapter、Workflow、Benchmark、Verifier 或 TUI。

详细设计见：

- [架构边界](docs/architecture.md)
- [Python 接口详细说明](docs/interfaces/README.md)
