# KernelBench 上的 Harness 优化

Research Agent 修改通用 harness，固定的 target 模型通过 DeepSeek Harness 解题，再由独立 GPU 工具评测。
`seed/` 只有场景通用的 prompt、skill 和 MCP 配置，不放具体题目的解答。
初始 harness 与候选 harness 都要测同一组 search 题，才能判断提升；PyTorch reference 延迟与这个初始 harness 分数是两种不同的参照。

## 模块边界

- `areal_rsi.adapters.kernelbench prepare`：从固定 Git commit 导出题面，建立互不重叠的 development/search/final 清单。
- `generate`：只读取 `RSI_TARGET_*` 连接配置，调用真实 DSH；每题独立工作区，保存轨迹和 target 写出的 `model_new.py`。不解析聊天回复中的代码。
- `evaluate.py`：独立 Python 3.10 GPU 环境，调用官方 KernelBench Python API，写结构化结果。stdout 只作诊断。
- `aggregate`：要求全体题目各有一条结果，计算 `fast_1`、正确率和编译率。
- `run.py`：适合调度器调用的可信入口，串起固定 target 和 GPU 验证，保存 `output/evaluation.json`。

程序保留现有 DSH 的 macOS sandbox。当前可直接组合 **Mac 主控 + SSH Linux GPU**；没有通过取消隔离来实现 Linux 主控。
如果要把主控也部署到 Linux，需要另接 DSH 的隔离实现。GPU 密钥和目标模型 API 密钥不会复制到候选 harness。

## 1. 准备固定题目

