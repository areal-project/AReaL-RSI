# 运行 Research 工作台

这一入口运行 Interaction Agent、RSI Agent 和保持连续会话的 Research Agents。可以先模拟 GPU 作业检查完整流程，随后换成真实执行配置；原有 `rsi tui / run / resume / web` 入口保留。

## 先跑起来

在仓库根目录安装依赖，并填写本机 `.env`：

```bash
uv sync --locked
cp .env.example .env   # 仅在还没有 .env 时执行，已有配置不要覆盖。
```

新入口使用以下变量；API key 只填在本机，不放进 JSON 配置或 Git：

```dotenv
RSI_BASE_URL=https://your-responses-gateway.example/v1
RSI_API_KEY=<在本机填写>
RSI_MODEL=gpt-6-astra
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/local
```

base URL 填所使用的模型服务地址。入口使用 Responses API，不会自动切换模型或降低 reasoning effort。先检查工具调用、连续上下文和重复消息去重：

```bash
uv run --env-file .env --locked python scripts/preflight_responses.py
```

然后打开工作台：

```bash
uv run --env-file .env --locked rsi workbench --simulate-gpu
```

访问 `http://127.0.0.1:8766`，创建研究，选择 KernelBench 或 CUHK-X 场景。在「需求与对话」中补充优化范围、资源和偏好，确认「研究约束」后点击「开始研究」。界面展示研究方向、各节点的任务和报告、共享证据、作业状态、可比较分数及预算。点击节点查看细节；可以暂停、继续、停止和下载产物。

**`--simulate-gpu` 仍会调用真实研究模型 API。**它只模拟长时间 GPU/训练/验证作业；KernelBench 的工作流演练同时模拟 Task Agent 解题。RSI 和 Research Agents 的规划、工具调用、文件编辑、报告、消息和等待逻辑实际运行。模拟分数和轨迹始终带标记，不能作为性能提升证据。

纯终端运行两个例子：

```bash
uv run --env-file .env --locked rsi research --example kernelbench --simulate-gpu
uv run --env-file .env --locked rsi research --example cuhkx --simulate-gpu
```

例子只是初始目标和资源上限。KernelBench 默认一条研究方向；CUHK-X 最多两条，RSI 自行决定调研、分工和迭代顺序。`--example` 必须配合 `--simulate-gpu`；真实任务使用下方的 `--config`。

## 等待、暂停和恢复

终端每隔两秒读取本地状态并输出有变化的 JSON 摘要，读取状态不会调用模型。Research Agent 提交 report 后停止推理，直到 RSI 明确回复；等待作业时由完成消息唤醒同一会话。RSI 可以在其他 Agent 工作时继续有用的调研，也可以显式等待。

```bash
# 只在前台观察十分钟；到时保存当前状态。
uv run --env-file .env --locked rsi research \
  --example kernelbench --simulate-gpu --timeout 600

# 使用原 run 的配置、节点、消息和会话恢复。
uv run --env-file .env --locked rsi research \
  --resume <run_id> --simulate-gpu
```

`Ctrl+C`、`SIGTERM` 和 `--timeout` 关闭当前 worker，保留可恢复状态，不等于工作台的「停止研究」。再次打开工作台后可以查看并继续已有 run；同一 run 只能由一个 worker 持有执行锁，不能同时在两个终端启动。

需要用户回答、方向失败或达到预算时，终端会给出工作台命令，不会宣称研究完成。退出码：`0` 成功；`1` 失败/停止；`2` 配置错误；`3` 等待交互或暂停；`124` 本次前台超时；`130/143` 收到中断/终止信号。预算耗尽不会自动增加额度或无限重试。

关闭服务后重新打开，已保存但没有工作进程的研究显示「恢复执行」。Agent 单回合调用额度耗尽时会进入失败状态；检查报告和执行记录后，可以点击该 Agent 的「重试」，为同一未完成回合提供一份新的有限额度。已经完成的工具不重放，全局预算不重置；普通刷新、恢复服务或重放已记录的同一重试 ID 不会增加额度。

若 Research Agent 直接回复文本但遗漏 report，「重试」会在原会话、原节点中追加补报告指令；不会为这个协议错误新建研究节点。网络中断仍从原模型回合恢复。

## 换成真实 GPU 作业

执行配置由操作员提供，和候选源码分开存放。`ResearchConfig` 包含任务 brief、可选种子目录、批准的命令模板和独立 verifier。命令里的 `{workspace}`、`{output}`、`{phase}` 由调度器绑定；Agent 不能选择任意可执行程序或修改 verifier。

