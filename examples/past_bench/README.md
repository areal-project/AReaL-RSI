# PAST-Bench 跨会话策略优化

通过官方 Hermes + PAST-Bench runner 执行完整任务族，接入当前 AReaL-RSI 研究工作台。
候选只包含 `prompt.md`，作为官方 `model.system_prompt_prefix` 注入每个 episode；
优化的是通用记忆、检索、复用和更新策略。模型、原生工具、持久化实现、反思流程和 grader 固定。
这不是任意 Agent 代码优化接口。TUI 通过新版研究入口启动，不依赖旧版 Benchmark catalog。

固定上游提交：`f8223517ae7491e776b69793d9f11e9d074ab42e`。
该版本的 `evolve` 明确拒绝 Hermes/nanobot/zeroclaw 持久化序列使用 `--runtime container`。
本适配采用 **Hermes 本地 runtime + Docker 工具沙箱**，notes 等模拟服务由官方 runner
在主机启动。不要把 `--sandbox` 理解成全部组件都在容器中。

## 安装和预检

在 AReaL-RSI 仓库根目录执行：

```bash
git clone https://github.com/Gen-Verse/PAST-Bench.git .rsi/past-bench/upstream
git -C .rsi/past-bench/upstream checkout f8223517ae7491e776b69793d9f11e9d074ab42e
uv venv --python 3.11 .rsi/past-bench/upstream/.venv
uv pip install --python .rsi/past-bench/upstream/.venv/bin/python \
  -e '.rsi/past-bench/upstream[mock,sandbox]' \
  -e .rsi/past-bench/upstream/agents/hermes-agent
docker build -t past-bench-agent:f8223517 \
  -f .rsi/past-bench/upstream/Dockerfile.agent .rsi/past-bench/upstream
```

若 Docker Hub 或 Debian 软件源不可达，可使用本目录的等价工具镜像构建文件：

```bash
docker build -t past-bench-agent:f8223517 \
  --build-arg REGISTRY=docker.m.daocloud.io \
  --build-arg DEBIAN_MIRROR=https://mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  -f examples/past_bench/Dockerfile.sandbox .rsi/past-bench/upstream
```

保留 LibreOffice 和上游沙箱依赖，不以精简环境冒充完整任务环境。镜像构建后，prepare
会固定实际 image ID；重新构建或依赖升级需要生成新的 lock 和研究配置。

复制 `operator.example.json` 到候选目录外，填写绝对路径、固定目标模型和 judge。
将对应密钥放入配置所引用的环境变量，例如 `RSI_TARGET_API_KEY`、`RSI_JUDGE_API_KEY`。
模型接口需要兼容上游 Hermes 和 judge 使用的 Chat Completions；研究模型仍使用 Responses。
目标模型与 judge 可以不同；使用其他 judge 时不能直接与论文表格比较。

```bash
uv run --locked python -m areal_rsi.adapters.past_bench prepare \
  --config /absolute/path/operator.json --output /absolute/path/operator.lock.json
uv run --locked python -m areal_rsi.adapters.past_bench describe \
  --lock /absolute/path/operator.lock.json --phase diagnostic
```

prepare/describe 不调用模型。它们核对上游提交及 tracked 文件、Python 依赖、沙箱镜像、
适配器代码、family 划分和官方生成的序列定义。lock 不包含密钥。输出路径必须是新文件。
保存 Python 环境和镜像以复现同一 lock；上游依赖安装本身未提供精确版本锁。

## 跑一组真实任务

```bash
uv run --env-file .env --locked python -m areal_rsi.adapters.past_bench evaluate \
  --lock /absolute/path/operator.lock.json \
  --harness examples/past_bench/seed \
  --output /absolute/path/new-smoke-output --phase diagnostic
```

示例 development 池为 `SM01_preference_adoption`，保留 8 个任务的顺序、学习快照、
近/远迁移的独立恢复，以及控制任务的清空状态。官方还会自动插入反思步骤，并在两组间
共享一次冷启动执行。因此不是简单的 16 次独立问答；当前 SM01 每组结果有 9 条记录，
实际执行 17 次（含两次反思）。

每次评测使用独立输出和持久化目录，同一上游 checkout 的适配器评测串行执行，避免固定
mock 服务端口冲突。不要同时在同一主机启动使用相同服务端口的上游 CLI 或另一份 checkout。
超时/中断会通知本次 runner 清理资源；SIGKILL 等强制退出后可能需要操作者按本次日志中的
容器 ID 清理，不要运行影响其他评测的全局 cleanup。

## 接入工作台

```bash
uv run --env-file .env --locked python -m areal_rsi.adapters.past_bench configure \
  --lock /absolute/path/operator.lock.json --harness examples/past_bench/seed \
  --output /absolute/path/research.json
uv run --env-file .env --locked rsi workbench --config /absolute/path/research.json
```

