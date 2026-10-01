# ToRL RSI 本地验收（2026-10-01）

## AReaL 权重训练闭环

本轮沿用第五代已发布的 M1/H1 作为第六代 M1/H0 基线。第六代重新划分 ToRL 任务，演化 256、Harness 选择 128、训练 2048、开发 256、全新密封测试 194 题。新密封集未开启，旧分支记录未改写。任务集 ID 为 `taskset_36dce3d54a3ed2df57bbc4508b156756599c97df92f46d31601460107e23dc68`。

Harness 候选在 128 题选择集上答对 29 题，H0 答对 28 题；1 题增益未超过预登记的重复测量噪声带，保留 H0。H0 演化任务有 53 道混合成败题，满足训练信号门槛。

首次训练在第 126 次更新后因数学等价验证持续超时而失败，无可用检查点。修复限定于训练 reward 回调：重复超时记为 0 分，独立验收验证器仍对无法判定的比较抛错。修复提交 `5c75bbb`。按同一任务集、基模及 Harness 重试后完成 3 个 epoch、192 次 GRPO 更新、2048 道训练题和 24576 条 rollout 记录；检查点摘要为 `sha256:f572a7aae93052037652a676e089f24a71accfe43a8c75cfa396ca36fe4fd77e`。训练 batch 平均 reward 依次为 0.202148、0.233398、0.228150。

| 配对评测 | M1/H0 | M2/H0 | 差值 |
| --- | ---: | ---: | ---: |
| 第六代开发集（256 题） | 61 | 48 | −13 |
| 固定第五代监控集（194 题） | 56 | 32 | −24 |

M1 监控集复测与此前基线同为 56/194。按预登记规则，M2 未超过固定监控基线，因此停止 AReaL 权重 RSI，不发布 M2，保留第五代 M1/H1。四组评测的 token、mask、回复及 reward 已核验；训练收据的意图、源码、检查点和轨迹也已独立核验。

证据：`data/torl-loop-workflow-2/plan.json`、`harness-selection.json`、`training-signal.json`、`train-retry-receipt.json`、`receipt-retry-audit.json`、`evaluation-audit.json`、`model-decision.json`。训练修复后主分支 `make check` 为 24 passed、4 skipped；跳过项需要可选环境。

## Claude Code 解题 Harness

另建 `feat/claude-code-rsi` 工作树，由 Claude Code CLI 2.1.167 逐题解答，后端是本地 SGLang 承载的 Qwen3-32B 权重，模型摘要 `sha256:02a272e7e515ab5412129900b9792a6974053c235463f4bc35808e99a608b858`。CLI 使用本地 Anthropic 协议适配器和受限 MCP 计算器，无需 Claude 账号登录；完整 CLI 事件和实际工具调用均写入逐题轨迹。这里的 Claude Code 指 Agent 执行框架，模型权重不是 Anthropic Claude。

第一代 Claude 专用 ToRL 任务集 `taskset_aa586fd0744b37573d5ece0900ef6e37f8252128c1be53e269100301f5693e24` 排除了第六代 AReaL 全部任务，包含公开演化 128、选择 128、未用于本项训练的 `model_train` 128、开发 128、全新密封 128 题。提示词 H0 先由公开演化试跑确认能正常使用 CLI 和本地推理，再冻结在选择集运行前。单题 CLI 90 秒超时按 0 分记录并继续；其他 CLI 错误中止评测。

| 第一代 Claude 选择 | 正确数 / 128 | 超时题数 |
| --- | ---: | ---: |
| H0 选择 | 40 | 10 |
| H0 同题重复 | 45 | 12 |
| Claude Code 根据公开失败反馈提出的 H1 | 51 | 12 |

预登记规则要求增益超过 A/A 绝对分差与 2 个百分点中的较大者。此处 A/A 分差为 5 题，H1 比 H0 多答对 11 题，因此接受 H1。四份第一代报告（含 H0 公开演化 44/128）的题目顺序、轨迹摘要、CLI 元数据和严格数学分数均经独立逐题复算。证据位于 `data/torl-claude-code/`：`plan-fast.json`、`selection-fast.json`、`audit-fast-h0-evolve-h0-select-h0-select-repeat.json`、`audit-fast-h1-select.json`。

在事先留出的 128 题开发集上，H0 答对 39 题、H1 答对 50 题，同样增加 11 题；超时数分别为 11 和 7。两份开发集报告的题目顺序、轨迹、元数据和分数也已逐题复核，证据为 `plan-fast-dev.json` 与 `audit-fast-h0-dev-h1-dev.json`。开发集仅作泛化描述，没有参与候选选择或提示词修改；此处没有做开发集 A/A 重复，因此不将单次差值当作新的发布门槛。

第二代重新冻结了与第一代全部 640 道题不重叠的任务集 `taskset_c2450a2ef81103d1cf1481c0a0c89b94a2cc34cc0b2e0a0bc4ee0c3c5ffeeee4`，以第一代选中的 H1 作为新 H0。公开演化为 51/128；选择与同题重复分别为 41/128、52/128，A/A 噪声 11 题。Claude Code 再提出的 H2 为 49/128，较选择基线增加 8 题，未超过噪声，因此拒绝 H2，停止 Claude Harness RSI，保留 H1。第二代全新密封集保持未开启。证据为 `plan-next-taskset.json`、`plan-next.json`、`selection-next.json`、`audit-next-h0-evolve-h0-select-h0-select-repeat.json`、`audit-next-h1-select.json`。

