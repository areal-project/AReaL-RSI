# 插件使用

插件只是入口。外层 Codex/Claude Code 负责总结已有上下文、转发用户与 Interaction 的消息、
观察进度；RSI 自己的 Codex runtime 负责 Leader、Goal 续行、经验和研究任务。
退出外层 harness 不会停止任务。后台服务退出或机器重启后，需要显式 resume；不自动重放不确定操作。

## 安装

需要 Python 3.11+、Git、uv、Codex CLI 和 API 配置。插件模式无需 Node/npm。
这次修改合并到 main 后，安装插件即可，无需手动安装 AReaL-RSI CLI：

```bash
codex plugin marketplace add areal-project/AReaL-RSI --ref main
codex plugin add areal-rsi@areal-rsi
```

插件首次加载 MCP 时自动检查并安装 CLI，以后每次重新加载都会检查远端 main 的最新提交。
检查的是 Git 提交，而不只是包版本号；相同提交不会重复安装。
CLI 装在 `~/.local/share/areal-rsi/cli` 的独立环境，uv 会准备 Python 3.12；
不覆盖已有的全局或 editable CLI，不修改正常 Codex 的配置。
插件直接使用绝对路径，不要求 `areal-rsi` 在 PATH 中。安装输出只写 stderr。
若首次下载超过宿主的 MCP 启动超时，运行插件的 `setup` Skill 完成安装，再重新连接 MCP。
Codex 的安装界面提供 setup 入口；命令行安装本身不保证执行初始化脚本。