从 [官方 KernelBench](https://github.com/ScalingIntelligence/KernelBench) 获取固定版本：

```bash
git clone https://github.com/ScalingIntelligence/KernelBench.git .rsi/KernelBench
git -C .rsi/KernelBench checkout 423217d9fda91e0c2d67e4a43bf62f96f6d104f1
uv run python -m areal_rsi.adapters.kernelbench prepare \
  --repository .rsi/KernelBench --config examples/kernelbench/config.json \
  --output .rsi/kernelbench-prepared
```

`config.json` 中的题目是最小接线样例，每个池只有一道题，不能用于宣称通用改进。用户启动 run 时决定正式池、资源与预算。
准备工具直接读取固定 commit 的 Git blob，不消费修改后的 checkout 文件；随后用 SHA-256 检查输入。已有目录不能覆盖。

```text
kernelbench-prepared/
  development/manifest.json
  development/references/
  private/search/manifest.json
  private/search/references/
  private/final/manifest.json
  private/final/references/
  protocol.json
```

Research Agent 可以消费 development 题面、轨迹和开发结果。search/final 的题面、task 工作区及原始轨迹留在受信任侧，研究过程只获得约定的聚合反馈。
固定 target 在解每道 search 题时当然会看到该题的 reference；不能把该题交给更强 Research Agent 代写 kernel。

## 2. 准备模型与 GPU

目标连接只通过本机环境变量提供：

```bash
export RSI_TARGET_MODEL='<exact target model name>'
export RSI_TARGET_BASE_URL='<target OpenAI-compatible chat endpoint>'
export RSI_TARGET_API_KEY='<configured locally; never commit this value>'
```

此 Task executor 使用 DSH 的 Chat Completions 工具协议；不将 RSI/Research 使用的 Responses 模型连接自动用作 target。
本地代理绑定目标模型、每题最多请求数和每请求输出 token 上限。重试也计入请求数；总生成 token 上限是两者乘积。输入 token 和实际费用需要另外计量。整个任务还有墙钟时间上限。

GPU 主机上的 KernelBench 同样 checkout 固定 commit，并使用独立 Python 3.10 环境；当前主库 Python ≥3.11 与这个上游环境分开。

```bash
git clone https://github.com/ScalingIntelligence/KernelBench.git /work/KernelBench
git -C /work/KernelBench checkout 423217d9fda91e0c2d67e4a43bf62f96f6d104f1
uv venv /work/KernelBench/.venv --python 3.10
uv pip install --python /work/KernelBench/.venv/bin/python -e /work/KernelBench
```

该版本要求 torch ≥2.9；需在 GPU 环境安装匹配驱动的 CUDA torch wheel、CUDA 编译工具链，Triton 后端还需要相应 Triton 安装。GPU evaluator 会检查 Python 版本、固定 checkout、上游评测源码是否修改以及 CUDA 是否可用。
一张 GPU 同时只运行一份正式计时任务，资源排队由外层调度器处理。

## 3. 调度器推荐入口

复制 `operator.example.json` 到操作员配置目录，填入绝对路径和 SSH host alias。配置不放到 Research Agent 的可编辑候选里。
`gpu.kind=ssh` 通过已有 SSH key/agent 访问主机，无密码交互；传输的只有固定题面、target 输出和可信 evaluator。
远端作业目录会保留，位置归档到 `output/private/remote-job.json`。连接通过远端终端传递挂断，evaluator 收到终止/挂断时终止其活动 worker 进程组；实际 GPU 主机仍需做取消和重连验收。

```bash
uv run python examples/kernelbench/run.py \
  --harness examples/kernelbench/seed \
  --output .rsi/kernelbench-baseline-search \
  --config /absolute/path/to/operator.json --phase search
```

调度器命令模板可以是：

```json
["python", "examples/kernelbench/run.py", "--harness", "{workspace}", "--output", "{output}", "--config", "/operator/kernelbench.json", "--phase", "{phase}"]
```

另配一个固定开发集命令，让 Research Agent 在正式评测外读取解题轨迹：

```python
ApprovedCommand(
    name="develop",
    command=("/absolute/rsi/.venv/bin/python", "/absolute/repo/examples/kernelbench/run.py",
             "--harness", "{workspace}", "--output", "{output}",
             "--config", "/operator/kernelbench.json", "--phase", "diagnostic"),
    parameters=("workspace", "output"),
    gpu_count=1,
    output_subdir="public/development",
)
```

只有显式 `diagnostic` 才发布 `output/public/development/`，其中 `summary.json` 索引每道开发题；`tasks/<id>/` 包含生成状态、开发评测结果、存在时的 target kernel/输出及已归档轨迹（包括失败轨迹）。它们是开发证据，不更新正式 best。公开目录不包含 session 配置和私有 CAS；`search`、`final` 不生成这个目录，不能通过该命令读取其轨迹。diagnostic 映射到任何私有题集会被拒绝。完成开发命令后，先调用 `read_job_output_file(job_id, path="")` 列文件，再读取 `summary.json` 和需要的轨迹；返回的材料 URI 可直接用于 report references。这样不会把开发资料拷进仅允许修改 skill 的候选工作区。

默认 phase 映射：`diagnostic → development`、`search → search`、`final → final`。兼容旧的 `validate` 参数和配置别名，输出统一记录为 `search`。`operator.json` 固定映射、每题预算、precision/backend 和试验次数。
用户只允许 skill 修改时，配置 `edit_scope=skills` 和 `baseline_harness`；工具检查实际文件差异，修改 prompt/tools/mcp 会被拒绝。`harness` 范围则允许这些已有组件。

真实 run 启动前先固定每个 phase 的评测身份。下面的命令通过已配置的 local/SSH GPU 环境检查 revision、CUDA、torch 和预算，不调用模型、不执行候选、不产生分数：

```bash
uv run python examples/kernelbench/run.py --describe \
  --config /absolute/path/to/operator.json --phase search --target-model FIXED_TARGET_MODEL
```

对 `search`、`final` 和需要的 `diagnostic` 分别执行。将输出的 `protocol_id`、`manifest_id`、`target_model`、`split` 写入 ResearchConfig 的 `metadata.verification_identity.phases.<phase>`，并设置 `metadata.verification_identity.adapter="kernelbench"`。`environment` 用于操作员审计，不放入上述比较字段。身份读取器会把真实输出与这些固定值、该作业的 candidate 内容和提交时契约分别校验；GPU、版本、题集或预算改变后应建立新的比较配置。

输出 `evaluation.json` 包含：

```json
{
  "status": "completed",
  "metric": "fast_1",
  "score": 0.0,
  "metrics": {"fast_1": 0.0, "correctness": 0.0, "compiled": 0.0, "task_count": 1},
  "simulated": false,
  "phase": "search",
  "split": "search",
  "manifest_id": "...",
  "protocol_id": "...",
  "harness_id": "...",
  "target_model": "..."
}
```

这是格式示例，没有声称运行所得的分数。`status=incomplete` 时 `score` 和三个比例是 `null`，不能更新 best。
分母包含所有选定题：错误答案、编译失败、无输出、预算耗尽是未通过；模型服务/环境/worker 故障属于 `infra_error`，会使本次聚合不完整。重复、缺失、混合协议或非有限时间直接报错。

`run.py` 的初始种子没有配置 GPU 开发工具；target 可以用 DSH 编辑文件，不能凭空测试 CUDA。
用户有受控的 GPU 开发工具后，可通过现有 harness MCP 接口加入，让 target 在单题预算内编译和调试；正式 verifier 仍使用固定题面重新评测。这个限制应在初次真实实验前确认。

## 分步执行与离线 mock

需要单独排查时可先运行 `generate`，然后在 GPU 主机调用 `evaluate.py`，最后在主控 `aggregate`。三个命令的 `--help` 给出全部参数。
`run.py` 使用 `gpu.kind=mock` 时仍调用真实 target，仅 GPU 结果明确模拟；可以在 `gpu.outcomes` 中按 task ID 设置 `correct_fast / correct_slow / wrong / compile_error / infra_error / no_output`。
target 生成失败不会被模拟 GPU 转成成功。

完全不调用模型的协议 mock：

```bash
uv run python -m areal_rsi.adapters.kernelbench mock \
  --manifest .rsi/kernelbench-prepared/development/manifest.json \
  --harness-id synthetic-harness --output .rsi/mock-kernel-results.json
uv run python -m areal_rsi.adapters.kernelbench aggregate \
  --manifest .rsi/kernelbench-prepared/development/manifest.json \
  --results .rsi/mock-kernel-results.json --output .rsi/mock-kernel-score.json
uv run pytest tests/test_kernelbench.py
```

模拟记录固定 `simulated=true`，只用于调度、报告、等待和界面测试，不能当性能提升证据。
实际 CUDA、目标模型接口和 SSH 取消要在用户资源到位后验收。

## 验证边界

GPU evaluator 默认开启上游静态检查、使用随机输入检查正确性、独立计时并标记异常加速。
它没有把上游 CLI 的 stdout 当作分数，但上游仍在同一个 Python 进程中加载生成代码和 reference，静态检查也不完备。当前适合流程演练和真实预实验，不能宣称恶意 kernel 已与评测机制隔离；有意篡改同进程 Python 状态仍属于未解决的边界。正式抗对抗评测需要另行实现并验收更强的执行/评分隔离。可信 evaluator 文件的 SHA256 纳入 protocol_id，修改 wrapper 会改变比较身份；这可以发现评测程序漂移，不等于阻止候选代码攻击它。
运行它的 GPU runner 应使用隔离容器、受控挂载及无网络执行权限；候选无权改写 evaluator、正式题面和结果目录。最终提升还需独立 final 池确认。
