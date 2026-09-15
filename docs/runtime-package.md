# 可配置 Task Runtime 与持久化

研究编排只有 ResearchApp / ResearchEngine。任务执行仍可使用独立的 ARealAgentRuntime package；切换执行后端不需要恢复旧固定工作流。

## 数学实验

在准备研究配置之前指定可信 Runtime JSON：

```bash
RSI_RUNTIME_CONFIG="$PWD/examples/runtime/dsh.json" \
  uv run --env-file .env --locked rsi prepare --case gsm8k \
  --dataset .rsi/datasets/gsm8k --output .rsi/configs/gsm8k-package/research.json
uv run --env-file .env --locked rsi tui --config .rsi/configs/gsm8k-package/research.json
```

DSH 对话中要求开始即可。准备阶段将 Runtime JSON 固定在 operator 配置中；恢复时使用该快照，不因当前环境变量变化而改变被测执行后端。未设置 RSI_RUNTIME_CONFIG 时，数学保留直接 DSH Task adapter。

`examples/runtime/` 保留 DSH、Codex、Claude、Anthropic、Hermes、Remote、Entrypoint、Fake 配置示例。原生 CLI、远端服务和可选 SDK 需各自安装/配置，配置能够解析不等于所有后端都已在本机执行验收。可选依赖：

```bash
uv sync --locked --extra codex --extra anthropic --extra claude
```

研究角色当前仍使用持续 Responses 会话；这些 Task Runtime 配置不改变 RSI/Research 的编排实现。需要自定义 AgentSession 的调用方也可以直接组合 PackageAgentRuntime，不依赖旧 WorkflowContext。

## KernelBench

可信 operator JSON 可提供 `task_runtime` 对象，例如：

```json
{"task_runtime": {"default": {"kind": "dsh"}}}
```

`examples/kernelbench/run.py` 将它传入任务执行工厂。它属于固定实验条件，不从候选或运行时环境临时读取。

## 实现与数据

```text
Task evaluator → create_task_runtime
                    ├─ 直接 DSH adapter
                    └─ PackageAgentRuntime → sandbox worker
                                                 ├─ 所选 Runtime
                                                 ├─ runtime_mcp 工具桥接
                                                 └─ runtime_storage 日志/资产插件
```

- `runtime_storage.py` 追加并 fsync 事件及 RunSnapshot，落盘失败后阻止继续发布状态。
- `read_journal` 校验连续序列与归属，崩溃时仅忽略末尾未写完整的记录；完整损坏记录会报错。
- worker 结束时从已提交日志导出轨迹，包括取消时的终态；资产由 FileSystemAssetStore 保存。
- 该 journal 是单 worker 存储，不承诺重启同一个 writer；研究会话恢复由上层引擎处理。
- TargetGateway 对 Chat Completions、Responses 和 Anthropic Messages 统一固定模型并限制调用/输出预算。
- macOS sandbox-exec 隔离 Task 工作区与宿主隐藏数据。原生程序路径需按配置授予必要读取权限。

Runtime wheel、DSH bundle 与 provenance 继续随仓库固定。旧固定演化的计划和控制器没有恢复。