先按下方两个示例 README 准备操作员配置、真实数据和执行环境，再生成可直接使用的研究配置：

```bash
uv run --env-file .env --locked python scripts/configure_research.py \
  --example kernelbench --operator /operator/kernelbench.json \
  --output /operator/kernelbench-research.json --target-model 'your-fixed-task-model'

uv run --env-file .env --locked python scripts/configure_research.py \
  --example cuhkx --operator /operator/cuhkx.json \
  --output /operator/cuhkx-research.json

uv run --env-file .env --locked rsi workbench --config /operator/kernelbench-research.json
```

KernelBench 的固定目标模型须与 `.env` 中的 `RSI_TARGET_MODEL` 一致。生成器先调用可信 wrapper 的 `describe`，冻结 search/final 的协议、题池与环境身份，再写入配置；这个预检不运行候选、不代表成绩验证。默认最多 12 个节点、200 次模型调用、200 万 Token、6 次验证、1 个并发作业和 4 小时；Research Agent 上限分别为 1 和 2。用相应 `--max-*` 参数调整硬预算，`--target` 设置目标，`--instructions-file` 加入给 RSI 的自由文本要求。API key 只从环境读取。

手工配置时，可参考下面的 KernelBench 结构。路径替换为本机实际绝对路径，目标、正式题池、固定模型和预算由用户决定：

```json
{
  "brief": {
    "objective": "Improve reusable kernel skills for the fixed task model",
    "instructions": "Only edit skills/*.md. Analyze development trajectories, evaluate the initial harness, then compare candidates under the frozen verification contract. Never embed task-specific answers.",
    "limits": {
      "max_agents": 1,
      "max_nodes": 10,
      "max_model_calls": 150,
      "max_total_tokens": 1000000,
      "max_jobs": 1,
      "gpu_ids": ["0"],
      "allowed_paths": ["skills/*.md"]
    },
    "verification": {
      "metric": "fast_1",
      "direction": "maximize",
      "target": 0.7,
      "identity": {"require_final_confirmation": true},
      "description": "Frozen task model, disjoint development/search/final pools, fixed GPU and per-task budget"
    }
  },
  "seed_dir": "/repo/AReaL-RSI/examples/kernelbench/seed",
  "verifier": {
    "name": "verify",
    "command": ["/repo/AReaL-RSI/.venv/bin/python", "/repo/AReaL-RSI/examples/kernelbench/run.py", "--harness", "{workspace}", "--output", "{output}", "--config", "/operator/kernelbench.json", "--phase", "{phase}"],
    "parameters": ["workspace", "output", "phase"],
    "gpu_count": 1,
    "timeout_seconds": 3600
  },
  "metadata": {
    "template": "kernelbench",
    "evaluate_seed_as_baseline": true,
    "verification_identity": {
      "adapter": "kernelbench",
      "phases": {
        "search": {"protocol_id": "<describe: protocol_id>", "manifest_id": "<describe: manifest_id>", "target_model": "<describe: target_model>", "split": "search"},
        "final": {"protocol_id": "<describe: protocol_id>", "manifest_id": "<describe: manifest_id>", "target_model": "<describe: target_model>", "split": "final"}
      }
    }
  }
}
```

该 JSON 的 target 和尖括号内容仅演示配置格式。先运行可信 wrapper 的 `--describe --config /operator/kernelbench.json --phase search`，再对 `final` 运行一次，按各自输出填写 `metadata.verification_identity.phases`，不能从候选输出推断这些身份。身份会固定题池、目标模型和评测协议；启用 `require_final_confirmation` 后，搜索分数达标仍需独立 final 验证。保存为操作员目录中的 `research.json` 后执行：

```bash
uv run --env-file .env --locked rsi workbench --config /operator/research.json
# 或：
uv run --env-file .env --locked rsi research --config /operator/research.json
```

工作台使用 `--config` 时默认运行真实作业；同时传 `--simulate-gpu` 只是允许在创建时切换到模拟作业。没有配置、只传 `--simulate-gpu` 时默认模拟。headless 的 `--simulate-gpu` 则直接选择模拟模式。`seed_dir` 可以省略；没有起点时直接发起研究，不强求 baseline 或候选版本。

两个例子的真实执行准备不同：

