# Isolated pass@k

设置 `ResearchConfig.mode = "passk"`，`brief.limits.max_agents` 即固定的 k。
省略 mode 时默认使用 `passk`。后端仍可选 `standard` 或 `codex`，都使用
AReaL Agent Runtime。这个模式只实现独立尝试，不包含信息共享或 evidence 审核。

工作台支持在创建研究或启动前选择协作方式，并冻结模块版本；通过“用此配置新建研究”
复用选择。`leader_guidance` 可增加 RSI 协调，不启用时保持纯 pass@k。配置和扩展方式见[协作与消融](collaboration.md)。

## 行为

RSI Agent 调用 start_researchers，按同一份完整任务和初始文件启动固定的 k 个 researcher。
每个 researcher 自行完成调研、实现、实验和改进。RSI 是实际模型 agent，但只监督持续探索：
可以查看进展、接收报告，并通过 continue_researcher 提醒停下来的研究员在原会话继续。
这个工具不接受建议或引用，只发送固定的继续提醒。RSI 不能调整方向、淘汰尝试或共享产物。

submit_report 记录进展并通知 RSI，不结束当前研究回合。研究员自然结束回合时，系统归档其
总结并通知 RSI；由 RSI 提醒继续，不创建新研究节点。RSI 等待期间不消耗模型调用，遗漏
启动或继续事项时系统最多每 30 秒提醒一次。执行故障按退避间隔恢复原任务和会话，不创建
额外样本；模型原生重试仍由 Runtime 负责。

`run_job(wait=true)` 和 `wait_for_job` 在当前原生回合内等待并返回反馈，不再在一秒后
强制结束回合。单次等待最多 240 秒，仍未完成时返回原作业 ID，继续等待即可。
日常续接只附带当前预算；进程重启时才补充恢复状态，且不重复完整任务和不断增长的引用列表。
初始 developer prompt、原生会话历史和 compact 仍由 Runtime 管理。

每个尝试只能访问自己的工作区、候选、作业和验证记录，以及共同起点和固定执行说明。
不开放同伴交流、evidence 写入或跨尝试读取。向 RSI 的报告不转发给其他研究员。
恢复状态只包含自己的最好结果。
撤回候选只影响自己，即使另一尝试的文件内容恰好相同，也不会连带剪枝。
工作台仍可供操作员查看全部尝试，研究开始后不接受聊天干预。

工作台每个节点对应一个完整的独立尝试：成绩显示本节点仍有效的探索阶段最佳正式结果，
与整组的最终独立复测分开；历时从节点创建计至停止，包含思考和等待作业的时间，报告不
截断计时。节点显示报告次数，详情按时间列出全部报告和送达状态。截止时尚在 RSI 收件
队列的报告保留并标明未送达，不会延长实验。旧研究的报告动态从已有记录恢复展示。

整组在以下任一条件下停止：

- 任一候选在当前契约的 search 正式验证中通过正确性检查，并达到目标指标。
- 从研究启动算起达到 `max_wall_seconds`。启动开销、暂停和进程停机时间都计入；恢复不续杯。
- 操作员明确停止。

模拟结果只属于模拟实验。诊断成绩、错误契约、失败验证和模型自报成绩不会触发达标。
使用历史最佳有效候选交付，不能用最后一次较差结果覆盖。配置了 finalizer 时，停止探索后
仍按原流程独立复测和归档；达标事件记录的是 search 结果，最终交付是否确认仍以复测为准。
只有时间限制时，可把 verification.target 设为 null；不限时时必须配置可信 verifier 和目标。

## 配置与启动

从已有题目的完整操作员配置复制一份，保留模型连接、seed、命令、verifier、finalizer 和
验证契约，只修改以下字段（JSON 片段，不是完整执行配置）：

```json
{
  "mode": "passk",
  "collaboration_modules": [],
  "backend": "codex",
  "model": "gpt-5.6-luna",
  "reasoning_effort": "xhigh",
  "context_window_tokens": null,
  "brief": {
    "limits": {
      "max_agents": 4,
      "max_nodes": null,
      "max_total_tokens": null,
      "max_verify_calls": null,
      "max_wall_seconds": 3600,
      "max_jobs": 4
    }
  }
}
```

模型、token 和验证次数必须显式不设整组共享上限，防止一个尝试耗尽其他尝试的预算。
`max_jobs` 是执行并发容量，不是累计提交数，应匹配实际 GPU/运行器资源。
所有执行条件必须在启动前准备好；本模式不会等待 leader 配置环境。
运行器在调用模型前检查沙箱中的 shell、临时目录和 `rsi-tools`。任务工具可用
`rsi-tools list` 查看，用 `rsi-tools call TOOL 'JSON_ARGUMENTS' CALL_ID` 调用。

```bash
RSI_DATA_ROOT=.rsi/passk uv run --env-file .env --locked rsi research --config /path/to/passk.json
```

程序中断后使用同一数据目录和 `rsi research --resume RUN_ID`；截止时间不变。
已结束的实验需要新建，不把恢复操作计作原来的 pass@k。
配置文件可通过工作台现有的操作员配置入口加载，无需新的执行服务。

## 比较实验

先以 k=1 检查持久探索、正式验证和恢复行为，再与 k>1 比较。两边固定题目、初始代码、
模型、推理强度、工具、目标和计时口径。记录各尝试的最好正式分数、首次达标耗时、
总 token、验证次数及实际 GPU 容量。共享 GPU 队列会改变每个尝试实际获得的实验时间；
不能只比较同一个墙钟时长而忽略总资源。

这里增加了 RSI 监督调用，比较时应单独记录其开销。它与 Codex 原生 goal pass@1 的提示和停止机制
并非完全相同。工程测试验证独立性和执行行为；是否优于 goal pass@1 要通过实际重复实验
检验，单次 pass@k 不保证必然胜出。

当前 KernelBench pass@k 默认使用 Luna/xhigh，Codex CLI 0.153.4，保留 Codex 自己的
默认上下文窗口。新的诊断作业在执行脚本前关闭 matmul 和 cuDNN 的 TF32，与正式验证
采用相同的 FP32 参考精度。与 goal baseline 一样，禁止 autotune、批量配置扫描和自动
调参循环；每次根据反馈决定下一项实验。正式结果除 speedup 外还记录候选/参考耗时和
标准差，用于检查参考计时异常。原始评分、历史最佳选择和 seed 137 最终复测规则不变。

`metadata.budget_clock = "wall"` 明确标记当前停止口径。这不等于 baseline 的有效时间预算：
后者扣除了本地提交到 GPU 开始的等待，以及已记录的 API 故障等待。对照时必须将各尝试
与 baseline 截到相同有效时间，再比较探索期正式结果；不能把最终独立复测混进搜索曲线，
也不能将首次正确后的结果倒算到提交时刻。会话启动、正常思考、工具执行、编译和 compact
均计入主要比较口径。Luna baseline 的 L3P10 是 2 小时有效预算，其他题是 1 小时。
这次修复不改变停止时钟，也不新增扣时规则；完成工程修复后先跑 k=1 对照，再跑 k=4。
