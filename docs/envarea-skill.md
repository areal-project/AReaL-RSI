# Skill: EnvArena 平台适配指南

你是 EnvArena 的研发 Agent。本文档教你用 `arena` CLI 完成四类适配工作，并创建/发布 Env、Harness、Tool、Skill、Reward、Benchmark、Model 的不可变版本。Web 的 Skill 抽屉直接展示本文件，其中 `https://htnn-gateway.alipay.com/envarena/openapi/v1` 和 `https://htnn-gateway.alipay.com/envarena/api/runtime-config` 会替换为当前环境的公开地址；在 Agent 上下文中，请使用 `../SKILL.md` 的环境表选择地址：

| 工作流 | 目标 | 涉及实体 |
|---|---|---|
| 一、导入 Env | 把开源 benchmark 题目导入平台（含 setup/collect 钩子） | Env |
| 二、适配 Harness | 定义被测 Agent：runtime + system prompt + tools + skills | Harness、Tool、Skill |
| 三、设计 Reward | 读取 agent 产物与 oracle，输出归一化分数 | Reward |
| 四、运行 Job | 组装 Benchmark、配置 Model、创建并监控 Job | Benchmark、Model、BenchmarkJob |

四个工作流共用同一套沙箱契约，先读「任务运行机制」。

## 第一步：安装并配置 arena CLI

~~~bash
# 一键安装（自动探测平台，装到 /usr/local/bin，不可写则回退 ~/.local/bin）
curl -fsSL https://htnn-gateway.alipay.com/envarena/openapi/v1/cli/install.sh | sh

arena token set            # 交互式保存 token（或 echo "$TOKEN" | arena token set）
arena doctor               # 连通性体检：profile / 地址 / token 来源 / 实际请求验证
~~~

- 默认连接 prod；多环境用 profile：`arena token set <token> --name staging --baseurl <OpenAPI地址>`，随后 `arena --profile staging ...`
- 升级：`arena update`（自动校验 sha256 后原子替换自身）；`arena update --check` 只看不更
- token 来源是平台部署密钥，由管理员提供；不要写进任何 Registry spec

### CLI 约定（对 Agent 很重要）

- **所有输出都以「下一步」结尾**：成功给后续动作，失败给原因分析 + 可复制的恢复命令。照着执行即可，不要自行猜测接口。
- `--json` 输出机器可读信封：`{"ok":bool,"title":"...","data":...,"error":{...},"next_steps":[{"description","command"}]}`
- 退出码：`0` 成功 / `1` 业务错误 / `2` 用法错误 / `3` 网络或鉴权失败
- 危险操作（cancel/deprecate/archive/delete/reeval）需交互确认，非交互环境加 `--yes`
- 未封装的接口用逃生舱：`arena api get|post|patch|delete <path> [--data @file] [--query k=v]`；字段不确定时以服务端 schema 为准：`arena api get /openapi.yaml`
- 任意命令 `--help` 都有中文用法与示例

## 总体心智模型

- Env@v = query(Target.md) + 可选镜像/文件 + 生命周期钩子(hooks) + 超时(timeouts)，不包含评测逻辑
- Tool@v = 可复用 MCP JSON 配置；mcp_config_inline 会格式化保存，并由平台转存为 OSS mcp_config.uri
- Skill@v = agentskills.io 规范能力包，至少包含带 frontmatter 的 SKILL.md
- Harness@v = system_prompt + tool_refs + skill_refs + runtime(type/uri)，runtime.uri 指向 agent runtime 可执行文件（例如 claude），不要填平台内置 harness 二进制
- Reward@v = runtime(image|inherit_env|shared_env) + entrypoint + normalization(jq 表达式)，读取 agent 产物(/envarena/out/agent/) + oracle；shared_env 在 agent 沙箱内原地评分（可探测 live 进程）
- Benchmark@v = N Env@v + 1 Reward@v + aggregation，只保存引用；可选 variants 声明对照臂（如带/不带 skill 各跑一遍再比分，见工作流四）
- Model@v = model_name + parameters + env，env 原样注入 Harness/Reward 沙箱（被测模型与网关/凭证全靠 env）
- BenchmarkJob = Benchmark@v + Harness@v + Model@v + sample_numbers，sample_numbers 是 pass@k 的 k
- 统一 Runtime 的 template 与 image 是两个独立字段：template 放 ARCA 模版 id（ARCA-TEMPLATE-*），image 放容器 registry 地址，不要混用
- 平台内置 harness adapter 二进制由部署配置管理，只读挂载到 /envarena/bin；创建 Harness 时不写这个地址

## 任务运行机制（必读）

一个 BenchmarkJob 展开为 N(env_refs) × sample_numbers 个 Task（Benchmark 声明了 variants 时再 × 臂数，每臂一个独立 Task）。每个 Task 依次经历：

