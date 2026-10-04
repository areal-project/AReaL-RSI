# AReaL RSI

从空历史重建的模型与 Harness RSI 实验项目。首个目标是数学 TIR Agent 的单代闭环：冻结任务与 `M0/H0`，搜索并选择 `H1`，在固定 `H1` 下用 AReaL GRPO 训练 `M1`，配对评测四种组合并记录发布决定。

当前已有从候选生成/选版到 AReaL 2.0 GRPO recipe、配对评测与原子发布的代码和 CPU 行为测试。**尚未在 Slurm GPU 集群执行真实推理、训练或封存评测**；CPU 模拟结果不能代表能力提升。运行准备见 [Slurm TIR 示例](examples/areal_rsi_tir/README.md)，验收事实见 [实现状态](docs/implementation-status.md)，实验约束见 [架构](docs/architecture.md) 与 [研发计划](docs/roadmap.md)。旧项目保留在原分支作为实现参考，不属于本分支历史。

首版只支持 Slurm：控制器运行在能调用 `sbatch` 的集群环境，AReaL 负责提交 GPU worker。数据合成和 SFT 是后续独立实验，不进入首个闭环的关键路径。

## 公共运行层（2026-10-05）

- `api/execution.py` 定义外部执行、MCP 工具配置和结果契约；`api/research.py` 定义选择策略的输入输出。
- `infra/runtime/claude.py` 执行 Claude Code 子进程，接受明确的 MCP 工具配置；`anthropic.py` 适配本地推理服务。两者不依赖数学题或 example。
- `infra/coordination/policies/` 负责候选选择、噪声门槛和版本晋升；`infra/storage/artifacts.py` 负责模型、轨迹和源码证据。
- `examples/areal_rsi_tir/` 保留数学题集、合成题、计算器、答案验证、TIR/Claude 训练配方和实验编排。最小执行入口仍是 `cycle.py`。

新任务可以直接使用公共执行函数和具名契约，提供自己的 MCP 工具、题集和评分实现；无需导入 TIR example。非数学工具调用由 `tests/test_agent_runtime.py` 验证。没有新增单实现 Protocol 或旧 import 路径的转发层。

迁移后的 Python 包仍随 wheel 完整安装。TIR 评分器的来源摘要覆盖整个 `areal_rsi` 包和 example 包；移动文件后需要冻结新的实验记录。历史源码锁、题集和分数留在原工作区，不迁移或覆盖。