本地 Claude Harness 搜索评估的是 Agent 提示词/工具策略的解题收益；第一代 11 题增益不能解释为模型权重训练收益。

## Claude Code 接入 AReaL rollout 的本地烟测

新增 `FrozenClaudeWorkflow` 作为 AReaL inline Agent workflow。它核对冻结任务、Harness、CLI、计算器和本地模型文件，通过 AReaL 分配的会话 `base_url` 与 `api_key` 运行 Claude Code CLI，并用严格数学验证器返回奖励。Claude Code 不需要账号登录。训练入口在 `examples/areal_rsi_tir/claude_train.py`。

在 Slurm 分配 `972320` 的 `slurmd-3` 上，使用 8 张 L20X、`/storage/openpsi/models/Qwen__Qwen3-4B`、本地 SGLang、8 道冻结训练题和 8 道开发题，运行了一个 epoch 的 GRPO 烟测。配置与启动脚本保存在 `data/claude-proxy-smoke/config.yaml`、`launch.sh`；独立生成的代理管理密钥仅在本地配置中。首次试跑因 AReaL 拒绝默认管理密钥而在代理初始化时失败，未进行更新；修正后试验 `qwen3-4b-one-step-20261001-b` 正常退出。日志确认 Claude Code 的 Anthropic 请求到达 AReaL 代理，再由本地 SGLang 服务生成；AReaL 指标 `ppo_actor/update/update_successful=1`，检查点 `model.safetensors` 的 SHA-256 为 `195d7dc4d571bcdc27cbefba7aed404c6a266d84746d099cde6e7016721c7c36`。

逐条核对的训练轨迹有 19 条、4 分，涉及前 8 道训练题中的 7 道；AReaL 采样重复了部分题，漏了其中 1 道。开发轨迹有 19 条、8 分，覆盖前 8 道开发题。每条轨迹含冻结任务 ID、Claude Code 系统提示、生成内容与 0/1 奖励；记录集摘要见 `data/claude-proxy-smoke/audit-b.json`。这是连接和单次更新验收，不据此判断 4B 模型收益，也不改变上面的 32B Harness 选择和第六代权重发布决定。

本次跨模块修改后 `make check` 为 31 passed、4 skipped；跳过项需要可选环境。

## Claude Code Harness 下的权重 RSI（2026-10-02）

在上面的接入烟测之后，使用第二代 Claude 专用任务集 `taskset_c2450a2ef81103d1cf1481c0a0c89b94a2cc34cc0b2e0a0bc4ee0c3c5ffeeee4` 的全部 128 道 `model_train` 和 128 道 `model_dev` 题，固定已选中的 H1、Claude Code CLI 和本地 Qwen3-4B 基模。训练前后均由同一 AReaL 代理与本地 SGLang 服务评测；训练为 1 epoch、16 个 batch step，每道训练题至少产生一条轨迹。全新密封测试集未开启。

完整试验 `qwen3-4b-h1-epoch1-20261002-f` 在 Slurm 分配 `972320` 内正常退出。早期试验依次发现默认 CLI 输出上限、输出用满后的错误处理、训练奖励对 CLI 费用元数据的非必要要求，以及 actor 微批容量 8192 小于多回合轨迹长度的问题；每次失败均使用新试验 ID 重跑，记录在 `data/claude-weight-g1/attempt-*.json`。最终冻结 CLI 输出上限为 4096，训练中的输出超限记 0 分，严格独立报告仍要求完整元数据，actor 微批容量调至 16384。最终源码提交 `e1498a6`，`make check` 为 31 passed、4 skipped。

| 同一 128 题开发集 | 每题平均 reward | 轨迹数 |
| --- | ---: | ---: |
| 未训练基模 A/A 复测（失败试验在更新前完成的独立基线） | 0.3260 | 395 |
| 最终试验训练前基线 | 0.2873 | 350 |
| 最终试验训练后 | 0.0000 | 365 |

预登记规则要求训练后增益超过 2 个百分点与 A/A 绝对差值中的较大者，且按题配对 bootstrap 的 95% 下界大于 0。A/A 差值为 0.0386，实际训练后减去训练前为 −0.2873，bootstrap 95% 区间为 [−0.3532, −0.2226]。全部训练轨迹 290 条、总奖励 47 分，覆盖 128/128 道训练题；训练后 365 条开发轨迹全为 0 分。训练前轨迹有 229 条包含 `\boxed`，训练后为 0 条；抽样生成可见重复作答片段。这是观测到的退化，不能单凭这些片段确定具体成因。

因此拒绝此次训练检查点，保留未训练的 Qwen3-4B 与 Claude Code H1，停止该分支的权重 RSI。检查点 `model.safetensors` 的 SHA-256 为 `5ce625cfadce59cf91c72cf0fa09bd5ccdfa334cb412ba77ae0f61fceaf28228`，只作审计留存。逐题任务 ID、0/1 奖励、基线与训练后轨迹摘要、配对统计、配置摘要及源码提交见 `data/claude-weight-g1/decision-f.json`；冻结决策规则见同目录 `plan.json`。这个结果只适用于此 4B 模型、任务集和训练配置，不能外推为 Claude Code Harness 在所有模型上训练均无效。