configure 从 `RSI_TEACHER_MODEL`、`RSI_TEACHER_BASE_URL` 读取研究模型设置，凭证引用
`RSI_TEACHER_API_KEY`，不会将密钥写入配置。运行时 `RSI_MODEL`、`RSI_BASE_URL` 仍可覆盖
研究模型设置；Task Agent 和 judge 由 operator 锁定，不受研究模型选择影响。
configure 不启动研究或调用模型。生成的配置限制候选只编辑 `prompt.md`，提供 `develop`
诊断命令和可信 `verify` 命令，并固定 `diagnostic → development`、`search → search`、
`final → final`。每个 family 必须完整分配到且仅分配到一个池。

示例只覆盖三个 memory family，是开发接入示例，不是官方的完整 26-family 成绩。
可在 operator 配置中换成覆盖四类能力的更大划分；正式研究前应确定划分，不要在 final
上反复挑选候选。完整运行的成本随 family 数量增加，首次建议只运行 development smoke。

## 在 TUI 中一句话启动

完成上述配置后，在 `.env` 中设置 `RSI_PAST_BENCH_CONFIG=/absolute/path/research.json`。
本仓库本地默认读取 `.rsi/past-bench/research-current.json`，已有此文件时无需额外设置。
确保 Docker 已启动、研究模型和 operator 引用的凭证已加载，然后运行：

```bash
./tui
```

在 TUI 中输入：**开始优化 Task Agent 在 PAST-Bench 上的表现。**

Interaction 会调用 `rsi_research_start`，按已准备的配置直接启动，不再要求第二次确认。
仅询问方法或要求计划不会启动。运行前检查协议、依赖、镜像、任务划分和凭证是否存在；
这不等于提前验证网络或模型接口可用。更改适配器后必须重新 prepare/configure。

默认优化方法为：测量 seed baseline；围绕记忆筛选、检索、规则作用域、更新或迁移提出假设；
修改通用 `prompt.md`；development 诊断、search 比较；最后用 final 独立确认。
同时观察持久化开启和关闭的绝对得分，不通过降低关闭组成绩制造提升。
默认预算为 1 个 Research Agent、最多 8 个节点、100 次研究模型调用、4 次验证调用、
1 个并行执行作业、4 小时。4 次验证需包含 baseline 和 final，不代表完整迭代 4 轮；
development 诊断另外占用作业和时间预算。模型调用有实际费用。

后台服务默认在 `127.0.0.1:8198`，可通过 `RSI_RESEARCH_PORT` 更换端口；研究数据位于
`RSI_DATA_ROOT/past-bench-research/`（未设置时为 `.rsi/past-bench-research/`）。
该入口自动启动独立工作台，退出 TUI 不会终止研究。返回链接可查看真实进度。
可继续说“查看这次研究进度”“暂停这次研究”“继续这次研究”或“停止这次研究”。
暂停不强杀已运行的评测，停止会取消作业。工具重试复用同一个 request_id，避免重复研究。
更新代码或凭证后应重启专用工作台服务；已有研究保留自己的配置和候选快照。

TUI 用 `rsi_research_status` / `rsi_research_control` 管理新版研究，不混用旧版
`rsi_status` / `rsi_resume`。这是有边界的小规模优化入口，不表示所有 26 个任务族已经实测。

## 成绩和证据

主要指标为每个 family 的 `evaluation_lift`，然后对 family 宏平均：

```text
持久化开启的 evaluation 均分 − 持久化关闭的 evaluation 均分
```

这是官方 runner `delta.evaluation_avg_task_score` 的口径，不是 `FamilyEvolveScore`
的多个因子乘积。6 个 proactive-information-gathering family 没有 cold baseline，不能
强行统一计算相对冷启动的增益。有 baseline 的 family 另报 `baseline_score`、
`outcome_delta` 和 `ablation_lift = (on_eval − on_baseline) − (off_eval − off_baseline)`；
这些辅助指标只对有观测的 family 平均，并报告 `baseline_family_count`，缺失不填零。
`passed` 表示该成对提升大于零，不表示每个 episode 都成功。

- `evaluation.json`：可信成绩；绑定候选文件摘要、protocol、family manifest、phase 和模型。
- `public/feedback.json`：仅 diagnostic 发布的聚合结果。
- `private/<family>/sequence_comparison.json`：官方逐任务对照结果。
- `private/<family>/v2_*_report.json`：官方多层指标，保留 `basis` 等实测/代理/默认值标记。
- `private/<family>.log`：官方运行日志。

不采信 stdout 分数或候选自己提交的结果。缺失/重复/错序任务、错误历史分支、无完整轨迹、
运行时错误、基础设施失败和非有限分数都不产生有效成绩。失败产物保留，重试必须用新目录。

上游 raw runtime trace 可能含模型凭据，所以原始轨迹和配置全部留在 operator-private
目录，研究 Agent 只能看到显式发布的聚合结果。`local` Hermes 和评分器仍同属主机信任域；
Docker 工具沙箱不等于面向恶意 Agent 代码的端到端隔离。当前接口不执行候选 Python 或 shell。

## 开发检查

```bash
uv run --locked pytest tests/test_past_bench.py tests/test_research_verification.py
make check
```

离线测试覆盖成对分数、反思与状态分支、残缺/篡改结果、family 划分、候选限制和可信成绩绑定。
