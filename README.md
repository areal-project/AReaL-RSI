# AReaL RSI

在冻结的数据、模型和 Harness 上执行候选研究、GRPO 训练、配对评测与版本发布。首版部署使用 Slurm，训练和推理由 AReaL v2 控制器创建 worker；示例使用 Megatron 训练并通过 XCCL/NCCL 同步推理权重。

代码安装为一个 `areal_rsi` 包：

| 目录 | 职责 |
| --- | --- |
| `api/` | 现有领域契约与 JSON 校验；不依赖实现 |
| `infra/coordination/` | 可恢复的代际流程与发布记录 |
| `infra/coordination/policies/` | 公开反馈提案、选择、噪声和晋升策略 |
| `infra/coordination/evaluation/` | 通用冻结题集与 Harness、可信轨迹和训练收据 |
| `infra/coordination/tools/` | 计算器和候选源码工具 |
| `infra/runtime/` | Claude 执行及有冻结校验的 AReaL workflow |
| `scripts/` | 任务无关的 AReaL v2 trainer、推理批次、Slurm 提交与作业收据 |
| `examples/` | 任务 profile、奖励、提示词及配置装配，位于 `src/areal_rsi/` 内 |

[数学 TIR 示例](src/areal_rsi/examples/tir/README.md)的 `cycle.py` 绑定数学 profile，并提供 Arena 入口；数学合成、评分、Claude 求解和连续实验也在该 example 中。普通分类示例使用同一个 `GenerationCycle`，包含相同的候选选择、混合奖励门槛、训练收据、四组 dev/sealed 评测和发布/回滚流程。

接入新的 prompt/answer 数据集时，将数据规范化为 `messages` 与 `answer` JSONL，复用冻结工具；在 example 中配置 `PromptProfile` 的工作流、奖励/独立验证函数、答案格式和研究员指令。更换模型只改运行配置的模型路径和资源参数。原始数据格式的转换可留在 example，通用冻结、调度、训练与评测无需复制。

也可以先用共享单轮 RLVR 路径训练本地数据，JSONL 格式为 `{"task_id":"one","prompt":"问题","answer":"答案"}`：

```bash
export AREAL_ADMIN_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
python -m areal_rsi.examples.arithmetic --data /shared/arithmetic.jsonl \
  --config src/areal_rsi/examples/grpo.yaml actor.path=/shared/models/math-model
python -m areal_rsi.examples.classification --data /shared/labels.jsonl \
  --config src/areal_rsi/examples/grpo.yaml actor.path=/shared/models/chat-model
```

这两个 example 共用数据校验和 trainer，只选择自己的奖励函数。部署到容器时使用 `scripts/submit_train.sh`，设置共享源码、镜像、配置、挂载和 `RECIPE`，并把数据参数传给脚本；分区、reservation 和 GPU 布局由站点环境与 AReaL 配置决定。配置须显式设置 `actor._version: v2`、`rollout._version: v2` 和 `scheduler.type: slurm`。

v2 接受带 `run()` 方法的 Agent。共享 `PromptRewardAgent` 通过会话代理生成并评分，轨迹、logprobs 与训练由 AReaL 记录和执行；数学与分类示例只选择奖励函数。冻结 prompt 的首次输入保持原模板，计算器多轮任务使用 assistant EOS 边界和 concat 导出完整轨迹，工具反馈不计入生成 token 的损失。

训练后的 HuggingFace 检查点支持单文件和带索引的 Megatron 分片导出；公共校验会检查索引引用的所有分片，缺失或空分片会阻止评测和续训。检查点保存用于评测、下一轮加载；训练期间权重更新使用 XCCL/NCCL。

只需要训练完成后的正式评测时，向 `areal-rsi-train`、`continuation` 或 `dynamic_synthesis` 入口传入 `--no-training-eval`。该选项关闭训练内的验证集评测及其 GPU worker，保留训练采样数、更新预算、检查点导出和轮次完成后的正式评测。动态实验可通过 `--completed-rounds` 引用 `CompletedDynamicRounds` 收据，在新输出目录继续剩余轮次；原源码锁、32步日志、配置、检查点、评测及轨迹摘要必须通过校验，旧目录保持只读。

若旧控制器在第1轮的32次训练更新及检查点导出完成后退出，新执行可向 `areal_rsi.examples.tir.continuation` 传入 `--reuse-first-training`，引用 `CompletedFirstTraining` 记录。控制器会核对原计划、源码锁、配置、完成日志、提示筛选记录及检查点摘要，然后只启动该轮训练后评测；原目录保持只读，后续轮次写入新输出目录。

若训练后评测也已完成，同时传入 `--reuse-first-evaluation`，引用独立的 `SavedEvaluation` 记录，包含原报表路径、摘要和源码根目录。恢复入口会核验原源码包、评分与解析规则、题序、逐题记录和原始轨迹，并复算每道题，要求分数与原记录一致；失败题保留零分。训练前后报表在新目录中重新绑定当前完整源码摘要，保留原报表出处，随后执行原有严格轮次校验。这个路径不会重新生成答案或训练首轮，也不会改写旧报表。

提交脚本生成并传递 `AREAL_ADMIN_API_KEY`，示例配置用它设置 v2 的 `actor.admin_api_key`、`ref.admin_api_key` 和 `rollout.admin_api_key`。自定义配置也需设置这些字段；`rollout.agent.admin_api_key` 不适用于 v2。训练入口将控制器容器自动导出的挂载替换为 `WORKER_MOUNTS`，worker 的其他挂载使用 AReaL scheduler 配置，避免将控制器内的路径带到 GPU 宿主机。

提交端使用 Python 3.11+ 将参数写入规范 JSON 并拒绝覆盖已有参数记录。宿主默认 Python 较旧时，设置 `SUBMIT_PYTHON=/shared/venv/bin/python`；镜像内解释器由 `TRAIN_PYTHON` 指定，默认为 `python3`。提交端无需启动容器，Slurm CPU 控制器在镜像内校验参数并调用同一个训练入口。

```bash
PATH="$PWD/.venv/bin:$PATH" make check
```

CPU 测试验证流程和故障路径；可选 AReaL 契约测试使用真实 checkout 的配置解析与工作流，训练器/推理引擎使用替身，不代表 GPU 训练或能力提升。历史数据目录、候选摘要、冻结协议及 source-lock 留在原 worktree；迁移源码后，新实验应重新冻结源码身份。