在 `~/.config/areal-rsi/.env` 填写 `RSI_API_KEY=...`，或在任务工作目录配置 `.env`。
配置文件与优先级见 [README](../README.md#configuration)。MCP 首次调用自动启动服务。
项目配置取自 create 的绝对 workspace，服务启动目录不参与项目配置选择。
通过工具显式指定 config_file、env_file 或图片时也使用绝对路径。

新开宿主会话，调用 `areal-rsi` skill，或说“用 AReaL-RSI 完成……”。
显示名称由宿主决定：可用 `/areal-rsi` 的宿主使用该命令；Claude Code 的插件 skill 通常为
`/areal-rsi:areal-rsi`。宿主会先交接摘要并与内部 Interaction 讨论，收到明确执行指令才交给 Leader。
摘要不能代替未传入的图片/完整工具结果，应包含可重新读取的文件路径。

Claude Code 本地加载同一份 Skill 与安装脚本：

```bash
claude --plugin-dir /absolute/path/to/AReaL-RSI/plugins/areal-rsi
```

其他支持 stdio MCP 的 harness 配置命令 `python3`，参数为
`["/absolute/plugin/path/scripts/launch.py", "mcp"]`，同样自动安装/更新。
已有独立 CLI 的用户仍可直接配置 `areal-rsi mcp`。
共享 Skill 位于 `plugins/areal-rsi/skills/areal-rsi/SKILL.md`；它不要求宿主使用 Codex 模型。
本版提供 Codex portable manifest 和 Claude manifest；没有实现 DeepSeek 专用插件包。
打包格式依据 [OpenAI 插件文档](https://developers.openai.com/plugins/build/plugins)及
[Claude Code manifest 文档](https://code.claude.com/docs/en/plugins-reference)。

## 请求与能力

| 工具 | 作用 |
| --- | --- |
| `rsi_create` | 绝对 workspace、用户消息、上下文摘要；默认进入 RSI，也支持 mode=chat |
| `rsi_send` / `rsi_answer` | 消息、图片路径、执行中补充；回答当前 prompt_id |
| `rsi_control` | rsi/start/pause/resume/stop/chat/open |
| `rsi_observe` | 完整消息的游标分页、进度与当前问题；可等待 0–20 秒 |
| `rsi_query` / `rsi_list` | 会话详情、模型列表、候选；最近 50 个会话 |
| `rsi_configure` | model、effort、learning_mode、budget；省略项保持原值 |
| `rsi_learn` / `rsi_experience` | 可带复盘意见的总结；列出或读取语义命名经验 |
| `rsi_research` | 准备绑定任务或对候选执行独立最终验收 |
| `rsi_request` | 查询写请求的执行回执 |

写操作都带调用方生成的 request_id。返回 running 表示请求已接受，使用 rsi_request 等待。
completed 表示**这次操作完成**，只有观察结果中的 session.status=completed 才表示 Goal 完成。
相同 request_id 和参数返回原回执，换参数会拒绝。超时重试沿用原 ID；interrupted 必须先核查实际
会话/文件结果，再决定是否提交新操作。长时间基线评测、最终验收和经验总结也遵循此规则。
create 一旦建好会话，回执 result 就包含 run_id，即使后面的连接或基线准备失败也能找回。

每个观察者保存自己的 cursor（初始 0），按返回 cursor 继续；has_more=true 时继续读分页。
完整消息持久化，delta 不经 MCP 返回，单条消息超过 3000 字符显示摘要；原文保留在 transcript。
等待调用是有时限的，不占用整个 Goal 生命周期。问题标识在外层断开后仍有效；内部 Codex 连接
重建后失效，拒绝旧答案，需恢复任务并重新提问。宿主不能代替用户批准，也不能宣布内部 Goal 完成。

可选 problem 使用现有 ProblemBinding 契约；配置文件用 ContentRef（含内容摘要），任务实现须在
服务 Python 环境中可导入。源码 checkout 的 examples 仍需设置 PYTHONPATH，插件不打包 GPU 环境。
普通编程任务无需传入 problem。既有候选、冻结协议与 source-lock 格式保持不变。

## CLI 与服务

机器调用和 MCP 使用相同 JSON 契约，例如将以下内容保存为 `/tmp/rsi-request.json`：

```json
{
  "request_id": "my-project-initial-goal",
  "workspace": "/absolute/path/to/project",
  "context": "已有讨论：保持现有业务字段与交互约束。",
  "message": "先讨论如何优化页面，并确认验收方式。"
}
```

```bash
areal-rsi call rsi_create --input /tmp/rsi-request.json
areal-rsi service status
areal-rsi service start
areal-rsi service stop
```

自动安装的 CLI 可通过 `~/.local/share/areal-rsi/cli/current` 调用；上述命令中的
`areal-rsi` 对应这一完整路径，也可使用自行安装的 CLI。
`call` 也接受 stdin JSON。所有传输入口支持 `--data-root /absolute/data`，默认使用
`RSI_DATA_ROOT` 或 `~/.local/share/areal-rsi`。MCP/服务的数据目录由启动参数或导出变量指定，
不会随某个项目的 TOML 改变。需要多套隔离服务时为不同 MCP 配置显式数据目录。

一个数据目录只有一个服务，使用用户私有 Unix socket；本版支持 macOS/Linux。
活跃任务存在时 stop 拒绝停止；先 pause，或显式 `service stop --force` 中断并保存状态。
`service/service.log` 记录服务错误，`service/requests.sqlite3` 保存请求回执。
服务停止不自动取消外部已提交 Slurm 作业；真正终止一个任务使用 rsi_control(stop)。

现有 TUI 与 `run/research` 命令仍为嵌入式入口，退出时暂停。它们和后台服务不能同时接管
同一个 run；会话锁会明确报错。查看插件任务用 MCP 或 `call`，不要另开 TUI 接管活跃任务。

## 更新

CLI 在插件每次加载时自动检查更新；无需再运行 `uv tool upgrade`。
新提交装入新的独立环境，安装成功后才切换 `current`；下载失败时保留可用旧版并记录原因，
首次安装失败则明确报错。并发加载用文件锁合并安装，不往插件缓存目录写入运行数据。

运行中的服务继续使用原安装环境，MCP 重连也跟随该服务的 CLI，避免更新打断工作。
旧环境保留，不在更新时删除。完成或暂停任务后停止服务，再重新加载 MCP 才启用新版。
自动更新不会强制停止服务，也不会恢复原本暂停的任务。

插件本身的 Skill/manifest 有改动时，仍通过宿主更新插件包：

```bash
codex plugin marketplace upgrade areal-rsi
codex plugin add areal-rsi@areal-rsi
```

包的语义版本和两个 manifest 在正式发布时同步提升。可用下面的命令比较已安装 CLI 与
运行服务；后者也返回运行服务的 Python 路径：

```bash
~/.local/share/areal-rsi/cli/current --version
~/.local/share/areal-rsi/cli/current service status
# 完成或暂停任务后，停止服务，再重新加载 MCP：
~/.local/share/areal-rsi/cli/current service stop
```

协议不兼容会明确报错。自动安装不等于自动升级已在运行的任务。

## 本地开发

尚未合并的代码不能从远端 main 自动安装。开发时直接使用本地 CLI，并关闭自动安装：

```bash
uv sync --locked
export RSI_CLI_PATH=/absolute/path/to/AReaL-RSI/.venv/bin/areal-rsi
codex plugin marketplace add /absolute/path/to/AReaL-RSI
codex plugin add areal-rsi@areal-rsi
```

该变量需要传给启动宿主的进程。默认路径不受 shell 当前目录影响。
`RSI_CLI_HOME` 可改变私有安装目录；分发测试可用 `RSI_CLI_REPOSITORY` 和 `RSI_CLI_REF`
指定其他 Git 仓库和分支。修改插件文件后重新安装插件刷新缓存，修改源码后重启内部服务。

Codex 会过滤 MCP 子进程继承的环境变量。插件的 `.codex-plugin/plugin.json` 仅补充
RSI 配置、Python 路径及下载所需的环境变量透传；启动命令仍由 portable `mcp.json`
定义。否则宿主中的 `RSI_CLI_REF`、`RSI_CLI_PATH` 等设置不会生效。清单只记录变量名，
不保存 API Key 的值；个人配置仍优先使用 `.env` 文件。
