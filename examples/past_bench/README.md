# PAST-Bench Harness 优化

通过官方 Hermes + PAST-Bench runner 执行完整任务族，接入当前 AReaL-RSI 研究工作台。
默认示例使用 `edit_scope: "hermes"`：候选包含 `prompt.md` 和完整的 `hermes/` 源码，
允许修改执行循环、工具、记忆实现、上下文管理、内部提示词和 skills。
`prompt.md` 仍作为官方 `model.system_prompt_prefix` 注入每个 episode。
模型、任务、评分器、官方序列及状态快照/恢复规则固定。TUI 通过新版研究入口启动。
兼容旧的 `edit_scope: "prompt"`（未填写时的默认值），仅允许修改 `prompt.md`。

本目录的 `run.py` 包含所有 PAST-Bench 专属执行、评分、协议检查和研究配置生成逻辑。
`src/areal_rsi/` 只提供通用的研究配置、作业调度、候选管理和可信结果校验；不导入本目录。
配置生成器输出固定的脚本绝对路径，因此可以从任意作业工作目录运行。

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
uv run --locked python examples/past_bench/run.py prepare \
  --config /absolute/path/operator.json --output /absolute/path/operator.lock.json
uv run --locked python examples/past_bench/run.py describe \
  --lock /absolute/path/operator.lock.json --phase diagnostic
```

prepare/describe 不调用模型。它们核对上游提交及 tracked 文件、Python 依赖、沙箱镜像、
适配器及进程桥接代码、编辑范围、family 划分和官方生成的序列定义。lock 不包含密钥。输出路径必须是新文件。
保存 Python 环境和镜像以复现同一 lock；上游依赖安装本身未提供精确版本锁。

生成可编辑的完整 Harness（只复制固定上游的 tracked 文件，不复制本地缓存或凭证）：

```bash
uv run --locked python examples/past_bench/run.py seed \
  --lock /absolute/path/operator.lock.json --output /absolute/path/hermes-seed
```

两个对比实验应从同一 seed 和协议开始。旧 prompt-only 成绩不能直接当作新协议的 baseline。

## 保存和选择版本

保留原始 upstream 和 seed；在独立工作目录中修改，每个版本保存到不同目录，不原地覆盖。
版本目录包含 `version.json` 以及完整的 `harness/prompt.md`、`harness/hermes/` 文件。
两个版本的同名模块是不同的文件，运行时只加载选中的完整版本，避免混用模块造成结果难以复现。

```bash
uv run --locked python examples/past_bench/versions.py save \
  --source /absolute/path/hermes-seed --output /absolute/path/versions/v001
# 修改独立工作目录后保存 v002；save 拒绝覆盖已有版本目录。
uv run --locked python examples/past_bench/versions.py save \
  --source /absolute/path/edited-workspace --output /absolute/path/versions/v002
uv run --env-file .env --locked python examples/past_bench/versions.py configure \
  --version /absolute/path/versions/v002/version.json \
  --lock /absolute/path/operator.lock.json --output /absolute/path/research-v002.json
```

选择哪个 `version.json`，就生成加载哪个版本的配置；TUI 用 `RSI_RESEARCH_CONFIG` 选择配置。
选择前会校验全部源码摘要，已保存版本被原地修改时拒绝加载。直接评测可先执行
`versions.py verify --version .../version.json`，再将返回的 Harness 路径传给 `run.py evaluate`。
研究工作台内部也会为每个候选保存独立的内容寻址快照，评测时展开副本，不修改 seed 或历史候选。
版本文件不是文件系统权限锁；操作者应始终在工作副本中改代码，再保存为新版本。

## 30 轮框架对比

默认配置的 4 次验证预算不适用于 30 轮实验。用独立配置保留旧实验，并在开始前固定
两组共同的[实验协议](comparison.md)：

```bash
uv run --env-file .env --locked python examples/past_bench/comparison.py \
  --lock /absolute/path/operator.lock.json \
  --version /absolute/path/versions/v001/version.json \
  --output /absolute/path/research-30rounds.json
