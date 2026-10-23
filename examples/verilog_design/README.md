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
共 592 题，均已连接 Agent 与独立判卷入口；heavy 非商业题所需的 36 个 Git bundles
已按固定上游版本的 Git LFS 大小和 SHA-256 校验，全部 75 个 context commit 均成功导出。
未下载商业 JSONL，也不执行商业分类 cid012/cid013/cid014。

`data/manifest.json` 记录文件 SHA-256、上游 Git commit 和 Hugging Face revision。
Git 中的 `data/cvdp/manifest.json` 另固定四个 CVDP 子集的题数、大小和 SHA-256，
以及 36 个 heavy bundle 的下载地址、大小、SHA-256 和全部 75 个 context commit。
2026-10-22 修复了 17 个截断的 heavy bundle，旧文件保留在 `cache/invalid-bundles/`。
历史 manifest 保留；当前 bundle 的校验收据见
`runs/cvdp-coverage-984625/bundle-integrity.json`。`fetch.py` 现在检查 HTTP 响应长度、
续传起点和上游 LFS 哈希，已有损坏 bundle 也会重新下载，避免仅检查 ref 列表而漏掉截断。
每个 SIF 附带 `.sha256`、`.inspect.json`、`.requirements.txt` 和 `.environment.txt`。
`containers/build-inputs.json` 固定构建源码归档的 URL、大小和 SHA-256；两个依赖锁记录
实际验证过的完整 Python 包版本。RTLLM 和 VerilogEval 复用 `rtl-requirements.lock`。

上游固定版本：

