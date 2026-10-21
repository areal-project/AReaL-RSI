# Verilog benchmark 数据、SIF、Agent 与评测

准备 RTLLM v2.1、VerilogEval v2 和 CVDP v1.1.0。每个 benchmark 使用一个独立 SIF；
只执行开源工具题，不需要 GPU、Docker、VCS 或 Cadence 许可证。模型推理不包含在镜像中。

## 已准备的资源

| Benchmark | 数据 | 镜像 | 工具链 |
| --- | --- | --- | --- |
| RTLLM v2.1 | `data/rtllm/`，50 题及全部辅助文件 | `images/rtllm-v2.1-icarus.sif`，约 490 MiB | Python 3.11.2、Icarus 12.0 |
| VerilogEval v2 | `data/verilogeval/`，两种任务格式各 156 题 | `images/verilogeval-v2.sif`，约 490 MiB | Python 3.11.2、Icarus 12.0、Verilator 5.006 |
| CVDP v1.1.0 | `data/cvdp/` | `images/cvdp-v1.1.0-oss.sif`，约 528 MiB | Python 3.12.3、Icarus 13.0、Verilator 5.038、Yosys 0.40、cocotb 2.0.1、pytest 8.3.2 |

CVDP 已下载普通生成 302 题、agentic 92 题、heavy agentic 75 题、代码理解 123 题，
以及 heavy 非商业题所需的 36 个 Git bundles。全部 75 个 heavy context commit 均在对应
bundle 的 advertised refs 中。未下载商业 JSONL，也不执行商业分类 cid012/cid013/cid014。

`data/manifest.json` 记录文件 SHA-256、上游 Git commit 和 Hugging Face revision。
每个 SIF 附带 `.sha256`、`.inspect.json`、`.requirements.txt` 和 `.environment.txt`。
`containers/build-inputs.json` 固定构建源码归档的 URL、大小和 SHA-256；两个依赖锁记录
实际验证过的完整 Python 包版本。RTLLM 和 VerilogEval 复用 `rtl-requirements.lock`。

上游固定版本：

