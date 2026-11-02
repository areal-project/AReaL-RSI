# Claude runtime 的执行预算收据

本目录为 Qwen3.5-35B 的 Arena RL 准备独立的 Claude runtime 包装器。
保留原 Claude 二进制、参数、stdin/stdout、skills、tools 和 system prompt。
不修改 Arena 平台服务或统一分发的 Go harness。

`claude` 是包装器入口。新 runtime 目录同时放置原始二进制 `claude.real`。
Arena 的 `runtime.uri` 指向整个上传目录，末尾必须有 `/`；平台将两者物化到
`$ARENA_HARNESS_DIR/bin/`。包装器使用 Python 标准库，沙箱需要能执行 Python 3.8+ 的 `python3`。
执行前以分块重试方式将原二进制复制到本地，避免直接在 OSS/FUSE 上执行。

## 超时与结果

- `ARENA_AGENT_TIMEOUT_SEC`：现有 Env 的外层强制时限，必须为正数。
- `AREAL_CLAUDE_DEADLINE_MARGIN_SEC`：默认 30 秒，预留终止进程和收据落盘时间；至少 10 秒。
- `AREAL_CLAUDE_AGENT_TIMEOUT_SEC`：可选；默认等于外层时限减 margin。
  显式指定时也必须给外层留下同样的 margin。

当前 Env 外层为 7200 秒，所以默认内部执行预算是 **7170 秒**。
若要求模型完整执行 7200 秒，应发布一个外层至少 7230 秒的 Env 版本，并显式设置
内部预算为 7200；不能让包装器与外层同时到期，否则外层 SIGKILL 可能阻止收据落盘。

只有包装器自身执行预算耗尽才会终止独立子进程组、原子写入
`$ARENA_OUTPUT_DIR/harness_result.json`，然后退出 124。收据包含：

```json
{
  "status": "ERROR",
  "summary": "AGENT_RUN_TIMEOUT",
  "raw": {
    "schema": "areal-claude-deadline/v1",
    "task_id": "job:case:0",
    "attempt": 1,
    "outcome_code": "AGENT_RUN_TIMEOUT",
    "trigger": "RUNTIME_EXECUTION_DEADLINE",
    "budget_seconds": 7170,
    "elapsed_seconds": 7170.2,
    "child_returncode": -15
  }
}
```

正常结束不生成这份失败收据。Claude 自行退出 124/137、提前崩溃、外部 TERM/INT/HUP
也不生成；重试启动时会移除本包装器上一轮留下的收据，防止旧失败污染本轮结果。

Arena 仍可能将超时任务标为 `HARNESS_FAILED`。AReaL 在显式启用下，通过现有的
`harness_result.json` 产物接口取得完整收据，固定查询当前 attempt，核对 schema、task ID、
attempt、触发原因及时间，避免把前一次重试的收据用到本次任务。
并将其保存在本地任务结果的 `raw.areal_claude_deadline` 中，便于审计。
训练阶段仍要求 Proxy 无系统错误、有可用交互且成功导出轨迹；符合条件才赋予
episode reward=0，并记录 `AGENT_RUN_TIMEOUT` 指标。收据缺失或不匹配时继续拒绝该样本。

## 准备并接入新版本

1. 导出当前 `claude-code-with-skills@5.0.2` 的完整版本 JSON，并取得它使用的原始 Claude
   二进制。不要替换为另一版本的 Claude。当前 runtime URI 为
   `uploads/260728/184e5b2da3c5fb4c/claude`。
2. 运行：

   ```bash
   python3 prepare.py --base-harness /path/to/base-version.json \
     --claude-binary /path/to/original-claude
   ```

   `build/runtime/` 是待上传目录；`build/version.json` 是新版本请求，
   `build/sha256.json` 记录两个可执行文件的摘要。原始二进制不加入 Git。
3. 使用 Arena 上传整个 `build/runtime/` 目录，把返回的目录 URI 写入
   `build/version.json` 的 `spec.runtime.uri`，保留末尾 `/`。
   `REPLACE_AFTER_UPLOAD/` 只是占位值，不能直接发布。
4. 用该 JSON 创建并发布新的 Harness 版本，例如
   `claude-code-with-skills@5.0.2-areal-deadline.1`。脚本会保留原版本其他设置，
   并添加收据的 `io_contract.artifact_globs`，确保 AReaL 能下载产物。
   已发布的 `5.0.2` 保持不变。
5. 在 RL 的 Arena 配置副本中指定新 Harness，并添加调用方开关：

   ```yaml
   econfig:
     arena_streams:
       - name: swe-verified483-setup600
         harness: claude-code-with-skills@5.0.2-areal-deadline.1
         task_envs:
           AREAL_CLAUDE_DEADLINE_RECEIPT: "1"
   ```

   上面仅展示修改字段；`--arena` 文件需要保留原 `arena.yaml` 的其他内容，尤其是
   stream ID、数据快照、协议和 reward ref。Harness 内的环境变量用于沙箱执行；
   这里的 task_envs 开关同时让 AReaL 知道需要读取 deadline 收据。

当前默认 `arena.yaml` 仍使用已存在的 `5.0.2`。代码准备不等于发布：上传目录、
创建/发布新版本并切换配置后，实验才会实际使用包装器。

## 测试

```bash
python3 -m pytest test_runtime.py -q
```

测试使用独立的假 Claude 子进程，覆盖协议转发、自身超时、提前退出、外部取消、
非法预算和重试残留；不请求模型、不创建 Arena 沙箱。
