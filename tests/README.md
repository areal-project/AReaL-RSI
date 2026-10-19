# 验证

`make check` 执行全部离线 Python/Node 测试、Ruff 和严格 Mypy。

- `test_research.py`：角色授权、交接、预算、中断、断线、不确定投递、恢复与独占。
- `test_codex_native.py`：启动真实 Codex app-server，模型端使用本地 Responses fixture。
  检查文字、助手回复、工具结果、图片在恢复和角色交接后的实际模型输入；检查真实命令执行、
  自动续行、完成和中断后不重复已完成操作；验证默认跨工作目录写入，以及显式只读权限的继承。
  不调用外网模型，不读取正常 Codex 登录。
- `test_tui.py`：真实 PTY 驱动 DSH TUI → Python → 原生 Codex，覆盖输入、`/rsi`、上下文、退出和重新打开。
- `ui/dsh/test.mjs`：原文件/补丁哈希，xterm 上的原版流式组件、工具展开、输入与权限/问答弹窗。
- `test_experience.py`：固定快照、并发发布冲突和文档路径边界；原生测试另验证新会话只获取文档。
- `test_keep_awake.py`：macOS 实际系统休眠断言及退出释放；其他平台跳过这项集成测试。
- `test_architecture.py`：API/infra/UI 依赖方向和协调状态边界。
- `test_training.py`：独立 TIR 数据合成与答案校验、真实子进程的失败/超时/中断、
  评测完整性、轨迹索引、训练产物新鲜性和分片完整性；不模拟训练成绩。
- `test_search_layout.py`：在源码目录外运行 Search 各入口的 `--help`，检查迁移后的包路径及 GPU 可选依赖隔离。

本机需安装 Node >= 22 和 Codex >= 0.162.1 才能执行原生协议测试；缺少二进制时会明确跳过。
真实 API 冒烟测试需另行使用本地 `.env`，不进入默认测试集，不将密钥写入 fixture。

`tests/experience_e2e/run.py` 是显式启动的真实 API 实验，不在 pytest 自动发现范围中。
它比较同一任务在无经验和读取经验时的行为，使用项目 `.env`，会调用付费 API。
任务与评分器分别为 `experience_e2e/task.md` 和 `grade.py`。每次使用新的标签与目录：

```bash
uv run --locked python tests/experience_e2e/run.py baseline-01 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000 --learn
uv run --locked python tests/experience_e2e/run.py docs-01 --root /tmp/rsi-new-study --seconds 1200 --tokens 250000 --use-docs
```

这是新实验入口，不复现旧会话或证明经验必然有效；比较时应固定任务、模型、预算和评分器，
保留失败结果，并检查评分器是否覆盖真实任务要求。

- test_problems.py：真实 CPU artifact 任务的完整研究、缓存校验、显式重试、工具授权与最终验收。
- test_slurm.py：文件状态驱动的真实 scheduler-client 子进程，覆盖 held 回执、release 响应丢失和禁止重提。
- test_problem_cli.py：统一 CLI、无需模型的状态查询和独立最终评测。

- test_problem_native.py：真实 app-server 与离线模型端点，验证绑定任务的工具继承、后台反馈和完成校验。

GitHub CI 固定 Python 3.12.12、Node 22.14.0、uv 0.9.11 和 Codex 0.162.1，
在 Linux/macOS 执行 `make check`、构建和源码目录外 wheel 安装检查。CI 拒绝原生测试被跳过；
只允许对应平台不适用的测试跳过。localhost 通过 `NO_PROXY` / `no_proxy` 直连，
本地使用代理时也应设置 `localhost,127.0.0.1,::1`，避免离线 HTTP fixture 被代理。
