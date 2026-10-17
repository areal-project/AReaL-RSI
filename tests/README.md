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
- `test_training.py`：真实子进程的失败/超时/中断、评测完整性、轨迹索引、
  训练产物新鲜性和分片完整性；不模拟训练成绩。

本机需安装 Node >= 22 和 Codex >= 0.162.1 才能执行原生协议测试；缺少二进制时会明确跳过。
真实 API 冒烟测试需另行使用本地 `.env`，不进入默认测试集，不将密钥写入 fixture。

`tests/experience_e2e/run.py` 是显式启动的真实 API 实验，不在 pytest 自动发现范围中。
方法与全部结果见 [经验实验](../docs/experience-study.md)。