1. **prepare** — 平台创建 task 专属可写前缀：files Env 把 bundle 目录拷贝成初始工作区；image Env 用镜像自带文件系统。已发布版本的 query/files/oracle/harness 物料在 publish 时已物化，任务只读挂载，不做 per-task 拷贝。
2. **agent 沙箱** — 平台按 Env runtime 选择执行环境：files Env 用部署配置的内置 harness 镜像；image Env 复用 Env 镜像并注入内置 harness 二进制。沙箱内固定执行 `harness -t "$ARENA_QUERY_PATH"`，它串行跑三个阶段：
   - **setup**（可选）：Env hooks.setup_script，`/bin/sh -c` 执行，cwd=$ARENA_WORKSPACE，携带完整沙箱环境变量，超时 ARENA_SETUP_TIMEOUT_SEC。**files Env 未配置 setup_script 时，自动把 $ARENA_ENV_DIR 下的全部 env files 拷进 $ARENA_WORKSPACE**（简单场景零配置）。
   - **agent**：启动 Harness runtime（claude）完成任务，超时 ARENA_AGENT_TIMEOUT_SEC。
   - **collect**（可选）：hooks.collect_script，**保证必跑**——即使 agent 失败或超时也会执行（用父上下文，不受 agent 超时影响），用于把可评分产物提取到 $ARENA_AGENT_OUTPUT_DIR。
3. **reward 沙箱** — 独立沙箱（image 或 Env 同镜像干净副本），只读读取 workspace/out/oracle/评分代码，运行 Reward entrypoint，把 reward_result.json 写入 $ARENA_REWARD_OUTPUT_FILE。**例外：Reward runtime.kind=shared_env 时不建独立沙箱**——agent 沙箱在 collect 后保活，reward 经 exec 在其内部原地执行，可访问 agent 留下的 live 状态（后台进程、端口、内存数据）；oracle/评分文件在 agent 阶段结束后才注入容器（/tmp/.arena_eval，agent 运行期间不可见），输出写 out/.eval/。
4. **record / aggregate** — 平台按 normalization 计算分数并聚合 JobResult。

补充：

- 阶段超时默认值：setup/collect 300s；agent 缺省回落到 Env 的 timeout_seconds；三者可被 timeouts.{setup,agent,collect}_seconds 覆盖
- 阶段失败对应 Task 终态：SETUP_FAILED / HARNESS_FAILED / COLLECT_FAILED / EVAL_FAILED / TIMEOUT
- URI 规范：spec 中的 URI 统一使用前缀相对路径（如 registry/envs/x/1.0.0/materialized/ 或 uploads/…），全局 bucket 由平台配置注入，不要写 oss://bucket/...

### /envarena 沙箱挂载布局（一域一挂载）

| 路径 | agent sandbox | reward sandbox | 内容 |
|---|---|---|---|
| /envarena/bin | ro | — | 平台内置 harness 二进制 |
| /envarena/harness | ro | — | Harness@v 物化：bin/claude, skills/, mcp.json, settings.json, system-prompt.md |
| /envarena/env | ro | — | Env@v 物化输入：query/Target.md, files/<dest> |
| /envarena/workspace | rw | ro | Agent 工作区 |
| /envarena/out | rw | ro | Agent 产物：agent/, artifacts/, attempts/<n>/ |
| /envarena/oracle | — | ro | per-query 答案（agent 永不可见） |
| /envarena/reward | — | ro | Reward.files 评分代码 |
| /envarena/result | — | rw | Reward 输出：reward_result.json, artifacts/（agent 不挂载，防预植） |

例外——shared_env Reward 没有独立 reward 沙箱，上表 reward 列不适用：oracle/评分代码注入到 /tmp/.arena_eval/{oracle,reward}（挂载之外，绝不回传 OSS），输出写 /envarena/out/.eval/result/（评分前平台自动清场防预植），对应的 ARENA_ORACLE_PATH / ARENA_REWARD_DIR / ARENA_REWARD_OUTPUT_DIR 等变量由平台自动重映射，评分脚本照常读环境变量即可，无需感知差异。

### 运行时注入变量

- ARENA_TRACE_ID / ARENA_TRACEPARENT / ARENA_TASK_ID / ARENA_JOB_ID / ARENA_ATTEMPT
- ARENA_WORKSPACE=/envarena/workspace / ARENA_QUERY_PATH=/envarena/env/query/Target.md（默认）
- ARENA_OUTPUT_DIR=/envarena/out / ARENA_ARTIFACTS_DIR=/envarena/out/artifacts（reward 角色下改指 /envarena/result/artifacts）
- ARENA_HARNESS_DIR=/envarena/harness
- ARENA_SKILLS_DIR=/envarena/harness/skills / ARENA_SYSTEM_PROMPT_PATH=/envarena/harness/system-prompt.md
- ARENA_ENV_DIR=/envarena/env/files（Env files 只读挂载目录）
- ARENA_BIN_DIR=/envarena/bin（平台内置 harness 二进制）
- ARENA_AGENT_OUTPUT_DIR=/envarena/out/agent（agent 产物 = reward 输入）
- ARENA_REWARD_OUTPUT_DIR=/envarena/result（reward 分数输出目录，仅 reward 可写）
- ARENA_REWARD_OUTPUT_FILE=/envarena/result/reward_result.json（默认）
- ARENA_ATTEMPT_DIR=/envarena/out/attempts/<n>（本次 attempt 的轨迹/遥测目录）
- ARENA_TRAJECTORY_PATH=$ARENA_ATTEMPT_DIR/trajectory.jsonl（claude stream-json 完整轨迹）
- ARENA_ORACLE_PATH=/envarena/oracle / ARENA_REWARD_DIR=/envarena/reward（仅 reward 沙箱有内容）
- ARENA_SETUP_SCRIPT / ARENA_COLLECT_SCRIPT（钩子脚本内容）
- ARENA_SETUP_TIMEOUT_SEC / ARENA_AGENT_TIMEOUT_SEC / ARENA_COLLECT_TIMEOUT_SEC
- ARENA_ENV_KIND=files|image（内置 harness 据此决定是否自动铺放 env files）
- TRACEPARENT / CLAUDE_CODE_ENABLE_TELEMETRY / OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_RESOURCE_ATTRIBUTES
- 平台不注入 ARENA_MODEL_NAME / ARENA_GATEWAY_URL / ARENA_API_KEY：被测模型名、网关、凭证全部来自 Model spec 的 env

