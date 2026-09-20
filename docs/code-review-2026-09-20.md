# 架构与实现审查：2026-09-20

这是目录迁移前的历史审查记录；旧名称不代表当前结构，现行布局见[架构说明](architecture.md)。

本轮检查了 ResearchApp 的入口、编排与执行边界，重点审查候选存储、
调度器所有权、后台进程资源回收，以及目标模型请求预算。
以下 5 项是已确认并修复的问题；不代表对全部代码或 vendor 依赖的无缺陷保证。

## 改动清单

| 编号 | 优先级 | 问题与影响 | 修复 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | P2 | 不存在的快照来源被保存为空候选；根目录符号链接未检查，FIFO 等特殊文件被静默丢弃 | 在存储边界检查真实目录，并使用 lstat 拒绝符号链接和特殊文件 | 已修复 |
| 2 | P2 | 恢复快照途中遇到缺失或损坏的内容，已写入的部分文件仍留在目标目录；重试被 FileExistsError 阻止 | 仅在本次调用成功创建目标目录后承担清理责任，失败或取消时删除本次半成品；保留预先存在的目录 | 已修复 |
| 3 | P1 | 监督进程等待主进程退出后直接发布 resources_released=true，后代进程仍存活时也可能释放 GPU | 有界等待整个进程组消失；无法确认时记录 lost 并保留资源声明，由既有恢复逻辑继续核查 | 已修复 |
| 4 | P1 | 调度器初始化或关闭期间持久化失败会遗留执行锁；有效时间模式下重复 close 会再次保存旧状态，覆盖新所有者的数据 | 统一释放所有权，初始化失败和关闭保存失败均释放锁；释放时清除计时器，重复关闭不再保存旧状态 | 已修复 |
| 5 | P1 | TargetGateway 在代理外计数，但 GatewayProxy 一次请求可内部尝试六次，绕过 max_requests | 将代理尝试次数做成配置；目标模型代理仅尝试一次，重试必须重新经过预算入口；普通代理仍默认六次 | 已修复 |

## 代码与回归证据

1. `src/areal_rsi/base/storage.py`：`snapshot`。
   `tests/test_storage.py::StorageTest::test_snapshot_rejects_invalid_roots_and_special_files`
   覆盖缺失目录、普通文件根路径、符号链接根路径和 FIFO。原实现未抛出预期异常。
2. 同文件的 `materialize`。
   `test_failed_materialization_can_be_retried_without_partial_files` 删除第二个内容块，
   验证失败后无半成品、补回内容后可重试、已存在的目标不会被删除。原实现留下目标目录。
3. `src/areal_rsi/execution/_runner.py`：`run` 的回收阶段。
   `tests/test_jobs.py::test_supervisor_retains_claim_until_entire_group_stops`
   注入主进程已退出但进程组仍存活的故障；原实现错误释放资源。
   同时覆盖进程组已消失的成功路径。测试不需要真实 GPU。
4. `src/areal_rsi/coordination/engine.py`：`_acquire`、`_release_ownership`、`close`。
   `tests/test_research_engine.py` 新增回归覆盖保存失败后的接手、旧调度器重复关闭不覆盖
   新所有者的 token 计数，以及初始化失败后立即可重新获取锁。
   原实现前两种场景分别出现锁冲突和新状态丢失。
5. `src/areal_rsi/modules/gateway.py` 和 `modules/task_execution.py`。
   `tests/test_gateway.py::test_target_request_budget_includes_overload_retries`
   使用本地 HTTP 上游持续返回过载，设置请求额度为 1；原实现产生 6 次上游请求，
   修复后仅 1 次，客户端再试得到 HTTP 429。原有普通代理六次尝试的测试继续保留。

## 架构判断与边界

后续专项审查修正了本节的粗粒度判断，并完成 API 和模块重构，
当前结构见[架构说明](architecture.md)。下文记录第一轮实现审查的结论。

目前 ResearchApp → 编排器 → Runtime / ResearchExecution 的职责划分可以保留。
本轮问题集中在跨层契约：文件是否完整、谁拥有写权限、何时可以释放资源、
一次预算额度对应多少次真实请求。修复均放在拥有相应责任的模块中。

没有修改持久化格式、候选摘要算法或冻结的协作协议；无需迁移已有记录。
快照恢复清理覆盖 Python 可处理的异常与取消，SIGKILL/断电后的输入恢复仍依赖
现有 `ready.json` 协议。本地进程组检查不构成容器隔离，也不声称能追踪主动脱离
进程组的任意子进程。资源无法确认时采用保留配额的保守策略。

## 验证

- 修改前全量 Python 基线：277 passed，19 skipped，63 subtests passed。
- 新增回归先在原实现复现失败，再执行修复后的相关测试。
- 最终全量 `.venv/bin/pytest -rs`：285 passed，19 skipped，63 subtests passed。
  跳过项目与基线一致：2 项 Linux 专用测试、7 项需显式启用的原生 DSH 测试、
  10 项需要安装 anthropic extra 的集成测试。这些路径未在本次 macOS 环境中验证。
- Ruff、Mypy（146 个源文件）通过。
- 前端 `node --test tests/frontend/workbench.test.mjs`：14 passed。
- `git diff --check` 通过。
