# DeepSeek V4 在 Terminal-Bench 2.1 上的分类表现报告

## 1. 结论摘要

本报告分析 EnvArena job `dsv4-terminus-max-v110-tb21-full-fixed-2609231359-2609230559` 的当前最终结果。该 job 使用 `deepseek-v4-flash-0731-terminus-max@1.0.2`、`terminus-2-tb21@1.0.10` 和 `tb21-terminus2-full@2.1.1`，共包含 89 个任务、每个任务 1 个 sample。

- 总体通过：**52 / 89**
- 总体通过率：**58.43%**（页面聚合分数显示为取整后的 58）
- 任务覆盖：论文 Appendix H 中的 89 个任务全部匹配，**89 / 89 无缺失**
- 按样本量不少于 3 的类别比较，表现最好的是 **Debugging（80.00%）**，其次是 **System Administration（77.78%）**
- 主要短板是 **File Operations（20.00%）**、**Scientific Computing（37.50%）** 和 **Software Engineering（46.15%）**
- 37 个未通过任务中，Software Engineering 占 14 个；Software Engineering、Scientific Computing 和 File Operations 合计占 **23 / 37（62.16%）**

需要注意：这是 job 的**第 2 代最终结果**。执行历史显示，上一代归档分数为 0.52，之后曾手动重试 10 / 89 个失败任务。因此，58.43% 应理解为当前 job 的最终成绩，不能直接当作无重试的 first-pass 成绩。

## 2. 分类与统计口径

论文在 Figure 4 中给出 16 个高层任务类别，并说明类别由任务作者标注；Appendix H 又给出了每个 Task ID 对应的 Category。本报告直接使用 Appendix H 的逐任务标签，不进行主观重分类。

这里使用的是论文的**任务领域分类**，不是论文中的失败模式分类。论文的 Execution、Coherence、Verification 等失败模式需要检查 agent 轨迹和命令级错误，仅凭任务最终分数无法可靠标注。

统计规则：job 中每个任务只有 1 个 sample；任务 Aggregate 为 1.00 记为通过，为 0.00 记为未通过。类别通过率为该类别通过任务数除以该类别任务总数。

## 3. 各类别通过率

| 类别（论文原名） | 通过 | 总数 | 通过率 | 未通过数 |
|---|---:|---:|---:|---:|
| Software Engineering | 12 | 26 | 46.15% | 14 |
| System Administration | 7 | 9 | 77.78% | 2 |
| Data Science | 5 | 8 | 62.50% | 3 |
| Scientific Computing | 3 | 8 | 37.50% | 5 |
| Security | 6 | 8 | 75.00% | 2 |
| File Operations | 1 | 5 | 20.00% | 4 |
| Debugging | 4 | 5 | 80.00% | 1 |
| Data Processing | 3 | 4 | 75.00% | 1 |
| Model Training | 3 | 4 | 75.00% | 1 |
| Mathematics | 3 | 4 | 75.00% | 1 |
| Machine Learning | 2 | 3 | 66.67% | 1 |
| Games | 0 | 1 | 0.00% | 1 |
| Video Processing | 0 | 1 | 0.00% | 1 |
| Data Querying | 1 | 1 | 100.00% | 0 |
| Optimization | 1 | 1 | 100.00% | 0 |
| Personal Assistant | 1 | 1 | 100.00% | 0 |
| **合计** | **52** | **89** | **58.43%** | **37** |

样本量为 1 的类别出现 0% 或 100% 并不说明模型在该领域具有稳定的极端表现，不能与大类别直接等量比较。

## 4. 各类别任务明细

### Software Engineering（12 / 26，46.15%）

**通过（12）**：`build-pmars`、`cobol-modernization`、`code-from-image`、`fix-git`、`fix-ocaml-gc`、`git-leak-recovery`、`headless-terminal`、`kv-store-grpc`、`polyglot-c-py`、`prove-plus-comm`、`pypi-server`、`winning-avg-corewars`

**未通过（14）**：`build-pov-ray`、`cancel-async-tasks`、`circuit-fibsqrt`、`gpt2-codegolf`、`make-doom-for-mips`、`make-mips-interpreter`、`path-tracing-reverse`、`path-tracing`、`polyglot-rust-c`、`regex-chess`、`schemelike-metacircular-eval`、`torch-pipeline-parallelism`、`torch-tensor-parallelism`、`write-compressor`

### System Administration（7 / 9，77.78%）

**通过（7）**：`compile-compcert`、`configure-git-webserver`、`mailman`、`nginx-request-logging`、`qemu-alpine-ssh`、`qemu-startup`、`sqlite-with-gcov`

**未通过（2）**：`git-multibranch`、`install-windows-3.11`

### Data Science（5 / 8，62.50%）

**通过（5）**：`hf-model-inference`、`mcmc-sampling-stan`、`mteb-retrieve`、`reshard-c4-data`、`rstan-to-pystan`

