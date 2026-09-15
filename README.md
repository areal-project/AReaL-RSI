# AReaL-RSI

一个支持持续研究的多 Agent 系统：Interaction 与用户确定需求，RSI 制定计划并协调 Research Agents，Research Agents 执行实验、汇报结果并在原会话中继续研究。工作台展示搜索图、节点报告、作业进度、耗时与验证成绩。

## 准备

需要 Python 3.11+、uv、Node.js 22.19+ 和 npm。研究模型接口须支持 Responses API 和工具调用。使用旧固定工作流或 DSH Task runtime 时，还需要 macOS `sandbox-exec`。

```bash
uv sync --locked
cp -n .env.example .env
```

在 `.env` 中配置模型连接，密钥仅保存在本机：

```dotenv
RSI_BASE_URL=https://4ipic.com
RSI_API_KEY=<你的密钥>
RSI_MODEL=gpt-6-astra
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/research-lab
```

## 启动 Web 工作台

在仓库根目录运行：

```bash
uv run --env-file .env --locked python run.py --type web
```

打开 **http://127.0.0.1:8197**，新建研究，与 Interaction 讨论任务、环境、资源限制和目标，要求开始后交给 RSI。开始前可修改模型地址、密钥、名称和推理强度；运行中可以回答问题、补充要求、暂停和恢复。

入口自动在后台启动服务；已有兼容的 RSI 工作台则复用，其他进程占用端口则报错，不关闭或覆盖它。用 `--port 8200` 指定端口，用 `--data-root /path/to/data` 指定数据目录。旧版 RSI 服务需要先重启到当前版本。

服务日志和 PID 位于 `<数据目录>/runtime/workbench-<端口>.log / .pid`。若希望前台运行、用 Ctrl+C 停止服务：

```bash
uv run --env-file .env --locked rsi workbench --port 8197
```

## 一键运行 KernelBench 八题 demo

拥有 et15 SSH 和已部署 GPUQ 的访问权限后，无需先打开浏览器或重复填写需求：

```bash
uv run --env-file .env --locked python run.py --type demo
```

脚本自动启动或复用工作台，注册独立的八题额度并启动研究，输出可直接打开的研究链接。RSI 自主分工，结束探索后程序自动冻结候选、独立复测并归档。关闭浏览器或启动终端不影响后台研究。

题目、提交预算、环境检查、恢复命令和历史结果见 [KernelBench demo 快速指南](examples/kernelbench_artifacts/README.md)。这条入口优化具体 kernel，不需要额外 Task Agent；[通用 harness 优化](examples/kernelbench/README.md)是另一种任务。

## CLI 与 API

已有完整研究配置时，可以不经过 Interaction，直接在终端运行：

```bash
uv run --env-file .env --locked rsi research --config /absolute/path/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

CLI 持续输出进度；Ctrl+C 保存状态供恢复。用同一数据目录打开工作台即可查看。CLI 持有执行权期间，Web 只读；需要同时操作研究时，使用上面的 demo/API 入口，让工作台后台执行。

工作台提供本机 HTTP API。先 `GET /api/bootstrap` 获取 `csrf_token`，写请求使用 `X-RSI-Token` 请求头和 JSON body：

| 接口 | 用途 |
| --- | --- |
| `POST /api/runs`，body 为 `{"message":"研究需求"}` | 创建 Interaction 对话 |
| `POST /api/runs`，body 为 `{"run_id":"自定ID","config":{...}}` | 用完整 `ResearchConfig` 创建研究；同一 ID、同一请求可安全重试 |
| `POST /api/runs/<id>/start`，body 为 `{}` | 启动已准备的研究 |
| `GET /api/runs`、`GET /api/runs/<id>` | 查看列表、搜索图、报告、作业和成绩 |
| `POST /api/runs/<id>/chat`，body 为 `{"text":"补充要求"}` | 发送消息 |
| `POST /api/runs/<id>/pause`、`resume`、`stop` | 控制执行，body 为 `{}` |

## 记录与恢复

每次研究的数据集中在 `RSI_DATA_ROOT/runs/<run_id>/`，包含需求、会话、任务、报告、共享证据、冻结候选和验证记录。等待作业、报告或用户时不调用模型；临时 API 故障自动重试。

demo 在服务重启后自动恢复尚在执行的研究或收尾；手动暂停的研究保持暂停。完成最终复测后不能继续修改这次候选，需要新建 run。外部凭据失效或集群故障会明确报错或等待处理。

系统职责和数据模型见[设计说明](docs/rsi-research-design.md)。旧固定流程和可选运行后端见 [Runtime 指南](docs/runtime-quickstart.md)。开发检查：`make check`；前端检查：`npm ci --prefix tests/frontend && npm test --prefix tests/frontend`。

旧固定流程仍可通过 `./tui` 启动，脚本自动读取本地 `.env`。Terminal-Bench
实验配置和用法见 [TB2.1 示例](examples/tb21/README.md)。