```

这只生成配置，不启动优化。设置 `RSI_RESEARCH_CONFIG` 指向新文件，再启动 TUI。
生成器固定研究模型 gpt-6-astra / xhigh，禁止运行时模型环境变量覆盖；开始前也校验 seed
版本摘要。评测协议、Task Agent、judge、初始源码保持不变。

每组预算为 seed search 一次、30 个不同候选的有效 search 评测、最终候选 final 一次，
另留 6 次失败尝试；不提供额外 develop 命令。框架硬限制总验证次数与资源上限，
“30 个不同有效候选”和禁止提前完成仍由实验指令约束，必须依据逐轮账本审计，不能只看
作业数或研究状态。此协议匹配评测次数，不声称两个平台的 token 计量或费用完全相同。

## 跑一组真实任务

```bash
uv run --env-file .env --locked python examples/past_bench/run.py evaluate \
  --lock /absolute/path/operator.lock.json \
  --harness /absolute/path/hermes-seed \
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
uv run --env-file .env --locked python examples/past_bench/run.py configure \
  --lock /absolute/path/operator.lock.json --harness /absolute/path/hermes-seed \
  --output /absolute/path/research.json
uv run --env-file .env --locked rsi workbench --config /absolute/path/research.json
```

configure 从 `RSI_TEACHER_MODEL`、`RSI_TEACHER_BASE_URL` 读取研究模型设置，凭证引用
`RSI_TEACHER_API_KEY`，不会将密钥写入配置。运行时 `RSI_MODEL`、`RSI_BASE_URL` 仍可覆盖
研究模型设置；Task Agent 和 judge 由 operator 锁定，不受研究模型选择影响。
本机使用 `.env` 中的 `https://4ipic.com/v1`，不需要官网密钥；未提供研究模型地址时使用
operator 的 `base_url`，不回退官网。Task Agent/judge 的地址在 prepare 前填写到 operator，
之后不会被 `.env` 静默覆盖。Codex 应用自己的模型提供商配置独立于本仓库 `.env`。
configure 不启动研究或调用模型。完整模式允许编辑 `prompt.md` 和 `hermes/**`，提供 `develop`
诊断命令和可信 `verify` 命令，并固定 `diagnostic → development`、`search → search`、
`baseline → full`、`final → full`。development/search 保留配置的研究子集。

示例的研究子集只覆盖三个 memory family，但初始 baseline 和最终验证会自动发现锁定的
上游 `self-evolve-tasks-v2` 下所有 family，运行全部回合及持久化开关对照。
启动正式研究会产生全量评测成本；检查接入时可单独运行 development smoke。
旧 lock 不会自动迁移，更新后需要重新 prepare/configure。

若已有同一协议、模型和初始 Harness 的全量 `evaluation.json`，configure 时增加
`--baseline-source /absolute/path/evaluation.json`（也支持 HTTP(S) URL）。框架校验并归档
结果后直接开始研究，不重复跑 baseline。也可在配置的 `metadata.full_baseline_source`
内直接填写结果对象。缺少任务或身份不匹配会暂停，不会自动回退重跑。
详见[全量评测规则](../../docs/full-benchmark-evaluation.md)。

## 在 TUI 中一句话启动

完成上述配置后，在 `.env` 中设置 `RSI_RESEARCH_CONFIG=/absolute/path/research.json`。
通用 TUI 默认读取 `.rsi/research.json`，也可以将生成的配置保存到此位置。
原 `RSI_PAST_BENCH_CONFIG` 不再使用；旧配置须通过新入口重新 prepare/configure。
确保 Docker 已启动、研究模型和 operator 引用的凭证已加载，然后运行：

```bash
./tui
```

在 TUI 中输入：**开始优化 Task Agent 在 PAST-Bench 上的表现。**

Interaction 会调用 `rsi_research_start`，按已准备的配置直接启动，不再要求第二次确认。
仅询问方法或要求计划不会启动。运行前检查协议、依赖、镜像、任务划分和凭证是否存在；
这不等于提前验证网络或模型接口可用。更改适配器后必须重新 prepare/configure。

默认优化方法为：测量 seed baseline；围绕记忆筛选、检索、规则作用域、更新或迁移提出假设；
修改提示词或 Hermes 实现；development 诊断、search 比较；最后冻结候选并全量复测。
baseline 已向研究者公开，因此最终对比衡量的是已见 Benchmark 上的提升，不代表未见任务泛化。
同时观察持久化开启和关闭的绝对得分，不通过降低关闭组成绩制造提升。
默认预算为 1 个 Research Agent、最多 8 个节点、100 次研究模型调用、4 次验证调用、
1 个并行执行作业、4 小时。4 次验证需包含 baseline 和 final，不代表完整迭代 4 轮；
development 诊断另外占用作业和时间预算。模型调用有实际费用。