**未通过（3）**：`mteb-leaderboard`、`query-optimize`、`sam-cell-seg`

### Scientific Computing（3 / 8，37.50%）

**通过（3）**：`adaptive-rejection-sampler`、`bn-fit-modify`、`modernize-scientific-stack`

**未通过（5）**：`dna-assembly`、`dna-insert`、`protein-assembly`、`raman-fitting`、`tune-mjcf`

### Security（6 / 8，75.00%）

**通过（6）**：`break-filter-js-from-html`、`crack-7z-hash`、`fix-code-vulnerability`、`openssl-selfsigned-cert`、`password-recovery`、`vulnerable-secret`

**未通过（2）**：`filter-js-from-html`、`sanitize-git-repo`

### File Operations（1 / 5，20.00%）

**通过（1）**：`large-scale-text-editing`

**未通过（4）**：`db-wal-recovery`、`extract-elf`、`extract-moves-from-video`、`gcode-to-text`

### Debugging（4 / 5，80.00%）

**通过（4）**：`build-cython-ext`、`custom-memory-heap-crash`、`merge-diff-arc-agi-task`、`sqlite-db-truncate`

**未通过（1）**：`overfull-hbox`

### Data Processing（3 / 4，75.00%）

**通过（3）**：`log-summary-date-ranges`、`multi-source-data-merger`、`regex-log`

**未通过（1）**：`financial-document-processor`

### Model Training（3 / 4，75.00%）

**通过（3）**：`count-dataset-tokens`、`pytorch-model-cli`、`pytorch-model-recovery`

**未通过（1）**：`train-fasttext`

### Mathematics（3 / 4，75.00%）

**通过（3）**：`feal-differential-cryptanalysis`、`feal-linear-cryptanalysis`、`largest-eigenval`

**未通过（1）**：`model-extraction-relu-logits`

### Machine Learning（2 / 3，66.67%）

**通过（2）**：`caffe-cifar-10`、`distribution-search`

**未通过（1）**：`llm-inference-batching-scheduler`

### Games（0 / 1，0.00%）

**通过（0）**：无

**未通过（1）**：`chess-best-move`

### Video Processing（0 / 1，0.00%）

**通过（0）**：无

**未通过（1）**：`video-processing`

### Data Querying（1 / 1，100.00%）

**通过（1）**：`sparql-university`

**未通过（0）**：无

### Optimization（1 / 1，100.00%）

**通过（1）**：`portfolio-optimization`

**未通过（0）**：无

### Personal Assistant（1 / 1，100.00%）

**通过（1）**：`constraints-scheduling`

**未通过（0）**：无

## 5. 结果解读

1. **系统管理、调试和安全任务较强。** 在样本量至少为 5 的类别中，Debugging、System Administration 和 Security 的通过率分别为 80.00%、77.78% 和 75.00%。这说明当前 agent/model 组合在构建修复、服务配置、虚拟机/系统工具操作和部分安全分析任务上相对稳定。

2. **软件工程是最大类别，也是失败任务的最大来源。** 该类别占全部任务的 29.21%，通过率仅 46.15%，并贡献了 14 个失败任务。失败项集中在复杂构建、底层模拟/解释器、受约束程序合成、逆向实现和分布式 PyTorch 等长链任务。

3. **科学计算和文件操作是更明显的薄弱点。** Scientific Computing 只有 3 / 8 通过；File Operations 只有 1 / 5 通过。两类失败项常涉及损坏或二进制文件恢复、媒体/机器码提取、领域算法实现及带严格数值或性能约束的验证。

4. **小类别不宜过度解读。** Data Querying、Optimization、Personal Assistant 各只有 1 个任务；其 100% 只表示当前具体任务通过。Games 和 Video Processing 同理，各 1 个任务未通过。

5. **当前结果包含重试影响。** 页面只给出了上一代整体归档分数和重跑数量，没有直接给出“哪些任务因重试而翻转”。因此本报告可以准确描述当前最终代的分类表现，但不能据此还原各类别的纯 first-pass 通过率。

## 6. 数据来源与可复现性

- Job 页面：<https://envarena-prod.alipay.com/jobs/dsv4-terminus-max-v110-tb21-full-fixed-2609231359-2609230559>
- 分类来源：`terminal-benchmark-2.pdf`，Figure 4（类别规模）与 Appendix H（逐任务 Category），本地路径 `/Users/jjsong/Downloads/terminal-benchmark-2.pdf`
- 抽取时间：2026-09-28（Asia/Shanghai）
- 交叉校验：分类计数为 26、9、8、8、8、5、5、4、4、4、3、1、1、1、1、1，与论文 Figure 4 完全一致；任务通过数合计 52，未通过数合计 37，与 job 页面 `52 / 89 passed` 一致。
