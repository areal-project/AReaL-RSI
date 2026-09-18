# Runtime 验证范围

当前研究入口为 ResearchApp，固定轮次实验控制器已删除。

- `tests/test_research_runtime_package.py`：原生 standard/Codex 工具、隔离、会话、取消与 usage。
- `tests/test_tool_call_identity.py`：不同消息中的工具 ID、同消息重试和持久化副作用。
- `tests/test_preflight.py`：真实 Runtime 子进程连接本地脚本模型，验证工具、记忆与重复消息。
- `tests/test_runtime_package_execution.py`：可复用 Runtime 的工具、MCP、事件及取消契约。
- `tests/test_benchmark_research.py`：数学/LCB 候选、任务、评分身份与 TRAIN/VALIDATE 公开输出隔离。

这些测试不调用外部付费模型，不代表 benchmark 成绩。真实端到端验收必须使用
ResearchApp 的记录、候选、实验作业及正式验证结果。