## 通用命令模式

7 种 kind（env/harness/reward/benchmark/model/tool/skill）共享同一套生命周期命令：

~~~bash
arena <kind> list                                    # 列出实体（env 例外：须带 --tag/--key-prefix/--limit 之一或 --all；先用 arena env tags 看标签）
arena env clone <src-key[@version]> <new-key> [--storage <storage_id>] # 复刻 env：一步复制实体元信息 + 版本 spec
arena <kind> create --key <key> --name <名称> --tags a,b   # 创建实体（或 --file entity.json）
arena <kind> version-create <key> <version> --file spec.json [--storage <storage_id>] # 创建 draft；可 --publish
arena <kind> version-update <key> <version> --file spec.json [--storage <storage_id>] # 更新 draft；省略 --storage 保留原绑定
arena <kind> version-get <key> <version>             # 查看完整 spec
arena <kind> publish <key> <version>                 # 发布（校验 + 物化，之后不可变）
arena <kind> versions <key>                          # 版本列表与状态
arena <kind> deprecate|archive <key> <version> --yes # 弃用 / 归档
~~~

- 版本生命周期：DRAFT → PUBLISHED →（deprecate 或 archive）；**已发布版本不可变**，任何变更都发新 version
- ⚠️ `arena <kind> update` 是**全量覆盖语义**：只传 tags 会把 name/description 清空。安全流程：先 `arena <kind> get <key> --json` 拿全量，改完整体提交 `--file`
- **素材上传**：`arena upload <路径> [--storage <storage_id>]`——文件直接上传；目录自动打 zip 并解包成目录 URI（以 / 结尾）；已有 zip 想解包加 `--unpack`。返回的 URI 填进 spec 引用字段，publish 时物化为不可变副本
- **多 OSS**：OSS 绑定在 Registry version，而非 Job/Task。上传与 `version-create` 建议使用相同的 `--storage`；省略时使用环境变量提供且不可修改的 `default`。Job 运行时会按 Env/Harness/Reward/Skill/Tool 各自版本的 `storage_ref` 自动生成多个 ARCA mount-id；workspace/out/result 等写路径仍固定落 `default`
- 小文件可内联：Env 的 query.uri / oracle.uri 支持 `data:` URI（如 `data:text/markdown;base64,...`），免上传
- Tag 约定：推荐 source 与 difficulty（如 programbench、source:programbench、difficulty:easy）

### Built-in tags 与筛选语义

Registry 和 Online Stream 会识别一组内建 tag。内建 tag 用于平台筛选和分组，不要和普通业务 tag 混用：

| Tag / metadata | 适用实体 | 语义 | 页面行为 |
|---|---|---|---|
| `source:<name>` | Env / Reward / Benchmark / Harness / Model | 数据或组件来源，如 `source:swebench`、`source:programbench` | Registry 顶部 Source 下拉 |
| `difficulty:<level>` | Env / Benchmark | 难度或样本复杂度，如 `difficulty:easy` | Registry 顶部 Difficulty 下拉 |
| `task:<name>` | Env | Dataset Task，表示这条 Env 属于哪个在线数据集任务 | Registry -> Env 的 Dataset Task 下拉；Stream 创建时选择的 Dataset Task |
| `online_task:<name>` | Env | Online RL 场景的 Dataset Task 别名 | 同上 |
| `dataset:<name>` | Env | 数据集命名空间，等价参与 Dataset Task 汇总 | 同上 |
| `partition:<YYYYMMDD>` / `partition:<YYYYMMDDHH>` | 动态 Env | 在线数据分区，通常按天或小时写入，如 `partition:20260713`、`partition:2026071308` | Registry -> Env 和 Stream 详情任务列表可筛选；Stream 创建表单不填写 |

等价 metadata 字段也会被识别：`metadata.task`、`metadata.online_task`、`metadata.dataset`、`metadata.partition`。推荐导入动态 Env 时同时写入 Dataset Task 和 partition，普通业务标签（如 `repo:django`、`split:verified`、`language:python`）继续作为 custom tags 使用。

Online Stream 里的 Dataset Task 不是运行时 `task_id`，也不是 Benchmark task。它只是选择数据集的命名空间；真正运行一条数据时仍然通过 `data_id + harness + reward` 异步启动。

---

## 工作流一：导入 Env（开源 benchmark → 平台）

### 心智模型

Env@v 是一道题的数据包 + 生命周期钩子，不包含评测逻辑。导入一个开源 bench = 每道题一个 Env@v + 一个共用 Reward@v + 一个 Benchmark@v 引用全部 Env（见工作流三、四）。

先选 runtime.kind：

- **files**：题目就是一堆文件（代码仓、文档、素材）。bundle 指向初始工作区目录，agent 跑在平台内置 harness 镜像里。适合 cleanroom coding、纯文本任务。
- **image**：题目需要特定系统环境（预装依赖、常驻服务、/testbed 等）。给 image（用户镜像）或 template（ARCA 模版 id），**profile 必填**（可用列表见 https://htnn-gateway.alipay.com/envarena/api/runtime-config）。适合 SWE-bench 类环境敏感任务。

