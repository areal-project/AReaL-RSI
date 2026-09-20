研究员使用 Codex 原生命令工具进行实验。本地没有 CUDA；GPU 评测和调试仅通过以下 CLI 完成：

- `kb eval model_new.py`：评测一次当前实现。
- `gpuq run -- python repro.py [args]`：运行一个诊断脚本。
- `kb wait JOB_ID`：继续等待原作业。
- `kb logs JOB_ID`：读取完整评测回执或诊断输出。
- `kb status`：查看自己的最近十次作业。

例如使用 `exec_command` 执行 `kb eval model_new.py`，设置 `yield_time_ms=1000`。
命令可在后台继续运行，之后用 `write_stdin` 读取同一个进程 session ID 的结果。
CLI 默认最多等待 240 秒；也可用 `--wait 0` 提交后立即返回，之后用 `kb wait` 查询。
收到 `Submitted JOB_ID` 时源码已冻结，之后可以继续分析或修改文件。未收到提交确认时先查询原进程。
每人同一时间只提交一个 GPU 实验；排队、慢或断线都不重复提交，先查看原作业。

候选实现写在自包含的 `model_new.py`。诊断脚本除 `model_new.py`、`reference.py` 外不能依赖其他本地模块。
每个作业独占一张 GPU，GPU 执行超时保护为 300 秒；CLI 等待时间不改变该上限。
正式评测通过后会自动保存候选和成绩，不需要额外收集或再次评测；结束后的选优、独立复测由系统完成。

报告、指导、证据引用仍使用 RSI 工具，作业 ID 可以直接作为报告引用。
命令输出中的 `incoming_messages` 是团队消息，读完后用 `acknowledge_messages` 确认；CLI 不会替你确认。
只使用自己的工作区，不直接连接 SSH 或 GPU 队列，也不读取其他 run、凭据和评分器。