- [RTLLM](https://github.com/hkust-zhiyao/RTLLM)：`51ed553d0ffd32797a1a0a13e051656bf302c81f`。
- [VerilogEval](https://github.com/NVlabs/verilog-eval)：`c498220d0a52248f8e3fdffe279075215bde2da6`。
- [CVDP tooling](https://github.com/NVlabs/cvdp_benchmark)：`8e894cf74414ab1eaea1e2b4e80a02f123df07b6`。
- [CVDP dataset](https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset)：`5b807d945f6a99aa645f7e43a64a2115e281b4bf`。

原始许可证和 NOTICE 随数据、源码保留。源码、数据、镜像、缓存和运行日志不进入 Git。

## 参考解检查结果

以下是环境验证结果，不是模型成绩。失败题保留在分母中，检查发现失败时返回非零。

| 检查 | 结果 | 原始记录 |
| --- | --- | --- |
| RTLLM，参考模块名适配及 Icarus 语法兼容补丁 | 50/50 编译成功，47/50 测试通过 | `runs/rtllm-golden-984003/summary.json` |
| VerilogEval，spec-to-RTL | 155/156 通过，1 题编译失败 | `runs/verilogeval-golden-983997/summary.json` |
| VerilogEval，code completion | 156/156 通过 | `runs/verilogeval-golden-984002/summary.json` |
| CVDP 官方有解非商业样例 | 2/2 通过，包含普通答案和 agentic golden patch | `runs/cvdp-golden-983999/summary.json` |
| CVDP 代码理解的官方客观指标自检 | 123/123，不调用 LLM judge | `runs/cvdp-golden-984012/summary.json` |

三个 benchmark 都执行了故意损坏 RTL 的负向检查，均正确识别编译或测试失败；
记录分别为 `runs/rtllm-golden-984006/`、`runs/verilogeval-golden-984000/` 和
`runs/cvdp-golden-984007/`。
另用 CVDP 官方 arbiter golden RTL 验证了 Yosys 综合/ABC 映射和 Verilator lint，均通过；
日志为 `logs/yosys-golden-smoke.log` 和 `logs/verilator-golden-smoke.log`。

### 未通过 golden 的题目

以下 4 个检查项需要单独排查。表中现象来自实际运行；时序竞争是根据源码提出的
排查方向，尚未通过修改后复测确认为根因。原始题目和参考实现保持不变，失败题仍计入总数。

| Benchmark / 题目 | 实际失败现象 | 已确认的问题或排查方向 | 原始日志 |
| --- | --- | --- | --- |
| RTLLM `radix2_div` | 编译成功；8 组向量中 3 组输出不匹配 | 期望/实际分别为 `00f6/faf1`、`000a/fa0f`、`0001/6400`；需要继续区分参考 RTL 的计算问题与测试台握手、采样时序问题 | [simulation.log](runs/rtllm-golden-984003/radix2_div/simulation.log) |
| RTLLM `clkgenerator` | 编译成功；测试台报告 20 次失败 | 测试台每隔 5 个时间单位采样，参考时钟也每隔 5 个时间单位翻转，存在同一仿真时刻的采样竞争风险 | [simulation.log](runs/rtllm-golden-984003/clkgenerator/simulation.log) |
| RTLLM `ring_counter` | 编译成功、退出码为 0；仿真到 `#100` 结束，但没有终态成功标记 | 两个 `posedge` 进程共享计数器 `i`，可能因进程执行顺序错过 `i == 9` 的完成判定；正常退出不足以证明通过 | [simulation.log](runs/rtllm-golden-984003/ring_counter/simulation.log) |
| VerilogEval spec-to-RTL `Prob099_m2014_q6c` | elaboration 失败，报告 `Y2/Y4` 不是模块端口 | 已确认参考 RTL/提示声明 `Y1/Y3`，测试台连接 `Y2/Y4`；同题的 code-completion 数据使用一致的接口并通过 | [compile.log](runs/verilogeval-golden-983997/Prob099_m2014_q6c/compile.log) |

RTLLM 上述三题属于 **Icarus 环境下 golden 未通过**，尚不能据此断言在其他仿真器下
也会失败。VerilogEval 的端口不一致则是本次固定版本中的数据问题。

RTLLM 的部分参考模块名带 `verified_` 前缀，验证时在独立工作副本中改为测试台使用的
模块名。`--icarus-compat` 额外应用 `compat/` 下三个补丁：将 unpacked array 初始化
展开为逐元素初始化、将一个 `break` 改为退出命名块、将实例名 `checker` 改名以避开
SystemVerilog 关键字。补丁不修改 DUT 逻辑、输入向量或判定条件。每题工作目录保存
`adaptations.patch`；原始 `data/` 与 `source/` 不变。

**CVDP 的 469 道生成题公开数据只有空的 output/patch，无法进行全量 golden 验证。**
官方仓库仅提供 1 道普通题和 1 道 agentic 非商业有解样例，本次运行了两者。
`cvdp-audit.json` 逐题记录全部 469 题的原始服务命令、环境文件、挂载及缺失参考解情况，
这属于配置检查，不等同于这些题全部通过。代码理解数据的 123 个参考答案可用，
`--comprehension` 仅用参考答案自身检查官方 BLEU/ROUGE 函数，不代替正式 LLM 主观评分。

## 运行

提交端/调度脚本使用 Python >=3.10 和 PyYAML 6.0.2；计算节点原生 Python 3.6 不满足要求。
可以用自己的共享 Python 环境，通过 `VERILOG_RUNNER_PYTHON` 指定。
当前验证使用已有共享 Python 3.12 运行时，PyYAML 安装在本例独立缓存中：

```bash
export VERILOG_RUNNER_PYTHON=/path/to/python3.12
```

从仓库根目录在 CPU allocation 内运行：

```bash
bash examples/verilog_design/scripts/verify.sh rtllm --icarus-compat --timeout 30
bash examples/verilog_design/scripts/verify.sh verilogeval --timeout 30
bash examples/verilog_design/scripts/verify.sh verilogeval --task-format code-complete-iccad2023
bash examples/verilog_design/scripts/verify.sh cvdp --timeout 600
bash examples/verilog_design/scripts/verify.sh cvdp --comprehension
```

一次只运行一个命令；每次使用独立 Slurm job，输出位于 `runs/<bench>-golden-<jobid>/`。
无 Slurm 时使用时间戳。目录已存在时拒绝覆盖。`--task <id>` 可选单题；
`--negative-control --task adder_8bit` 可验证 RTLLM 错误实现确实失败。
负向检查的退出码表示是否成功检测到错误，其 `summary.json` 仍保留真实失败状态。

例如提交单个验证作业：

```bash
sbatch --cpus-per-task=8 --mem=12G --time=00:15:00 \
  --output=examples/verilog_design/logs/verify-%j.log \
  --wrap='bash examples/verilog_design/scripts/verify.sh rtllm --icarus-compat --timeout 30'
```

CVDP 的 Apptainer 入口从原始 Compose 配置解析各服务命令和 `.env`，用同一个 SIF 逐一执行。
每题拥有独立的 `/code` 和 `/rundir`，官方 harness 挂载到 `/src`。
参考补丁使用上游 `merge_in_memory.diff_apply` 应用；不调用 Docker，不改写测试源码。
未知服务选项、镜像或额外 Dockerfile 构建步骤会明确报错。

## Codex Agent 盲测

现已接通：公开输入导出 → SIF 内 Codex 自测/调试 → 冻结提交 → 独立进程运行官方测试。
Agent 结束后才启动评测，不将官方测试结果反馈给本次 Agent。失败仍计入成绩。

| Benchmark | Agent / Judge 可共用的 SIF | 本次抽测 |
| --- | --- | --- |
| RTLLM v2.1 | `images/rtllm-v2.1-icarus-codex.sif`，约 628 MiB | `accu`、`adder_8bit`、`counter_12`：3/3 通过 |
| VerilogEval v2，spec-to-RTL | `images/verilogeval-v2-codex.sif`，约 628 MiB | `Prob009_popcount3`、`Prob047_dff8ar`、`Prob110_fsm2`：3/3 通过 |
| CVDP v1.1.0，普通非商业 RTL 生成 | `images/cvdp-v1.1.0-oss-codex.sif`，约 668 MiB | `cvdp_copilot_bcd_adder_0001`、`cvdp_copilot_bcd_counter_0001`、`cvdp_copilot_signed_adder_0001`：3/3 通过 |

三个镜像均包含本机 **Codex CLI 0.163.0** 的 `codex`、`codex-code-mode-host` 和 `rg`
原生二进制，不依赖 Node.js。`containers/agent-cli-lock.json` 固定二进制大小、SHA-256
和来源路径；镜像内部 `/opt/agent-build-inputs.sha256` 另记录实际入口脚本和二进制哈希。
原有 EDA SIF 保留作为构建基底和历史验证证据；实际 Agent 与 Judge 可以使用同一个
`*-codex.sif`，不需要单独构建含测试数据的镜像。

本次模型固定 `gpt-6.1-sol`、reasoning effort `high`。实际答题记录位于
`runs/{rtllm,verilogeval,cvdp}-agent-v2-984065/`，包括公开输入、Agent 自测、事件日志、
冻结提交及 SHA-256、官方判定。初次判定使用原有 EDA 镜像；随后将相同冻结提交在最终
Codex SIF 内重新评测，仍为 9/9，并且三个 benchmark 的故意损坏提交均被拒绝。
后者记录位于 `runs/agent-final-judge-984065/`。新增运行入口直接使用最终 Codex SIF 评测。
`agent-validation.json` 汇总本次证据；这 9 题是有意选择的小规模连通性检查，不代表全量成绩。

### 隔离方式

- SIF 仅包含 CLI、运行时和 EDA 工具；题目、官方测试、golden 均在镜像外。
- Agent 只收到题目描述和官方允许公开的 `input.context`；RTLLM 仅导出
  `design_description.txt`，VerilogEval 仅导出对应格式的 `*_prompt.txt`。
- 容器内 `CODEX_HOME` 使用启动时指定的同一路径，但挂载的是每题独立目录。
  **只从宿主指定目录读取并复制 `auth.json`**；不挂载宿主整个 CODEX_HOME，
  不导入配置、会话、记忆、skills、插件、hooks 或 MCP。任务结束/控制器中断时删除凭据副本。
- Codex 使用 `--ignore-user-config --ignore-rules --ephemeral`；禁用网页搜索、外部应用、
  插件、浏览器和多 Agent 工具。Agent 工具全部在容器内执行。
- Apptainer 使用独立 PID/IPC/网络、UID 65534、零 effective capabilities；禁用 HOME、
  当前目录、hostfs 和管理员额外挂载。容器只有该题工作区、独立 home、只读控制文件
  和代理 socket；不挂载仓库、数据目录、历史运行或 Judge 工作区。
- Codex 使用 `danger-full-access` 在上述外部隔离环境内自主调试；隔离由 Apptainer
  和挂载/网络策略承担，不依赖提示词要求 Agent 保密。
- 容器没有外部网卡，只在内部 `127.0.0.1:1081` 提供代理入口，通过 Unix socket、
  带随机凭证的转发和启动机的 `http://127.0.0.1:1081` 出网。转发仅允许 CONNECT
  到 `chatgpt.com`、`api.openai.com`、`auth.openai.com`、`ab.chatgpt.com` 的 443 端口；
  GitHub、Hugging Face 等源站被拒绝，Agent 看不到转发凭证。
- 每题模型调用前，实际检查隐藏路径及符号链接、非特权 UID/capabilities、直连阻断、
  GitHub 代理拒绝，以及自写 RTL 的编译/仿真；任一检查失败就不调用模型。
- Agent 退出后只收集指定 RTL 文件；拒绝符号链接、非普通文件和超大文件，独立复制、
  哈希并设为只读。Judge 随后才挂载官方测试/参考实现；它的目录和日志不提供给 Agent。

### 运行 Agent

在**本机 1081 代理所在的启动机**执行，脚本自动启动受限转发并提交 CPU Slurm 作业。
启动机地址需要能被计算节点访问；可用 `VERILOG_PROXY_LISTEN` 指定。
在 Git 忽略的 `.local/run_agent.sh` 中设置本机 `CODEX_HOME`、Python 路径和代理，
然后调用公共启动脚本；这些本机设置不进入提交。公共入口要求设置 `CODEX_HOME`。
模型默认值为 `gpt-6.1-sol` / `high`，可通过 `--model`、`--effort` 指定。

```bash
export VERILOG_RUNNER_PYTHON=/path/to/python3.12
export CODEX_HOME=/path/to/codex-home

bash examples/verilog_design/scripts/run_agent.sh rtllm \
  --task accu --task adder_8bit --task counter_12
bash examples/verilog_design/scripts/run_agent.sh verilogeval \
  --task Prob009_popcount3 --task Prob047_dff8ar --task Prob110_fsm2
bash examples/verilog_design/scripts/run_agent.sh cvdp \
  --task cvdp_copilot_bcd_adder_0001 --task cvdp_copilot_bcd_counter_0001 \
  --task cvdp_copilot_signed_adder_0001
```

每条命令为一个独立 Slurm job；每题独立 Codex 会话，默认 Agent 限时 1200 秒、Judge
限时 120 秒。`--probe-only` 只检查隔离和 EDA，不调用模型、不评测。启动脚本退出时
关闭自己的转发，取消尚未结束的作业；成功返回必须包含全部所选题目。

VerilogEval 另支持 `--task-format code-complete-iccad2023`。当前 CVDP Agent 入口只连接
普通非商业 RTL 生成任务；agentic、heavy 和代码理解暂未连接此入口。
CVDP 抽测通过的是官方测试，未将空 golden 当成通过，也没有使用上述官方有解样例代答。
镜像和这些文件/网络检查不构成独立虚拟机，也不声称防御宿主内核或容器运行时漏洞。

### 构建与检查

先在安装 Codex 的启动机提取原生二进制，再在 CPU allocation 内构建：

```bash
python examples/verilog_design/scripts/prepare_agent.py
# 以下命令在 CPU allocation 内逐一执行
bash examples/verilog_design/scripts/build_agent.sh rtllm
bash examples/verilog_design/scripts/build_agent.sh verilogeval
bash examples/verilog_design/scripts/build_agent.sh cvdp
```

构建时校验二进制与 lock 一致，已有镜像拒绝覆盖。升级 CLI 时重新提取并审查 lock，
在新的构建目录生成镜像，保留旧实验记录。Codex 参数参考
[官方 CLI 文档](https://developers.openai.com/codex/cli/reference)。

```bash
PYTHONPATH=examples/verilog_design/cache/runner-deps \
  python examples/verilog_design/scripts/test_agent.py -v
```

新增 7 项测试覆盖公开导出、提交冻结、符号链接、FIFO、路径逃逸、超时杀死后代进程
和控制器中断清理。记录为 `logs/agent-boundary-tests.log`。启动脚本完整提交路径另通过
两个不调用模型的 Slurm 作业检查（984080、984083）。

整合最新 main 前再次执行完整 `make check`：UI 构建与 3 个测试通过；pytest 在已有会话恢复测试
处再次停住，180 秒后中断，记录为 `logs/agent-make-check-984065.log`。排除此前已确认
超时的同一测试后，其余 179 个测试及 5 个 subtests 通过，1 个仅 macOS 支持的测试在 Linux 跳过，
1 个测试 deselect；仓库全量 Ruff 和 70 文件 Mypy 通过，记录为
`logs/agent-check-rest-984065.log`。历史 `validation.json` 未改写；整合后完整检查结果见下文。

## 重新准备与构建

```bash
python3 examples/verilog_design/scripts/fetch.py
python3 examples/verilog_design/scripts/prepare_build.py
```

`fetch.py` 固定上游提交，复制原始任务数据并下载非商业 CVDP 数据及对应 bundles。
`prepare_build.py` 下载并校验构建工具链归档。无需向仓库的共享 `.venv` 安装 EDA 依赖。

`scripts/build.sh` 在 CPU Slurm allocation 中构建，建议 8 CPU/24 GiB；CVDP 建议
12 CPU/48 GiB。默认基镜像为 `images/debian-12.sif`（前两个 benchmark）和
`images/ubuntu-24.04.sif`（CVDP）。也可用 `VERILOG_BASE_IMAGE` 指定本地基镜像；
本地验证使用已有 Debian 12 基镜像。
CVDP 基镜像从已校验的官方 Ubuntu Base 24.04.3 rootfs 构建，不依赖 Docker Hub。

```bash
export VERILOG_BASE_IMAGE=/path/to/debian-12.sif
bash examples/verilog_design/scripts/build.sh rtllm
bash examples/verilog_design/scripts/build.sh verilogeval
unset VERILOG_BASE_IMAGE
bash examples/verilog_design/scripts/build.sh cvdp
```

已存在的目标 SIF 不会覆盖。若需重建，使用新的工作目录保留旧镜像和验证证据。
构建默认使用 PyPI，可通过 `VERILOG_PYPI_INDEX_URL` 指定包索引；源码归档在提交端预取，
构建不再下载 Python 运行时。最终软件包清单、基镜像哈希和工具版本均在 `images/` 中保留。

## 仓库开发检查

PR 分支整合到 `origin/main` 的 `b3d2c0a` 后重新执行完整 `make check`，全部通过：
UI 构建与 2 个测试、7 个仓库测试、全量 Ruff、62 文件 Mypy 均通过，没有跳过测试。
记录为 `logs/pr-make-check-984091.log`。新增 7 项 Agent 边界测试也再次通过，记录为
`logs/pr-rebase-boundary-tests.log`；本地隐藏启动脚本通过 Slurm 作业 984090 的实际隔离预检。
本地仅验证 Linux，macOS 由仓库 CI 覆盖。实际 CODEX_HOME 与凭据未加入 Git。

以下为整合前的历史检查记录，保留用于追溯，不代表当前 PR 检查状态。
当时完整执行过 `make check`，未得到全绿结果：

- UI 构建和 3 个 UI 测试通过。
- 单元测试停在已有的
  `ResearchTests::test_model_switch_preserves_active_turn_and_survives_reopen`，隔离复跑 70 秒仍超时。
  原记录为 `logs/make-check-984005.log`，隔离诊断为 `logs/check-isolated-984015.log`。
- 排除这一个超时测试后，179 个测试及 5 个 subtests 通过；1 个 macOS assertion 测试在
  Linux 跳过。记录为 `logs/check-rest-984017.log`。
- Mypy 的 70 个源文件通过。首次仓库全量 Ruff 报告 1466 条问题，均来自此前
  `examples/mle_bench/source/` 或其 `cache/` 下的上游源码。
  随后已将整个 `mle_bench` 目录移至仓库外的
  `../mle_bench`，目录内容和历史记录保留。
  移出后重新执行 `ruff check src tests examples` 全量通过，记录为
  `logs/ruff-after-mle-move.log`。此前 `make check` 和 `validation.json` 保留原始检查结果，
  不将新的 Ruff 结果写回历史记录；完整 `make check` 仍受上述会话恢复测试超时影响。
- `ruff check examples/verilog_design`、Python 语法检查和两个 shell 入口的语法检查通过。

上述检查使用本例缓存中的独立开发虚拟环境，没有重建仓库现有的共享 `.venv`。
