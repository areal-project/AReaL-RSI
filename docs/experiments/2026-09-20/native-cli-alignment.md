# 研究员执行入口对齐 goal baseline

2026-09-20。目标是对齐单个研究员日常实现、评测、调试、等待和读取反馈的方式。
不改变协作版本、不启动新研究，也不重新运行历史 baseline。

## 研究员看到的流程

| 操作 | Goal baseline | 新 Codex 研究 |
|---|---|---|
| 编辑源码 | Codex 原生文件/命令工具 | 相同 |
| 评测 | `exec_command` 执行 `kb eval model_new.py` | 相同 |
| 调试 | `exec_command` 执行 `gpuq run -- python repro.py [args]` | 相同 |
| 短暂等待 | `yield_time_ms=1000`，返回进程 session ID | 相同 |
| 后台运行 | CLI 继续等 GPU，agent 可继续工作 | 相同 |
| 读取进程结果 | `write_stdin` | 相同 |
| 按作业 ID 查询 | `kb wait` / `kb logs` | 相同命令形式 |
| CLI 默认等待 | 240 秒，超时后保留原作业 ID | 相同；也支持 `--wait 0` |
| 作业索引 | `kb status` | 同命令，显示本人最近十次作业 |

这使“GPU CLI 正在等待”和“模型必须等待工具返回”分离。CLI 通过 `run_job(wait=false)` 提交，
在服务端冻结输入完成后打印 `Submitted JOB_ID`。后续等待在 CLI 后台进程进行，
不会主动结束原生 turn、压缩上下文或重新发起一轮模型调用。
研究员在收到提交确认前不能修改本次输入；若一秒时尚未收到确认，应先读取原进程。

CLI 每次向服务端等待最多十秒，以便带回新消息；整个 CLI 最多等待 240 秒。
服务端等待期间按一秒间隔检查作业，不每秒重建一条完整工具调用、动作记录和恢复状态。
该过程仍依赖 RSI 主控服务，因此不是直接绕过主控访问 GPU 队列。

## 保留的 RSI 职责

- 同一作业管理器负责冻结输入、排队、取消、归档、可信成绩登记与个人有效时间。
- CLI 使用现有 `rsi-tools` 的研究员能力凭据，沿用原角色和 run 边界；不向 CLI 提供 SSH 或 GPUQ 凭据。
- 模型的 MCP 工具列表隐藏重复的提交/等待入口；报告、指导、原始证据查看和消息确认仍可用。
- CLI 返回运行中收到的团队消息，但不自动确认。只有模型调用 `acknowledge_messages` 才会确认。
- 持续探索和结束条件仍由 RSI 管理，没有开启另一个原生 goal 或 subagent。

## 实现边界与复现

核心只增加通用能力：将任务提供的 Python CLI 安装到当前 Runtime 的 PATH，
允许后台 CLI 显式选择作业等待时长，并隐藏重复的模型工具入口。
所有 `kb/gpuq` 命令翻译、题号、诊断脚本参数与使用说明都位于 `examples/kernelbench_artifacts/`。
Runtime 继续使用 AReaL Agent Runtime，没有另起 Codex SDK 或模型调用循环。

`prepare` 对新 Codex 研究默认选择 `execution_interface="cli"`；standard 仍用 `tools`。
任务 CLI 源码冻结在 run 的 `setup/agent-cli.py`，当前会话将它复制进沙箱。角色 Prompt 和协作模块不变，
工具说明随执行入口选择。想复测旧入口，可显式传 `execution_interface="tools"`。
恢复历史研究时沿用其配置，不自动更换入口；对已准备研究显式更换入口会报错，要求创建新研究。

## 验证与尚未证明的部分

本地测试使用真实 AReaL Runtime/Codex 原生工具、本地模型服务和模拟作业，不调用付费模型或 GPU。
覆盖后台 session、提交快照、作业未结束时继续修改、同一作业完成、调试参数、消息不被自动确认，
以及研究停止后拒绝新提交。另检查新旧入口配置和冻结文件。

已有 baseline 可以继续作为对照，不必为 CLI 接口另跑一份 baseline。
但接口一致不等于整个系统吞吐已经相等：RSI 仍有必要的状态管理、隔离和协作处理，
原生 goal 与 RSI 持续监督机制也不同。下一轮应复用原 baseline，记录新研究每人的
外层工具阻塞、输出量和实验次数，检查实际差距是否缩小。
本次没有得到新的 kernel 成绩，不能提前宣称 pass@4 已达到四份 baseline 的探索效率。