- **KernelBench**：按 [示例说明](../examples/kernelbench/README.md) 固定上游 commit，准备互不重叠的开发、search 和 final 池；填写 [GPU 操作员配置](../examples/kernelbench/operator.example.json) 和 `.env` 中的 `RSI_TARGET_MODEL / RSI_TARGET_BASE_URL / RSI_TARGET_API_KEY`。可信 `run.py` 串起固定 Task Agent、DSH 和 GPU verifier。当前 Task executor 的隔离支持 Mac 主控配 SSH Linux GPU；Linux 主控仍需匹配的隔离实现。先真实评测少量题，确认目标接口、GPU、取消和结果归档，再扩展题池。
- **CUHK-X**：按 [示例说明](../examples/cuhkx/README.md) 准备已获准使用的数据、按 subject 划分训练与验证，并准备独立 final subject。`examples/cuhkx/run.py` 提供可信 `train` / `verify` 入口，正式候选在固定 Docker 镜像中运行；训练挂载训练数据，推理挂载匿名验证数据，宿主评分器读取私有标签。配置一个 `train` ApprovedCommand 和一个 `verify` verifier，参数均绑定 `{workspace}`、`{output}`，verifier 额外绑定 `{phase}`。**训练命令必须设置 `output_subdir: "trained"`**，只收集可公开的训练产物。通过作业产物工具导入 checkpoint 后，冻结候选再验证。用 `describe --config ... --phase search|final` 的真实输出填入 adapter 为 `cuhkx` 的冻结身份，其中包括 `prepared_protocol_id`。无需 Task Agent，不自动提交 Kaggle。

CUHK-X 的命令模板形状：

```text
/repo/.venv/bin/python /repo/examples/cuhkx/run.py train
  --candidate {workspace} --output {output} --config /operator/cuhkx.json
/repo/.venv/bin/python /repo/examples/cuhkx/run.py verify
  --candidate {workspace} --output {output} --config /operator/cuhkx.json --phase {phase}
```

命令使用 argv 数组，每个占位符独占一个元素；这不是由 Agent 拼接的 shell 字符串。真实验证必须保持数据划分、评分器、环境和模型预算可比较。GPU 和比赛真实数据尚未到位时，模拟工作流通过不等于 CUDA 或比赛成绩验收通过。

## 代码与数据放在哪里

| 模块 | 职责 |
| --- | --- |
| `agents/interaction/prompt.py` | 原始需求、硬限制与给 RSI 的自然语言说明 |
| `agents/rsi/prompt.py` | 调研、计划、分工、报告决策与经验共享 |
| `agents/research/prompt.py` | 一个方向的连续研究、证据与明确报告 |
| `modules/responses.py` | API、工具循环、连续会话、compact 和逐工具恢复 |
| `coordination/` | 消息和节点状态、调度、预算、工具权限、作业桥接 |
| `execution/`、`adapters/` | 后台进程、资源、取消和可信任务验证 |
| `research_app.py`、`ui/workbench.py` | 应用装配、公共状态投影和本地工作台 |

每次 run 的主要记录集中在 `RSI_DATA_ROOT/runs/<run_id>/`：

```text
run.json
briefs/ assignments/ reports/ evidence/
candidates/ verifications/
agents/<agent_id>/
  agent.json  iterations/  workspace/  sessions/
coordination/                 # 配置、消息投递和调度状态
messages/ events/ jobs/
execution-inputs/ execution-outputs/
exports/                      # 下载产物及其研究结果说明
```

候选文件的不可变内容存入 `RSI_DATA_ROOT/content/`，由 run 内的候选记录引用；run 之间不共享可变 memory/evidence。`ResearchIteration` 是通用节点，不要求每轮生成版本或验证。完成的研究任务有具体 report；旧固定 evolution 流程的 `EvolutionResult` 是可选适配记录，新研究流程不要求它。

会话原文、工具结果、请求与响应保留在对应 Agent 的 session 下。compact 可在新回合开始前或长回合的完整工具 batch 结束后触发，只缩短下次推理使用的 context，不删除原文、不新建节点、不重置预算。Web 只显示允许公开的研究记录，不提供原始会话、密钥或私有验证文件接口。导出下载的是候选文件及 run/验证说明；未产生候选时不会伪造可下载版本。

协议细节见 [Responses 接口](interfaces/responses.md)、[工作台接口](interfaces/workbench.md) 和 [完整实现设计](rsi-research-implementation.md)。