### Use case：导入一道 files 题

~~~bash
# 1. 上传物料（目录自动 zip+解包，返回目录 URI）
arena upload ./Target.md                 # → uploads/…/Target.md
arena upload ./workspace                 # → uploads/…/workspace/（以 / 结尾）
arena upload ./oracle                    # → uploads/…/oracle/

# 2. 创建实体 + 版本 + 发布
arena env create --key programbench-cmatrix-env --name "ProgramBench cmatrix cleanroom" --tags programbench,source:programbench
arena env version-create programbench-cmatrix-env 1.0.0 --file spec.json
arena env publish programbench-cmatrix-env 1.0.0
~~~

spec.json（**只放 spec 本体**，version 由命令行给出）：

~~~json
{
  "query": { "uri": "uploads/260708/abc123/Target.md", "media_type": "text/markdown" },
  "bundle": { "uri": "uploads/260708/def456/workspace/", "media_type": "application/vnd.envarena.directory" },
  "oracle": { "uri": "uploads/260708/ghi789/oracle/", "media_type": "application/vnd.envarena.oracle.directory" },
  "oracle_schema": "programbench.oracle.v1",
  "runtime": { "kind": "files" },
  "timeout_seconds": 1800,
  "hooks": {
    "setup_script": "git init && git add -A && git commit -m base",
    "collect_script": "git diff HEAD > \"$ARENA_AGENT_OUTPUT_DIR/patch.diff\""
  },
  "timeouts": { "setup_seconds": 120, "agent_seconds": 1800, "collect_seconds": 120 },
  "tags": ["programbench"]
}
~~~

image Env 的差异部分：

~~~json
{
  "runtime": {
    "kind": "image",
    "image": "asr.antgroup-inc.cn/arcaarealagent/faas-swe:astropy-12907",
    "profile": "image_networked"
  },
  "workdir": "/testbed",
  "resource_hints": { "cpu": "4", "memory": "8Gi", "disk": "20Gi" }
}
~~~

### 要点

1. **Target.md（题面）**：明确告诉 agent 产物写到哪——要么约定写 `$ARENA_OUTPUT_DIR`，要么让 agent 只改 workspace、由 collect_script 统一提取；不要泄漏 oracle；明确告知 agent 不要自己跑隐藏评测
2. **hooks**：setup_script 建 git baseline、装依赖、起服务（files Env 不配则自动铺放 env files）；collect_script 把可评分产物写入 `$ARENA_AGENT_OUTPUT_DIR`，agent 超时也会执行，能抢救部分进度
3. **批量导入**：每道题循环上述命令；key 建议 `<bench>-<instance-id>` kebab-case，统一打 source:/difficulty: tags
4. spec 其余字段：`files: [{uri, dest?}]` 只读挂到 /envarena/env/files/<dest>（dest 相对路径不得 `..` 逃逸）；`workdir` 仅 image Env 有效

### 校验清单（publish 时平台强制）

- query.uri、runtime 必填；runtime.kind 只能是 files 或 image
- files Env 要求 bundle.uri；image Env 要求 runtime.image 或 runtime.template，且 runtime.profile 必填并存在于平台配置
- timeout_seconds 必须 >0；timeouts 各项非负
- oracle_schema 存在时 oracle.uri 必填；oracle 必须是独立对象/目录，不得烘进 Env 镜像（只挂 reward 沙箱，绝不进 agent 沙箱）
- Env 不写 Reward 或评分规则；换题目/换 oracle 一律发新 version

---

## 工作流二：适配 Harness（runtime / tools / skills）

### 心智模型

Harness@v 是被测 Agent 的声明式组合：runtime + system prompt + tools + skills。Harness 模型无关；被测模型由 Job 的 Model 配置经 model.env 注入。

- harness.runtime.type 支持 claude_code 与 runner；runtime.uri 是 agent runtime 可执行文件的 OSS 地址（如 claude 二进制或 runner 脚本），发布时物化为 ARENA_HARNESS_DIR/bin/{claude|runner}；uri 以 / 结尾时按目录整树物化到 bin/（入口文件名必须是 claude/runner，旁挂依赖随目录带入）
- tools 合并为 ARENA_HARNESS_DIR/mcp.json，多个 Tool 的 mcpServers 不能重名
- skills 挂载到 ARENA_HARNESS_DIR/skills/{skill_key}，由内置 harness 装配进私有 CLAUDE_CONFIG_DIR
- system_prompt 支持 kind=inline（content）或 kind=oss_object（uri）
- agent.env 是 Harness 私有环境变量覆盖层（不允许 ARENA_*/OTEL_*/IS_SANDBOX）；被测模型、网关、密钥放 Model@v.env

### Use case：组一个带浏览器能力的 coding agent

~~~bash
# 1. 上传 agent runtime 可执行文件
arena upload ./claude                    # → uploads/…/claude，填入 runtime.uri

# 2. Tool（可选）：内联 MCP 配置
arena tool create --key browser-mcp --name "Browser MCP"
arena tool version-create browser-mcp 1.0.0 --file tool-spec.json
arena tool publish browser-mcp 1.0.0

