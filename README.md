# AReaL-RSI

通过对话确定研究任务，RSI Agent 制定计划并协调多个 Research Agent 持续探索。任务可以没有起点、GPU 或验证分数。流程与数据结构见[设计说明](docs/rsi-research-design.md)。

## 启动工作台

需要 Python 3.11+、uv、Node.js 22.19+ 和 npm。使用固定工作流或 KernelBench 的 DSH Task runtime 时，主控还需要 macOS 的 `sandbox-exec`。

```bash
uv sync --locked
cp -n .env.example .env
```

在 `.env` 中填写默认模型连接；服务需要支持 Responses API 和工具调用：

```dotenv
RSI_BASE_URL=https://4ipic.com
RSI_API_KEY=<你的密钥>
RSI_MODEL=gpt-6-astra
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/research-lab
```

```bash
uv run --env-file .env --locked rsi workbench
```

打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)，新建研究，与 Interaction 讨论目标、允许修改的内容、计算环境、资源上限和评判方式。信息充分且你要求开始后，Interaction 将需求交给 RSI。

新建后、研究开始前，可以修改 API 地址、密钥、模型名和推理强度。开始后锁定；Task Agent 的目标模型单独配置。密钥保存在本机，不要写进聊天或提交到 Git。

工作台展示当前计划、研究图、节点任务与报告、作业、耗时和最佳成绩。运行中可以补充要求或回答 Agent 的问题；暂停后可继续。等待用户或作业时不调用模型，刷新页面也不调用模型。历时包含等待、排队和暂停。

关闭服务后，数据仍在 `RSI_DATA_ROOT/runs/<run_id>/`，使用同一数据目录重启工作台即可查看和恢复。需要终端执行时：

```bash
uv run --env-file .env --locked rsi research --config /absolute/path/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

## 接入任务

| 任务 | 接入说明 |
| --- | --- |
| 优化通用 KernelBench harness | [固定目标模型、题池和 GPU 环境](examples/kernelbench/README.md) |
| 优化单题 kernel | [已有 GPUQ 队列与每题提交额度](examples/kernelbench_artifacts/README.md) |
| CUHK-X 训练与模型设计 | [数据准备、容器训练和可信评分](examples/cuhkx/README.md) |
| 其他研究 | 在对话中提供环境与命令；需要正式分数时接入独立验证器 |

已有固定工作流仍可用 `rsi tui / run / resume / web`，使用 `RSI_TEACHER_*`、`RSI_TARGET_*` 和实验 YAML 配置。详见 [Runtime 启动指南](docs/runtime-quickstart.md)、[本地示例](examples/local/README.md)和[远程示例](examples/remote/README.md)。

## 开发检查

```bash
make check
npm ci --prefix tests/frontend
npm test --prefix tests/frontend
```

核心测试检查需求交接、报告后等待、会话与作业恢复、API 重试、compact、验证结果和工作台交互；不调用真实研究模型或 GPU。