- [RTLLM](https://github.com/hkust-zhiyao/RTLLM)：`51ed553d0ffd32797a1a0a13e051656bf302c81f`。
- [VerilogEval](https://github.com/NVlabs/verilog-eval)：`c498220d0a52248f8e3fdffe279075215bde2da6`。
- [CVDP tooling](https://github.com/NVlabs/cvdp_benchmark)：`8e894cf74414ab1eaea1e2b4e80a02f123df07b6`。
- [CVDP dataset](https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset)：`5b807d945f6a99aa645f7e43a64a2115e281b4bf`。

原始许可证和 NOTICE 随数据、源码保留。四个 CVDP JSONL（592 题）、官方小样例、
许可证、NOTICE 和校验清单纳入 Git；heavy bundle 二进制仍从固定版本的上游下载。
源码 checkout、其他 benchmark 数据、镜像、缓存、运行日志和 `.local/` 本机配置不进入 Git。
克隆仓库后运行 `python3 examples/verilog_design/scripts/fetch.py` 补齐 bundle 和官方工具源码，
下载时验证响应长度及上游 LFS SHA-256；不需要把约 1.3 GB 的 bundle 写进 Git 历史。

## 参考解检查结果

以下是完整原始数据集的环境验证结果，不是模型成绩。完整审计保留全部题目；
默认运行已按下述排除清单屏蔽确定的 golden 失败项。

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
排查方向，尚未通过修改后复测确认为根因。原始题目和参考实现保持不变。

| Benchmark / 题目 | 实际失败现象 | 已确认的问题或排查方向 | 原始日志 |
| --- | --- | --- | --- |
| RTLLM `radix2_div` | 编译成功；8 组向量中 3 组输出不匹配 | 期望/实际分别为 `00f6/faf1`、`000a/fa0f`、`0001/6400`；需要继续区分参考 RTL 的计算问题与测试台握手、采样时序问题 | [simulation.log](runs/rtllm-golden-984003/radix2_div/simulation.log) |
| RTLLM `clkgenerator` | 编译成功；测试台报告 20 次失败 | 测试台每隔 5 个时间单位采样，参考时钟也每隔 5 个时间单位翻转，存在同一仿真时刻的采样竞争风险 | [simulation.log](runs/rtllm-golden-984003/clkgenerator/simulation.log) |
| RTLLM `ring_counter` | 编译成功、退出码为 0；仿真到 `#100` 结束，但没有终态成功标记 | 两个 `posedge` 进程共享计数器 `i`，可能因进程执行顺序错过 `i == 9` 的完成判定；正常退出不足以证明通过 | [simulation.log](runs/rtllm-golden-984003/ring_counter/simulation.log) |
| VerilogEval spec-to-RTL `Prob099_m2014_q6c` | elaboration 失败，报告 `Y2/Y4` 不是模块端口 | 已确认参考 RTL/提示声明 `Y1/Y3`，测试台连接 `Y2/Y4`；同题的 code-completion 数据使用一致的接口并通过 | [compile.log](runs/verilogeval-golden-983997/Prob099_m2014_q6c/compile.log) |

RTLLM 上述三题属于 **Icarus 环境下 golden 未通过**，尚不能据此断言在其他仿真器下
也会失败。VerilogEval 的端口不一致则是本次固定版本中的数据问题。

`exclusions.json` 固定上述 4 个排除项的 benchmark、任务格式、上游版本、golden 状态、
原因和复现命令。Agent 和 `evaluate.py` 默认启用：RTLLM 为 **47 题**，VerilogEval
spec-to-RTL 为 **155 题**，code-completion 仍为 **156 题**。明确选择被排除题会报错；
只有加 `--include-excluded` 才运行完整诊断，并在汇总中记录该开关和排除项。
排除只适用于记录的任务格式，不会屏蔽同名的 code-completion 题目。
创建控制器快照时，需要同时复制 `scripts/`、`compat/` 和 `exclusions.json`，
并按排除后的题数记录预期数量；Agent 只挂载公开题目工作区。

2026-10-23 在最终 Agent SIF 中重验全部参考实现，完整结果仍为 RTLLM 47/50、
VerilogEval spec 155/156、completion 156/156；排除后的参考解分别为
47/47、155/155、156/156。证据在 `runs/eval-repair-985228/golden-*/summary.json`。
历史实验及原始分母不改写；本次完整重测的有效分母汇总另存
`runs/eval-repair-985228/filtered-experiment-summary.json`。

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
缺少公开 golden 本身不构成排除理由；CVDP 592 题仍全部保留。

### CVDP heavy 评测适配修复

2026-10-23 修复真实运行暴露的评测问题：将 Compose `../../external/` 映射到该题
已经应用冻结提交的公开源码树；只忽略官方 JSON 中值为 `null` 的 Python 字节码缓存占位，
其他非文本必需文件仍报错。输入数据文件及测试源码不改写。

Judge 单独只读挂载 `compat/cvdp_sitecustomize.py`，将 cocotb 的 Python bool 参数
序列化为 Verilog 的 `0/1`。`cvdp_agentic_heavy_cv32e40p_1_0001` 的基线包含 Icarus
无法解析的并发 SVA，因此明确切换为 Verilator 并启用 `--assert`；不关闭断言，也不
放宽 Verilator 的警告判定。每题 `evaluation-adaptations.json` 保存实际调整、挂载和环境。
评测准备异常记为 Judge `error`，保留 Agent 的真实退出码、耗时及冻结文件，避免误报
Agent 失败。Agent 的输入和运行环境不加载该 Judge 兼容模块。

使用全量实验 `qwen35-all-20261022-154141` 的原冻结提交重判 9 个受影响的 heavy 题，
8 个暴露功能断言失败，1 个在 Verilator 下触发 `CASEINCOMPLETE` 警告；heavy 通过数仍为
27/75，但原来 7 个错误归入 Agent 的记录已更正为评测后的失败。原始记录保留，
更正结果及源文件哈希证据在 `runs/eval-repair-985228/`。

修复通过真实 SIF 的 bool 参数编译、SVA 正例与故意失败断言反例；官方 CVDP 有解样例
2/2 通过，故意损坏版本 2/2 被拒绝。CVDP 没有公开完整 golden，以上不能证明其全部
题目都可通过，也不会将候选失败自动认定为题目损坏。

## 运行

VerilogEval 的 Docker 构建、ARCA 上传及全量验收见
[Docker / ARCA 指引](containers/verilogeval-arca.md)。下列命令继续使用原有 Apptainer 路径。

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

### CVDP 工作目录修复与历史成绩更正

2026-10-22 扫描了已有运行的 1,551 份评测执行日志，确认 12 题的 lint 步骤因
`/src/rundir/` 不存在而未启动。`/src` 的 harness 挂载会遮住镜像原有目录，
因此仅在 SIF 内创建目录不足以解决；评测入口现在为每个步骤在对应的题目挂载副本中
创建工作目录，并拒绝未映射或越界的目录。缺失依赖、容器挂载等已识别的环境错误
单独记为 `error`，不与 RTL 测试失败混为一类。官方测试源码和判定条件不变。

对这 12 题原冻结 RTL 使用同一 SIF 重判，11 题通过，1 题仍因 Verilator 的位宽及
不完整 case 警告未通过 lint。没有重新生成答案或向 Agent 反馈评测结果。
CVDP 全量运行 `qwen35-cvdp-984265` 的通过数由 85 更正为 96；另有 86 题官方评测
失败、72 题 Agent 超时、4 题评测超时、2 题缺少提交、42 题因模型服务中断失败。
接口中断仍未补测，因此这不是一次完整无故障的模型测评。

原始运行记录保持不变。更正后的结果及逐题重判证据位于
`runs/environment-integrity-audit-984331/corrected-cvdp-summary.json` 和
`runs/environment-integrity-audit-984331/rejudge/`；综合环境与轨迹审计见同目录的
`REPORT.md`。这些运行产物不进入 Git。

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
- Agent 只收到题目描述和官方允许公开的 `input.context` / agentic `context`；RTLLM 仅导出
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
- Agent 退出后只收集指定提交文件；拒绝符号链接、非普通文件和超大文件，独立复制、
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

VerilogEval 另支持 `--task-format code-complete-iccad2023`。CVDP 的完整入口见下一节。
CVDP 抽测通过的是官方测试，未将空 golden 当成通过，也没有使用上述官方有解样例代答。
镜像和这些文件/网络检查不构成独立虚拟机，也不声称防御宿主内核或容器运行时漏洞。

### CVDP 完整题目入口

四个子集使用同一个 CVDP SIF，无需逐题构建镜像：

| `--cvdp-split` | 题数 | Agent 输入与提交 | 判卷 |
| --- | ---: | --- | --- |
| `nonagentic`（默认） | 302 | prompt、公开 context；指定输出文件 | 原始官方 harness |
| `agentic` | 92 | prompt、公开源码；修改指定文件 | 原始官方 harness |
| `heavy` | 75 | prompt、bundle 指定 commit 的完整公开源码树；修改指定文件 | 原始官方 harness |
| `comprehension` | 123 | prompt、公开源码；答案写入 `subjective.txt` | 63 题官方 BLEU-2，60 题官方 LLM rubric |
| `all` | 592 | 合并以上四个子集，ID 不重复 | 按题型选择 |

Agentic/heavy 的 `/code` 与 `/workspace` 挂载同一工作区，兼容官方题目中的绝对路径。
Heavy 按官方规则导出 wrapper 的 `external/`，`*_ext_*` 仓库则直接导出根目录，避免
源码多套一层而导致 `/code/...` 缺依赖。导出不包含 `.git`、其他 commit、refs、remotes 或 bundle；公开源码树中原本存在的
测试属于官方公开上下文，独立的评测 harness、参考答案、评分日志不导出给 Agent。
按官方 `output.context` / `patch` 的路径收集最终文件，支持多文件、Markdown、新增和删除；
这些字段的值不向 Agent 导出。其他路径的修改不提交，自测应放在 `selftest/`。
补丁题同时保存冻结文件、manifest 和相对于公开初始文件的 `changes.patch`，判卷重新准备
原始源码后应用冻结提交；不采信 Agent 提供的 Git 状态或评分报告。

```bash
# 全量运行需按并发和模型速度申请资源；这三个变量可放在 .local/ 启动脚本。
export VERILOG_AGENT_CPUS=32 VERILOG_AGENT_MEMORY=96G VERILOG_AGENT_TIME=12:00:00

bash examples/verilog_design/scripts/run_agent.sh cvdp --cvdp-split agentic --all
bash examples/verilog_design/scripts/run_agent.sh cvdp --cvdp-split heavy --all

# 独立 Judge 使用 Chat Completions 接口；密钥通过环境变量提供。
# API 地址、模型和本机凭据仍应放在 Git 忽略的 .local/ 启动配置中。
bash examples/verilog_design/scripts/run_agent.sh cvdp --cvdp-split comprehension --all \
  --judge-model YOUR_JUDGE_MODEL --judge-base-url http://YOUR_JUDGE_HOST/v1

# 全部 592 题；并发数按所申请的 CPU/内存调整。
bash examples/verilog_design/scripts/run_agent.sh cvdp --cvdp-split all --all \
  --jobs 8 \
  --judge-model YOUR_JUDGE_MODEL --judge-base-url http://YOUR_JUDGE_HOST/v1
```

理解题 cid006/cid008 直接调用固定版本的官方 BLEU/ROUGE 函数，BLEU-2 为正式分数，
ROUGE-2 为附带诊断。cid009/cid010 复用官方 `SubjectiveScoreModel_Instance` 的
评分提示词及解析器，用 `--judge-model` 选择独立评分模型；`--judge-base-url` 可省略以使用
OpenAI 默认地址，`--judge-api-key-env` 默认为 `OPENAI_API_KEY`。
Judge 使用指定地址直连，不继承启动器注入的 SOCKS/HTTP 代理；该地址需能从计算节点访问。
它在 Agent 结束后运行，参考答案和临时 API 凭据只挂载给 Judge，凭据不会写入运行记录。

按官方默认 score 模式保存 0–1 分数，状态为 `scored`，不把连续分数伪装成通过率。
`summary.json.text_scores` 分别汇总 BLEU 和 LLM 的已评分题数、均分。
缺少 LLM Judge 配置/凭据时为 `unscored`；API、解析或进程失败分别记 `error` / `timeout`，
不会退回 BLEU 或默认为通过。理解题退出成功表示完成评分，零分也属于已评分；有未评分、
评测错误或生成题未通过时整体退出非零。

已有冻结提交可直接重新判卷，无需再次调用 Agent：

```bash
python examples/verilog_design/scripts/evaluate.py --bench cvdp --cvdp-split heavy \
  --candidate-root /path/to/previous-agent-run --output /path/to/new-judge-output --timeout 600
```

本次最终验证记录在 `runs/cvdp-coverage-984625/final/`：592/592 公开工作区导出及
非提交 HDL 依赖路径检查、469 道生成题的
520 个服务配置解析成功；新增三个子集各 3 题，9/9 通过真实 SIF 隔离/EDA 预检。
官方有解样例 2/2 通过、损坏版本 2/2 被拒绝；3 道 agentic、3 道 heavy 的损坏提交也均被
官方 harness 拒绝。63 道客观理解题经真实提交/私有评分路径，用参考答案作为环境正例
均得 1，用无关答案作负例均得 0；另外 60 道在未配置 Judge 时均正确记录为 `unscored`。
LLM 评分链路用本地模拟 API 验证了官方 rubric、正常解析和错误处理，未作为模型成绩。
这验证了数据与运行路径；469 道生成题没有公开完整 golden，仍不能声称全量题目已答对。

### 自托管 Responses API 与全量并发测试

`agent.py --all` 选择对应 benchmark 的全部基础任务；`--jobs 32` 在该 benchmark 内
启动最多 32 个独立 Agent。三个 benchmark 应按顺序逐个执行。`--all` 默认对应 RTLLM
47 题、VerilogEval spec 155 题或 completion 156 题，以及 CVDP 默认子集 302 题；CVDP 加
`--cvdp-split all` 为 592 题。

自托管 Qwen/SGLang 使用 `scripts/sglang_proxy.py` 作为只允许模型接口的转发入口。
实际服务地址通过 `--base-url` 传入；本机启动配置仍放在被忽略的 `.local/` 中。
该入口显式固定 top-p 0.95、temperature 0.6、top-k 20、输出上限 81920，并设置
`chat_template_kwargs.enable_thinking=true`，逐请求保存实际参数和响应审计记录。
它拒绝网页搜索/fetch 工具及非模型路由，不通过放开 Agent 外网来连接自托管服务。

Agent 入口增加 `--model-base-url`、`--context-window`、`--auto-compact-limit` 和
`--image-tag`。容器内模型地址使用转发入口 `http://127.0.0.1:1081/v1`，由外部转发连接
实际服务。自托管服务不要求复制 OpenAI 登录凭据；可以使用新的空 CODEX_HOME，仍为
每题创建独立视图。CODEX_HOME 路径应位于仓库外，以免与宿主仓库隐藏路径预检冲突。

Qwen3.5 的部署模板以 `enable_thinking` 控制思考开关，没有更细的 effort/budget 档位。
Codex 可传 `--effort max`，但不能仅凭这个标签认定模型采用了更高思考等级；应检查实际
模板、请求与思考 token。本地验证关闭时为 0，开启时正常产生思考 token；未设独立思考
预算。`--context-window 262144 --auto-compact-limit 172032` 为长输出保留空间。
单次输出上限包含思考和答案，仍可能出现只思考到达上限而没有提交的失败。

修改容器入口后用新的镜像标签保留旧实验：构建时指定
`VERILOG_AGENT_IMAGE_TAG=codex-qwen35-v2`，运行时指定相同 `--image-tag`。
服务端实际并发能力需单独核实；32 个 Agent 不等于服务端同时解码 32 个请求。

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

# 在有 Apptainer 的 CPU allocation 中，另测真实 SIF 的客观评分和 LLM API 链路。
PYTHONPATH=examples/verilog_design/cache/runner-deps \
  VERILOG_TEST_SIF=/absolute/path/to/cvdp-v1.1.0-oss-codex.sif \
  python examples/verilog_design/scripts/test_agent.py CVDPScoringTests -v
```

2026-10-23 评测修复后，21 项边界测试及 3 项真实 SIF 集成测试通过；后者覆盖 bool 参数、
SVA 正例、故意失败断言反例和原有理解题评分链路。Linux 完整 `make check` 再次通过
（UI 构建及 2 项测试、7 项 pytest、全量 Ruff、62 文件 Mypy），记录在
`runs/eval-repair-985228/`；未在 macOS 运行。

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

从仓库根目录运行 `make check`，覆盖 UI 构建/测试、仓库单元测试、Ruff 和 Mypy。
本例的边界及真实 SIF 集成检查使用上文的 `test_agent.py` 命令；Docker/ARCA 验收使用
独立的 [golden 流程](containers/verilogeval-arca.md)。

当前通过记录见 `runs/eval-repair-985228/`。此前检查记录仍保留在本机 `logs/` 和
`runs/`，不覆盖历史报告。检查使用本例独立缓存环境，不重建仓库共享 `.venv`；
本机 CODEX_HOME、凭据、构建缓存及运行产物不进入 Git。
