# AReaL RSI

基于原生 Codex app-server 的单 Agent 工作框架。默认普通对话；中途 `/rsi` 进入需求讨论，
Interaction 整理目标，Leader 亲自执行，由宿主维持 Goal 的持续运行。

本版仅支持 Codex；终端直接复用 DSH 原版组件。研究团队、版本管理、独立评测、memory、训练和
legacy pass@k 的实现已移除；未来 API 契约保留。经验先沉淀为本地文档，云文档同步后续接入。

## 本地启动

需要 Python >= 3.11、uv、Node.js >= 22、npm、系统 `patch`，以及 Codex CLI >= 0.162.1（本版验证版本）。

```bash
npm install -g @openai/codex@0.162.1
uv sync --locked
cp .env.example .env  # 已有 .env 时保留原文件
```

在 `.env` 中填写 `RSI_API_KEY`、`RSI_BASE_URL` 和 `RSI_MODEL`。
供应商须支持 Responses API、工具调用；视觉任务还需要图片输入支持。

之后一行启动：

```bash
uv run --locked rsi tui
```

首次启动自动按锁文件安装 TUI 依赖并应用上游编辑器补丁，需要联网；之后可直接启动。
启动时自动加载当前目录的 `.env`。在另一个项目中工作：

```bash
uv run --locked rsi tui --workspace /absolute/path/to/project
```

也可指定 `--env-file /path/to/.env`、`--data-root /path/to/data`。
API 连接参数、历史和 Codex 设置在 RSI 独立的数据目录中使用；不会改写正常 Codex 的
登录或 `~/.codex/config.toml`。API key 只由子进程环境读取，不写进会话 JSON 或命令参数。
工作目录内的项目指令及工具配置仍按原生 Codex 规则生效。

新会话默认使用 `RSI_SANDBOX=danger-full-access`、`RSI_APPROVAL_POLICY=never`：
可访问当前系统用户有权限访问的任意目录，执行时不弹审批。workspace 是起始目录，不是权限边界。
普通模式、Interaction、Leader 和经验总结沿用同一权限配置；总结提示词仍要求只提炼经验。
需要限制时可在 `.env` 显式设置 `workspace-write` 或 `read-only`。旧会话沿用已保存的权限；
切换默认值后，退出并重新启动 TUI 创建新会话即可，不使用 `--resume` 恢复旧配置。

## 使用

1. 先普通对话：讨论需求、理解业务、修改小问题；Enter 发送，Shift+Enter 换行。
2. 输入 `/rsi`，或 `/rsi 基于刚才讨论的内容优化页面`。
3. Interaction 沿用之前的会话讨论目标、约束和完成标准。明确要求开始后自动交接，
   也可在任务已保存后输入 `/start`。
4. Leader 执行；回合结束而目标未完成时，宿主自动继续。
5. `/chat` 回到普通对话，继续使用最新工作上下文。

| 操作 | 命令 |
| --- | --- |
| 执行中补充要求 | 直接发送消息，使用原生 steer |
| 附加参考图 | `/image /path/reference.png`，然后发送描述；或在路径后直接写描述 |
| 暂停 / 继续 / 停止 | Esc 或 `/pause`；`/resume`；`/stop` |
| 回答问题 / 显式启用审批后的权限请求 | 原版 DSH 弹窗选择或输入；审批选项默认拒绝 |
| 展开工具 | Ctrl+O |
| 总结经验 / 查看文档 | `/learn [复盘反馈]`；`/docs` |
| 查看 / 切换总结模式 | `/learn-mode`；`/learn-mode auto`；`/learn-mode guided` |
| 查看当前目标 | `/status` |
| 查看 / 取消 / 设置预算 | `/budget`；`/budget off`；`/budget <tokens\|off> <秒\|off>` |
| 查看 API 模型列表 / 切换模型 | `/model`；`/model 模型名` |
| 查看 / 设置推理强度 | `/effort`；`/effort low\|medium\|high\|xhigh` |
| 历史会话 | `/sessions`、`/open run-ID`，或启动时 `--resume run-ID` |
| 新会话 / 保存退出 | `/new`；Ctrl+Q 或 `/quit` |

退出会暂停未完成工作，重新打开不会自动执行，使用 `/resume` 显式继续。
新目标默认不限制 token 和执行时间，Interaction 不设置预算。需要限额时，在目标保存后由用户
输入 `/budget 100000 3600`（总 token 上限和活跃执行秒数，包含已用量），任一项用 `off` 表示不限。
预算耗尽后用 `/budget off` 取消限制，或提高上限，再 `/resume` 继续原目标和上下文；已用量不会清零。

`/model` 使用当前会话的 Base URL 和 API key 查询 `/models`，显示接口实际返回的模型列表。
查询期间仍可操作 TUI；若接口不支持查询或连接失败，会说明原因，也可继续手动设置模型。
列表可能包含图片等非 Agent 模型，不代表每个模型都支持 Codex 所需的工具调用。

