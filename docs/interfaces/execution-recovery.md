# 执行环境变化与研究恢复

验证失败需要区分研究结果与运行环境。`infra_error` 没有可比较分数，不能作为目标无法达到的证据。RSI 先读取公开诊断；有限检查仍不能解决时，使用 `ask_user` 说明问题并停止推理等待。系统拒绝在最新验证仍为 `infra_error` 时由 RSI 调用 `stop_run`；用户明确停止仍然有效。

用户可以通过工作台「恢复研究」接续已停止的 run。成绩、候选、报告和会话保持原身份；RSI 再决定下一项任务。已完成作业不重放，取消的工作和单独停止的研究方向仍归档保留。

## 固定执行源码

比较条件应绑定经过审查的执行源码副本，避免每轮直接依赖持续修改的共享工作目录。副本由操作者管理，放在 run 的执行环境中，不能放进候选或允许研究员修改。改变实际评分、数据、模型或硬件条件时仍须处理比较有效性，不能仅因文件变化看似很小就自动放行。

`execution/pinned_sources.py` 是可直接复制到远端的独立工具。它先核对副本 SHA256，然后在 Linux 私有 mount namespace 中将指定文件只读覆盖到原路径，最后运行 Python 命令；宿主和其他进程看到的文件不变。这样不需要修改原脚本中的绝对路径和导入关系。

```json
{
  "root": "/srv/experiment",
  "python": "/srv/experiment/venv/bin/python",
  "files": {
    "runner.py": {"path": "/srv/run/pinned/runner.py", "sha256": "<审查后的 SHA256>"}
  }
}
```

调用：`python pinned_sources.py CONFIG.json -- SCRIPT.py ARG...`。配置与源码副本均为操作者文件。需要 Linux、util-linux 和挂载权限；启动失败必须保留诊断，不能退回未校验的共享版本。该工具只隔离指定源码文件，不固定外部 GPU 服务、依赖库、数据或硬件；这些仍由任务的执行协议管理。

当前 KernelBench 恢复使用 baseline 归档里的 `full_eval/runner.py` 副本，其他原有源码校验继续执行。v2 候选、题池、模型、预算、计时精度、评分及验证身份未改变。原接入、失败记录和维护变更说明都保存在同一个 run 中。

## 公开启动诊断

执行器可以在 `{output}/diagnostic.json` 原子写入：

```json
{
  "code": "EXECUTION_PREFLIGHT_FAILED",
  "stage": "preflight",
  "message": "固定运行器副本不可用",
  "action": "恢复副本后重试同一候选"
}
```

只写可对 Agent 和用户公开的信息，不包含凭证、隐藏评测数据或原始日志。文件最大 16 KB，字段有长度上限；格式异常、超限或符号链接会被忽略。ResearchApp 在失败验证记录中采用这个说明，不从私有 stdout 自动提取内容。原始日志继续仅供授权维护者排查。已有失败结果不回写，恢复后创建新的验证作业。