后台服务默认在 `127.0.0.1:8198`，可通过 `RSI_RESEARCH_PORT` 更换端口；研究数据位于
`RSI_DATA_ROOT/research/`（未设置时为 `.rsi/research/`）。历史目录保留，不自动迁移已有研究。
该入口自动启动独立工作台，退出 TUI 不会终止研究。返回链接可查看真实进度。
可继续说“查看这次研究进度”“暂停这次研究”“继续这次研究”或“停止这次研究”。
暂停不强杀已运行的评测，停止会取消作业。工具重试复用同一个 request_id，避免重复研究。
更新代码或凭证后应重启专用工作台服务；已有研究保留自己的配置和候选快照。

TUI 用 `rsi_research_status` / `rsi_research_control` 管理新版研究，不混用旧版
`rsi_status` / `rsi_resume`。只有实际完成全量评测后才能报告全量成绩。

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
- `feedback.json`：所有阶段的聚合结果、逐回合成绩、trace_id 与任务组产物目录。
- `artifacts.json`：完整文件索引，包含原始路径和大小；失败评测也会生成。
- `private/<family>/sequence_comparison.json`：官方逐任务对照结果。
- `private/<family>/v2_*_report.json`：官方多层指标，保留 `basis` 等实测/代理/默认值标记。
- `private/<family>.log`：官方运行日志。

不采信 stdout 分数或候选自己提交的结果。缺失/重复/错序任务、错误历史分支、无完整轨迹、
运行时错误、基础设施失败和非有限分数都不产生有效成绩。失败产物保留，重试必须用新目录。

Research Agent 可通过 read_job_output_file 读取整个评测输出目录，包括历史命名为
`private/` 的目录；所有文件保持原样，不脱敏、不裁剪或重写。先读索引，再按任务和 trace_id
分段读取轨迹、评分报告、配置和运行日志。修改前应引用实际轨迹说明失败原因与改动假设。
这些内容会进入研究模型上下文，因此该配置将完整产物授权给所配置的研究模型。
`local` Hermes 和评分器仍同属主机信任域；
Docker 工具沙箱不等于面向恶意 Agent 代码的端到端隔离。

## 完整源码模式的运行边界

`harness_runtime.py` 在官方 runner 与 Hermes 之间建立进程桥接。每个 episode 从候选源码
生成临时副本，在新的 Python 进程里运行，再将响应交给官方 runner 评分。这样修改工具和
执行循环会真正生效，也不会通过 Python 模块缓存或修改源码目录偷偷跨 episode 保留状态。
正常学习状态必须写入官方提供的 `HERMES_HOME`，由 runner 按原协议保存、恢复或清空。
关闭持久化的对照组使用同一份候选代码。加载记录绑定 session 和源码摘要，缺失或不匹配
不产生有效成绩。所有候选文件都纳入 Harness 身份，包括非 Python 文件。

允许修改整个 `hermes/`，但须保留官方适配器使用的 `AIAgent`、`SessionDB`、工具注册接口
及 `HERMES_HOME` 状态契约。不能修改 benchmark runner、grader、任务、划分、桥接器、
模型或评分规则，也不能读取隐藏题目/答案、操纵对照组。Python 依赖环境固定；修改依赖
声明不会自动安装新包，需要新增依赖时应由操作者更新环境并为双方实验建立新协议。

候选代码在主机同一用户下执行；独立进程和过滤常见凭据环境变量不阻止任意代码访问
主机文件。该模式适用于受实验规则约束的 Harness 优化，不提供恶意代码的安全隔离保证。
官方控制的是任务序列和反思任务；Hermes 如何执行、调用工具及组织记忆均可优化。

## 开发检查

```bash
uv run --locked pytest tests/test_past_bench.py tests/test_past_bench_harness.py tests/test_research_verification.py
make check
```

离线测试覆盖成对分数、反思与状态分支、残缺/篡改结果、family 划分、候选限制和可信成绩绑定，
并通过实际子进程验证工具代码修改生效、记忆持久化、源码副本隔离和超时回收。