`/model 模型名` 与 `/effort high` 保留当前上下文，从下一回合生效；已经开始的执行或经验总结使用原设置。
设置保存在当前会话中，随后 `/new` 创建的会话也沿用，不修改 `.env` 或正常 Codex 的配置。
本版提供 low、medium、high、xhigh 四档 effort，具体支持情况取决于所选模型和 API 服务。

普通对话 → Interaction 使用同一个原生 thread；Interaction → Leader 使用原生 fork，
继承此前文字、工具结果、图片和压缩历史。任务配置用于明确目标，不替代完整会话。
长会话仍受模型窗口和 Codex 压缩机制影响，不能保证所有原始细节永久驻留。

本版界面支持流式输出、常用工具事件、图片路径输入、权限确认和恢复，尚未复刻 Codex TUI
的全部交互。独立配置目录也不会自动继承正常 Codex 的插件和 MCP 配置；前端任务所需的
浏览器工具需要在该独立环境配置。本版没有单独实现浏览器或视觉验证器。

## 持续运行与经验

Leader 回合结束后，目标仍未完成时继续执行；预算耗尽、需要用户信息、故障或显式暂停时停止。
执行和经验整理期间默认抑制系统闲置休眠，没有活动任务后释放。macOS 使用 `caffeinate`，Linux 使用可用的
`systemd-inhibit`。这不是后台守护进程：关闭终端会暂停；合盖、关机或网络故障仍会中断。

默认 `/learn-mode auto`：RSI 目标完成后自动启动独立总结会话。
`/learn-mode guided`：完成后先等待你的复盘意见，不启动总结模型；输入 `/learn 你的意见` 后，
整理者结合原生任务历史和反馈总结。直接 `/learn` 可跳过补充，按历史总结。
等待复盘的状态和模式随会话保存，重新打开仍会提醒；`/new` 沿用当前模式。
等待时切回 `auto` 会立即开始总结；已经开始总结时需等它结束后再切换。
普通工作结束或任务暂停后也可 `/learn`，可附上测试结果等反馈。
整理者按角色指令从来源历史提炼通用方法，不继续修改工作区；它继承会话权限，不另加只读沙箱。
总结时对照最新目录及本任务上次总结的最新正文，由模型判断归属：同主题合并并保留文件名，
只有适用场景或方法独立时才新增。更新会去重、纠正旧结论，不逐轮追加复盘日志；
没有新增结论时原样返回已有文档，宿主不重复写入。正文建议不超过 1500 字符，
硬上限 4000 字符（标题 80、描述 200）；超长内容不发布，需压缩后重试。
经验库中每个主题只有一个普通 Markdown 文件：`RSI_DATA_ROOT/experience/<主题>.md`，
例如 `browser-game-validation.md`。Agent、`/docs` 和总结通知使用同一路径，更新直接覆盖，
不保留历史版本、哈希目录、编号文件或软链接；文件名使用有实际意义的小写英文词组。
普通工作/Leader 每个新回合刷新文档目录，按适用范围读取最新原文；
不复制之前任务的对话、代码或答案。已进入对话的旧内容不会被自动抹去。
Interaction 专注整理需求，不把经验建议升级为任务约束。
总结失败会显示错误，`/learn` 可重试；同一任务重复触发默认复用已发布结果。
附上新反馈会重新总结；若上一轮仍在运行，会提示等待完成后再提交，不会静默丢弃反馈。

`RSI_AUTO_LEARN=0` 让新会话默认使用 guided 模式，`RSI_USE_EXPERIENCE=0` 关闭新会话参考文档，
`RSI_KEEP_AWAKE=0` 关闭休眠抑制。总结是额外 API 调用，其回合最多等待 240 秒（连接请求另有超时）；
普通任务不会逐回合自动总结。
文档仍需结合当前任务验证，单次经验不保证正确。

## 命令行与开发

```bash
uv run --locked rsi doctor                         # 只检查原生协议，不调用模型
uv run --locked rsi run '检查项目并总结结构'          # 单次普通工作，输出 JSON 事件
uv run --locked rsi run --resume run-ID '继续检查'   # 延续原生会话
uv run --locked rsi learn --resume run-ID '复盘反馈'  # 独立总结
make check                                        # Python/原版 UI 测试、Ruff、Mypy
```

`run` 在完成时返回 0，失败返回 1，需要交互、暂停或预算停止时返回 2。

代码入口：`infra/research.py` 的 `ResearchSystem`。详细说明见
[结构与上下文管理](docs/architecture.md)、[当前 API 与未来契约](docs/interfaces/README.md)、
[测试说明](tests/README.md)、[半小时代码导读](docs/walkthrough.md)、
[TUI 选择与来源](docs/tui-comparison.md)、[真实 API 经验实验](docs/experience-study.md)。