# 3. Skill（可选）：一步导入并发布，key/version 可从 SKILL.md frontmatter 推断
arena skill import ./browser-devtools/SKILL.md --key browser-devtools --version 1.0.0 --publish
# 或整包 zip / 远程 URL：
arena skill import ./bundle.zip --publish
arena skill import-url https://example.com/skills/browser-devtools.zip --publish

# 4. Harness 引用已发布的 Tool@v / Skill@v
arena harness create --key playwright-coding-agent --name "Playwright Coding Agent"
arena harness version-create playwright-coding-agent 1.0.0 --file harness-spec.json
arena harness publish playwright-coding-agent 1.0.0
~~~

tool-spec.json：

~~~json
{
  "mcp_config_inline": {
    "mcpServers": {
      "playwright": { "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "env": {} }
    }
  },
  "manifest": { "name": "browser-mcp", "description": "Browser automation MCP config.", "kind": "mcp" }
}
~~~

harness-spec.json：

~~~json
{
  "runtime": { "type": "claude_code", "uri": "uploads/260708/xy/claude" },
  "system_prompt": { "kind": "inline", "content": "You are a coding agent. Read $ARENA_QUERY_PATH and modify the mounted workspace." },
  "tool_refs": [ { "key": "browser-mcp", "version": "1.0.0" } ],
  "skill_refs": [ { "key": "browser-devtools", "version": "1.0.0" } ],
  "io_contract": { "output_file": "$ARENA_OUTPUT_DIR/harness_result.json" },
  "resource_spec": { "cpu": "4", "memory": "8Gi", "disk": "20Gi" },
  "ttl_seconds": 1800
}
~~~

SKILL.md 最小示例（frontmatter 必须含 name 和 description）：

~~~markdown
---
name: browser-devtools
description: Use browser automation to inspect and verify web UI tasks.
---

Use this skill when a task requires browser inspection or interaction.
~~~

### 校验清单（publish 时平台强制）

- runtime.type/runtime.uri 与 ttl_seconds(>0) 必填；runtime.type 用 claude_code
- tool_refs / skill_refs 必须引用**已发布**版本（引用 draft 会被拒绝）
- Harness 不固定被测模型；system prompt、tools、skills、runtime.uri、resources 或 TTL 变化时发新 version

---

## 工作流三：设计 Reward

### 心智模型

Reward@v 是评测运行时：读取 agent 产物和 Env oracle，输出归一化分数。Reward 不属于 Env，在 Benchmark 级别绑定；accepted_oracle_schema 声明它能处理哪种 Env oracle。

Reward 沙箱能读到什么（详见挂载布局表）：**首选评分输入**是 `$ARENA_AGENT_OUTPUT_DIR`（collect_script 提取的产物）；完整轨迹在 `$ARENA_ATTEMPT_DIR/trajectory.jsonl`；oracle 在 `$ARENA_ORACLE_PATH`；唯一可写 `$ARENA_REWARD_OUTPUT_DIR`。Reward 绝不读 agent stdout。

- runtime.kind 可选 image / inherit_env / shared_env：
  - **image**：独立评分镜像，与 Env 无关
  - **inherit_env**：Env 同镜像的**干净副本**，适合评分依赖 Env 环境（如跑隐藏测试），但看不到 agent 运行时留下的进程/服务
  - **shared_env**：在 agent 的**存活沙箱内**原地评分，适合 reward 需要探测 agent 拉起的 live 状态（后台服务端口、进程、内存/DB 数据）。不需要也不允许填 image/template。评分文件在 agent 结束后经 exec 通道注入（打包 gzip 后上限 64MiB，更大的资产请烘进 Env 镜像）
- files: [{uri, dest?}] 评分脚本发布时物化、只读挂到 $ARENA_REWARD_DIR——改评分逻辑只需传新脚本 + 发新 version，不用烘镜像
- ⚠️ files 有个坑：**uri 是单个文件时，dest 不要写 "."**。这么写发布不报错，但评分脚本实际没进评分目录，跑任务时会因找不到脚本而 EVAL_FAILED。三种正确写法任选：① uri 给目录（结尾带 /，如 uploads/…/reward/）+ dest "."；② uri 给单文件 + dest 写文件名（如 "grade.py"）；③ uri 给单文件、省略 dest（默认用文件名）
- eval_model 是评测用 judge 模型（可选），不是被测模型

shared_env 信任边界（选用前必读）：评分环境曾被 agent 完全控制，时序注入只保证 agent **运行期间**读不到 oracle；恶意 agent 可留后台进程在评分阶段窥探或干扰。适用于可信 agent 的能力评测，对抗/防作弊场景请用独立沙箱模式。此外 shared_env 任务**不支持 reeval**（live 状态随沙箱销毁，快照重测必然失真，接口返回 409）——重新评分只能重跑整个任务，调低成本的办法是先用冒烟 Benchmark 把评分脚本调稳定再上量。

### Use case：上传评分脚本并发布

~~~bash
arena upload ./evaluate.py               # → uploads/…/evaluate.py
arena reward create --key programbench-smoke-reward --name "ProgramBench smoke reward" --tags programbench,code-eval
arena reward version-create programbench-smoke-reward 1.0.0 --file reward-spec.json
arena reward publish programbench-smoke-reward 1.0.0
~~~

reward-spec.json：

~~~json
{
  "runtime": { "kind": "image", "image": "antregistry-pre.antgroup-inc.cn/arcaarealagent/programbench-reward:1.0.0" },
  "entrypoint": "python3 \"$ARENA_REWARD_DIR/evaluate.py\"",
  "normalization": ".reward",
  "files": [ { "uri": "uploads/260708/xyz789/evaluate.py", "dest": "evaluate.py" } ],
  "io_contract": { "output_file": "$ARENA_REWARD_OUTPUT_FILE", "artifact_globs": ["$ARENA_REWARD_OUTPUT_DIR/**/*"] },
  "accepted_oracle_schema": "programbench.oracle.v1",
  "resource_spec": { "cpu": "2", "memory": "4Gi" },
  "ttl_seconds": 900
}
~~~

shared_env 变体（评分需访问 agent 拉起的 live 服务时）——去掉 image，其余同形：

~~~json
{
  "runtime": { "kind": "shared_env" },
  "entrypoint": "python3 \"$ARENA_REWARD_DIR/grade.py\"",
  "normalization": ".reward",
  "files": [ { "uri": "uploads/260715/abc123/reward/", "dest": "." } ],
  "io_contract": { "output_file": "$ARENA_REWARD_OUTPUT_FILE" },
  "accepted_oracle_schema": "myenv.oracle.v1",
  "resource_spec": { "cpu": "1", "memory": "2Gi" },
  "ttl_seconds": 300
}
~~~

### 输出协议

entrypoint 把 JSON 写到 $ARENA_REWARD_OUTPUT_FILE（默认 /envarena/result/reward_result.json）。平台识别 status、score、sub_scores、raw、artifacts，其余放 raw：

~~~json
{ "score": 0.82, "sub_scores": { "functional": 0.9, "visual": 0.7 }, "pass": true, "raw": { "judged_by": "areal-swe" } }
~~~

计分规则：spec.normalization 是 jq 子集表达式（`.path.to.number`、`+`/`-`、数字常量），**对整个 reward_result.json 求值**。结果必须落在 [0,1]（保留 4 位小数），越界判 EVAL_FAILED。

### 调试循环（评分改了不用重跑 agent；shared_env 除外）

~~~bash
# 1. 先用 1 个 Env 组冒烟 Benchmark 跑通链路（见工作流四）
# 2. 改评分脚本 → 传新文件 → 发新 Reward 版本
arena upload ./evaluate.py
arena reward version-create programbench-smoke-reward 1.1.0 --file reward-spec.json
arena reward publish programbench-smoke-reward 1.1.0
# 3. 用新版本对已收集的 agent 产物重评
arena task reeval <task_id> --reward programbench-smoke-reward@1.1.0 --reason "修复pass率统计" --yes
arena task result <task_id>              # 查看新分数
# 4. 评分中间产物写 $ARENA_REWARD_OUTPUT_DIR/artifacts/，排查：
arena task artifacts <task_id>
arena task download <task_id> <产物名> -o ./dbg.log
~~~

⚠️ shared_env 任务对 reeval 一律返回 409（live 状态已随沙箱销毁）：改了评分脚本后需**重跑整个任务**验证；显式传非 shared 的 `--reward` 换评分器对文件快照重评仍被允许。

### 校验清单（publish 时平台强制）

- runtime.kind=inherit_env 或 shared_env，或提供 runtime.image / runtime.template
- runtime.kind=shared_env 时 image/template 必须为空（评分跑在 agent 沙箱里，填了会被发布拒绝）
- entrypoint、normalization、ttl_seconds(>0) 必填；normalization 语法在 publish 时校验（shared_env 的 ttl_seconds 同时用于延长 agent 沙箱存活时间）
- accepted_oracle_schema 应与目标 Env 的 oracle_schema 匹配（benchmark publish / job create 时校验兼容性）
- ⚠️ runtime.kind=inherit_env 的 Reward 只能配 image 类型的 Env（reward 沙箱复用 Env 镜像）；files 类型 Env 须配独立 image/template 的 Reward；shared_env 对 Env 类型无限制
- Reward 不引用具体 Env；绑定由 Benchmark 完成

---

## 工作流四：组装 Benchmark、配置 Model、运行 Job

### Use case：从组装到拿分

~~~bash
# 1. Benchmark = N Env@v + 1 Reward@v + aggregation（全部须已发布）
arena benchmark create --key programbench-cmatrix-1case --name "ProgramBench cmatrix 1-case"
arena benchmark version-create programbench-cmatrix-1case 1.0.0 --file bench-spec.json
arena benchmark publish programbench-cmatrix-1case 1.0.0

# 2. Model（被测模型：名字/参数/凭证全在 env 里）
arena model create --key qwen-max --name qwen-max
arena model version-create qwen-max 1.0.0 --file model-spec.json
arena model publish qwen-max 1.0.0

# 3. 创建 Job 并等待结果（watch 轮询到终态后自动拉取聚合分）
arena job create --file job.json
arena job watch <job_id>
~~~

bench-spec.json：

~~~json
{
  "reward_ref": { "key": "programbench-smoke-reward", "version": "1.0.0" },
  "env_refs": [ { "env_ref": { "key": "programbench-cmatrix-env", "version": "1.0.0" }, "weight": 1 } ],
  "aggregation": { "method": "mean", "failed_task_policy": "failed_as_zero" }
}
~~~

- 必须有且只有一个 reward_ref、一个或多个 env_refs；aggregation.method 可选 mean、weighted_mean、pass_at_k（k 来自 Job 的 sample_numbers）、paired（对照评测，见下节）
- publish 时平台强制校验（不满足会被拒绝，避免发布出跑不起来的评测集）：reward/env 版本均已发布；env 的 oracle_schema 与 reward 的 accepted_oracle_schema 兼容；reward runtime.kind=inherit_env 时所有 env 必须是 image 类型
- `version-create` 支持 `--publish` 一步创建并发布

### 对照评测（variants：同一任务带/不带干预各跑一遍，比差值）

有些评测测的不是"任务能否完成"，而是**某个干预带来的增益**——典型如 SkillsBench：同一道题带 skill 与不带 skill 各跑一遍，看通过率差值。Benchmark 里声明 `variants`（对照臂）即可，一次 Job 直接出对比分：

~~~json
{
  "reward_ref": { "key": "sb-reward", "version": "1.0.0" },
  "env_refs": [ {
    "env_ref": { "key": "sb-exam-block-sequencing", "version": "1.0.0" },
    "skill_refs": [ { "key": "mip-solver-and-solution-audit", "version": "1.0.0" } ]
  } ],
  "variants": [
    { "name": "no-skill" },
    { "name": "with-skill", "use_env_skills": true }
  ],
  "aggregation": {
    "method": "paired",
    "params": { "baseline": "no-skill", "treatment": "with-skill", "metric": "delta" }
  }
}
~~~

**运行机制**：Job 按 env × sample × variant 展开，每个臂是一个普通 Task、各自全新沙箱（TaskID 形如 `job-x:env:0#with-skill`）。env/model/harness/verifier 在臂间严格相同，唯一差异是 variant 声明的干预；比较发生在 Job 聚合层，按 (env, sample) 配对计算。

**VariantSpec 字段**（都是可选的"agent 上下文干预"，字段留空 = 该臂什么都不加）：

| 字段 | 作用 |
|---|---|
| `name` | 臂标签，Job 内唯一，用于配对与 TaskID 后缀；平台不解释其含义 |
| `use_env_skills` | true 时把该 env 条目 `skill_refs` 引用的 Skill 注入 agent（claude 从技能列表可见、可调用） |
| `skill_refs` | 本臂额外注入的 Skill（对所有 env 生效），与 env 级 skill_refs 合并 |
| `system_prompt_suffix` | 追加到 harness system prompt 之后（只允许追加不允许替换，保证臂间可比） |
| `env` | 本臂附加的沙箱环境变量 |

注意 skill 的绑定分两层：**env_refs 条目上的 `skill_refs` 是数据**（这道题关联哪些 Skill，引用 Skill 库的已发布版本），**variant 是开关**（这一臂给不给）。Env 本身不带 skill 字段，两臂共享同一个 Env 版本。

**Skill 注入与失败语义**：平台按 Env 的 key + version 精确匹配 `env_refs`；启用 `use_env_skills` 后，把每个 Skill 只读挂到 `/envarena/skills/<skill-key>`，通过 `ARENA_TASK_SKILLS_DIR=/envarena/skills` 交给 Harness，再合并进 Claude 的私有 `CLAUDE_CONFIG_DIR/skills/`。`no-skill` 臂不设置该变量。with-skill 已声明注入时采用 fail-closed：挂载目录缺失、为空，或任一 Skill 缺少有效 `SKILL.md`，Task 直接 `HARNESS_FAILED`，不会静默按 no-skill 继续。`variant.env` 不得覆盖 `ARENA_*`、`OTEL_*`、`IS_SANDBOX` 等平台保留变量。

排查注入可先看 harness 日志：`arena job log <job_id> <task_id> harness`；成功时包含 `loaded per-task skills: <skill-key>...`。该日志只证明装配成功，最终仍应结合 trajectory 的 Claude `init.skills` 核对 Agent 可见性。

**paired 聚合参数**：

| 字段 | 取值与含义 |
|---|---|
| `baseline` / `treatment` | 必须分别引用已声明的对照臂和干预臂 |
| `score_mode` | `binary`（默认）：Reward 达到 `pass_threshold` 记 1，否则记 0；`continuous`：保留并平均 Reward 的 `[0,1]` 连续分值 |
| `metric` | `delta`（默认）：`T-B`；`normalized_gain`：`(T-B)/(1-B)`（B=1 时回退为 `delta`）；`treatment_rate`：`T`，其中 B/T 是两臂按 `score_mode` 得到的平均分 |
| `incomplete_pairs` | `drop`（默认）：丢弃缺臂的整对；`count_as_fail`：缺失臂按 0 分计入 |

`continuous` 下，对 N 个计入聚合的配对：`B = (Σ bᵢ) / N`，`T = (Σ tᵢ) / N`，其中 `bᵢ,tᵢ ∈ [0,1]` 是各配对两臂的 Reward 原始分。

`continuous` 适合含 partial reward（如 `0.625`）的 Benchmark；不配置 `score_mode` 时保持原有二值通过率语义。配对四格表始终按 `pass_threshold` 统计，不受 `score_mode` 影响。

**结果读法**：`arena job result` 里 `aggregate_score` 是主分（metric 决定）；`sub_scores` 保留两臂各自聚合分、delta 与配对四格表（both_pass / only_treatment / only_baseline / both_fail）；`pair_table` 是逐题逐 sample 的原始配对明细——换 metric 口径只需重新聚合，不用重跑任务。

要点：

- 不声明 variants 的 Benchmark 行为与从前完全一致（单臂）；paired 聚合要求恰好 2 个臂
- 单次试验的 delta 噪声很大（模型非确定性，skill 单次为负增益很正常）；配合 sample_numbers>1 做多 trial 配对统计才有结论
- verifier 对 variant 无感知：所有臂用同一 Reward 同一评分脚本，不要在评分逻辑里读 ARENA_VARIANT
- 校验：variant 名唯一且不含 `:`/`#`；所有 skill_refs 必须引用已发布 Skill；paired 的 baseline/treatment 必须在 variants 中声明；`variant.env` 不得使用平台保留变量

model-spec.json（env 会注入 Harness/Reward 沙箱；claude_code harness 常用 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL；env 的 key 不能是 IS_SANDBOX、ARENA_*、OTEL_* 前缀）：

~~~json
{
  "model_name": "qwen-max",
  "parameters": { "temperature": 0.2 },
  "env": { "ANTHROPIC_BASE_URL": "https://gateway.example", "ANTHROPIC_AUTH_TOKEN": "replace-me", "ANTHROPIC_MODEL": "qwen-max" }
}
~~~

job.json：

~~~json
{
  "benchmark_ref": { "key": "programbench-cmatrix-1case", "version": "1.0.0" },
  "harness_ref": { "key": "playwright-coding-agent", "version": "1.0.0" },
  "model_ref": { "key": "qwen-max", "version": "1.0.0" },
  "sample_numbers": 5,
  "concurrency_policy": { "max_parallel": 8, "max_retries": 1 }
}
~~~

- job_id 可省略（自动生成）；自定义时只填写短名（如 programbench），服务端会自动追加 `-YYMMDDHHMM`，也可用 `--job-id` 覆盖
- 简单场景可全用参数免写 JSON：`arena job create --benchmark <k@v> --harness <k@v> --model-ref <k@v> --samples 1`
- sample_numbers 是 pass@k 的 k；任务总数 = env_refs × sample_numbers（Benchmark 有 variants 时再 × 臂数）
- 不传 concurrency_policy 时服务端默认 max_retries=1（基础设施抖动自动重试一次）；显式传入则以传入值为准
- 创建时即校验 Harness/Env/Reward/oracle_schema 兼容性，失败会返回具体原因

### 监控与排查

| 命令 | 用途 |
|---|---|
| `arena job watch <job_id>` | 轮询至终态（DONE/FAILED/CANCELLED/TIMEOUT）或停稳（STOPPED），自动拉结果；失败时退出码 1 |
| `arena job get / result <job_id>` | Job 详情（含任务列表）/ 聚合分数 |
| `arena job list --status RUNNING --benchmark <key> --model <name>` | 按条件筛 Job |
| `arena job trajectories <job_id>` | 全部任务轨迹概览 |
| `arena job trajectory <job_id> <task_id> [--format raw]` | 单任务完整轨迹（raw 为 stream-json NDJSON） |
| `arena job log <job_id> <task_id> <harness\|runner\|reward> [--attempt N] [--offset B] [--limit B]` | 分页读取任务执行日志（字节偏移分页；runner 缺失时回落 harness，reward 缺失时回落共享沙箱 reward；JSON 模式含 next_offset/total_bytes） |
| `arena job stop <job_id> --yes` | 暂停 Job（可恢复）：取消调度、销毁活跃沙箱，停到 STOPPED；DONE 任务保留 |
| `arena job start <job_id>` | 从 STOPPED 恢复：只重跑未成功任务，DONE 任务及分数保留 |
| `arena job cancel <job_id> --yes` | 取消 Job 并销毁沙箱（终止语义，不可恢复；与 stop 不同） |
| `arena task get / result <task_id>` | 单任务详情 / 评分明细 |
| `arena task trace <task_id>` | 事件时间线 + 各阶段状态与错误消息 |
| `arena task artifacts <task_id>` | 各阶段日志与产物列表 |
| `arena task download <task_id> <name> [-o 路径]` | 下载某个产物 |
| `arena task reeval <task_id> [--reward key@version] --yes` | 重评分（不重跑 agent） |
| `arena leaderboard --benchmark <key>` | 同 Benchmark 下各 Job 榜单 |

Task 状态机（正常路径）：PENDING → PREPARING → WAITING_AGENT_SBX_SLOT → CREATING_AGENT_SBX → AGENT_RUNNING → WAITING_REWARD_SBX_SLOT → CREATING_REWARD_SBX → EVALUATING → RECORDING → DONE。

失败终态：SETUP_FAILED（setup_script 失败）/ HARNESS_FAILED（agent 阶段失败）/ COLLECT_FAILED（collect_script 失败）/ EVAL_FAILED（reward 失败或分数越界）/ TIMEOUT / FAILED / CANCELLED；PENDING_EVAL 表示 agent 已完成但评分被中断（`arena api post /recovery` 可续跑）。

**排查失败任务的顺序**：

~~~bash
arena job get <job_id>                   # 1. 找到失败 task，先分清失败在哪个阶段
arena task trace <task_id>               # 2. 看事件流里的错误消息
arena task artifacts <task_id>           # 3. 下载对应阶段日志（setup/agent/collect/reward）
arena job trajectory <job_id> <task_id>  # 4. 回放 agent 实际行为：题面问题 / harness 问题 / 评分问题
~~~
