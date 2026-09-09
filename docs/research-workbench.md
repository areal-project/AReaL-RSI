# Research 工作台使用说明

通过对话确定研究任务，由 RSI 自主规划，再交给保持连续会话的 Research Agents 推进。任务不需要属于某个示例，也不强制要求 GPU、起点版本、候选产物或验证分数。原有 `rsi tui / run / resume / web` 入口继续保留。

## 启动与对话

在仓库根目录安装依赖；没有 `.env` 时，从 `.env.example` 复制一份并填写本机配置，已有文件不要覆盖。

```bash
uv sync --locked
```

```dotenv
RSI_BASE_URL=https://your-responses-gateway.example/v1
RSI_API_KEY=<在本机填写>
RSI_MODEL=gpt-6-astra
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/local
```

密钥只放在进程环境或本机 `.env` 中，不发送到聊天，也不写进研究配置。模型服务需要支持 Responses API、工具调用和连续会话。可选预检：

```bash
uv run --env-file .env --locked python scripts/preflight_responses.py
```

正式工作台默认直接使用真实模型与执行配置：

```bash
uv run --env-file .env --locked rsi workbench
```

打开 [本地工作台](http://127.0.0.1:8766)，直接描述研究目标；不用选模板或填写参数表。Interaction 沿用已有信息，逐步询问会影响任务的条件，整理成：

- `TaskBrief` 中的结构化约束：资源、并发与预算、可修改范围、可选验证指标和目标。
- 给 RSI 的自然语言说明：研究目的、期望成果、建议方向、取舍、环境用法和其他要求。
- run 内的执行配置：可选起点、普通执行命令、可选可信验证器和非敏感环境变量。

准备阶段以对话和需求摘要为主。Interaction 检查所需条件后给出总结，等待你说“开始”；也可以点击「开始研究」。开始前可继续修改要求。开始后，资源上限、修改范围和验证条件固定，新的建议通过 Interaction 转交 RSI。

研究视图展示 RSI 当前计划、研究方向、节点任务与具体报告、共享证据、作业及调用预算。方向标题由 RSI 创建研究员时填写，随任务而变。有验证协议的研究显示可比较的最佳成绩；没有分数的任务不会补零或伪造最佳版本。产物按 run 归档，不要求所有成果都能打包下载。

## 环境如何接入

先描述实际环境，提供已有目录、环境说明或执行配置的路径。Interaction 可以查看这些资料，再用工具完成配置；参考文件中的占位路径不会成为可执行环境。

| 接入方式 | 使用场景 |
| --- | --- |
| 仅讨论目标和资料 | 不需要运行代码的研究；执行配置可以为空 |
| 普通执行命令 | 使用本机、容器或远程执行入口开展实验；由用户提供入口和用法，Interaction 保存固定 argv 模板 |
| 导入 `ResearchConfig` | 已有执行配置或自定义可信验证器；保留当前用户需求、研究模型与预算 |
| 内置适配器 | KernelBench 和 CUHK-X 的便利接入工具；不会限制外层研究流程 |

通用编排不等于自动适配所有训练框架。新任务若需要正式分数，应接入该任务的可信验证程序；可以使用通用 `content` 结果协议，参见 [`coordination/verification.py`](../src/areal_rsi/coordination/verification.py)。验证程序固定比较条件、候选身份与数据身份；普通脚本输出和 Agent 报告中的数字不会自动进入最佳成绩。

命令使用 argv 数组，`{workspace}`、`{output}`、`{phase}` 等占位符必须独占一个数组元素。系统绑定实际路径并保存执行输入。普通本机命令不是安全沙箱；执行不可信候选时，接入者应提供对应容器或隔离入口，并将私有评测材料放在候选无法修改的位置。

### KernelBench

可以从这样一段话开始：

> 我想改善固定模型在 KernelBench 上的成绩，只优化通用 skills，先用一个 Research Agent。GPU 在我的 SSH 主机上，现有 harness 和题池位置我接下来提供。先帮我把任务和环境整理好。

继续说明固定目标模型、harness 起点与修改范围、GPU 环境、题池和比较方式。Interaction 使用内置适配器完成预检和配置交接。可以提供已有 `prepared_data`，也可以提供本机 KernelBench 仓库与题池选择 JSON，由系统在 run 内准备数据。选择文件采用 [现有格式](../examples/kernelbench/config.json)，实际题目划分由用户决定。

目标模型使用 `RSI_TARGET_API_KEY`；模型名称和非敏感接口地址可通过对话保存为本 run 的 `execution_env`，也可沿用 `RSI_TARGET_MODEL / RSI_TARGET_BASE_URL`。研究模型由工作台配置，RSI 不自行更换它。

预检固定目标模型、GPU 和独立 development/search/final 题池，不调用目标模型或测量候选成绩。研究开始后再按约定评测起点、分析开发轨迹、修改 skills、比较候选和做最终确认。实际环境要求见 [KernelBench 说明](../examples/kernelbench/README.md)；当前 Task executor 支持 Mac 主控配 SSH Linux GPU，Linux 主控还需要匹配的隔离实现。

### 其他任务与现成配置

CUHK-X 可通过相同对话入口接入其训练与验证命令，环境要求见 [CUHK-X 说明](../examples/cuhkx/README.md)。其他任务通过普通命令或自定义验证配置接入，不需要新增研究任务类型。

操作员仍可以独立准备配置，例如：

```bash
uv run --env-file .env --locked python scripts/configure_research.py \
  --example kernelbench --operator /operator/kernelbench.json \
  --output /operator/kernelbench-research.json --target-model 'your-fixed-task-model'

# 在对话中提供生成文件的绝对路径，或用它作为新研究的起步配置：
uv run --env-file .env --locked rsi workbench --config /operator/kernelbench-research.json
```

## 等待、暂停和恢复

Research Agent 提交报告后立即停止推理，RSI 回复后在同一会话中继续。等待后台作业时由完成事件唤醒。RSI 可以在其他研究员工作时继续有价值的调研；没有可推进事项时停止等待。刷新工作台只读取本地状态，不调用模型。

「暂停」保留研究状态；「继续」恢复推进；「停止」取消研究及相关工作，不能自动改称成功。`Ctrl+C` 或关闭工作台服务只关闭当前 worker，保留恢复所需记录；重启后通过「恢复执行」或「继续对话」接续。同一 run 同时只允许一个 worker，其他进程可以查看。

Agent 失败可通过「重试」接续未完成回合，已经完成的工具操作不会重做，全局预算不重置。达到硬预算后不会自动增加额度。

需要纯终端执行时：

```bash
uv run --env-file .env --locked rsi research --config /operator/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

## Prompt 与归档

Interaction、RSI、Research 的角色指令、工具说明、compact 与恢复指令默认使用中文。原始记录保留原语言；任务代码、标识符和必要原文保持原样。被优化的 Task Agent harness 是独立产物，其初始 prompt 不随研究角色的语言设置改变。

每次 run 的记录集中在 `RSI_DATA_ROOT/runs/<run_id>/`：

```text
run.json
briefs/ assignments/ reports/ evidence/
candidates/ verifications/ iterations/
agents/<agent_id>/
  agent.json  workspace/  sessions/
coordination/
  config.json                  # 准备阶段当前配置
  configurations/              # 已保存的执行配置修订
  started-config.json          # 启动时固定的完整配置
  state.json                   # 包含 intake 摘要、状态与确认消息
setup/                         # 内置适配器的环境配置、预检和可选题池
messages/ events/ jobs/
execution-inputs/ execution-outputs/
```

不可变文件内容仍在 `RSI_DATA_ROOT/content/` 去重，由 run 内记录引用；可变 memory/evidence 不跨 run 自动共享。会话压缩保留完整原文，只缩短下次使用的上下文。`ResearchIteration` 是通用节点，完成任务后有具体 report，不要求候选或验证；`EvolutionResult` 是旧固定流程的可选记录。

正式工作台默认不列出历史模拟 run，也不接受以创建表单选择模拟模式。原始测试数据保留其 `simulated` 标记，不会改写成实测结果。开发者仍可在独立数据根目录用 `rsi research --example ... --simulate-gpu` 演练；该模式会调用真实研究模型，但不证明 GPU 或比赛成绩。

接口见 [工作台 API](interfaces/workbench.md)，设计见 [实现参考](rsi-research-implementation.md)，实际测试及限制见 [验收记录](research-acceptance.md)。
