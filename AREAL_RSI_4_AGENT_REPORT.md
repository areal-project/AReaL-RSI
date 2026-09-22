# AReaL-RSI：4 Research Agent Harness优化实验报告

## 实验目标

- 现状：GPT-5.6-luna + Terminus Harness在历史TB2.1全量评测中通过60/89题（67.4%）。
- 预期：以30个有效、不同的候选Harness为优化预算，由4个Research Agent并行研究，对比score、time、cost；目标通过70/89题（78.6%）。目标不是实际结果。
- 评测方式：候选先在小验证集上评测，按预定规则选出Top3，再分别完成一次全量89题评测。同时对原Harness进行一次独立全量复验，不补跑失败题。

本次最高观测通过数为65/89（73.0%），来自搜索排名第三的r21，未达到70题目标。在四组共同有效的75题上，r21与原Harness均通过59题，尚无明确优于原Harness的证据。

## 结果

Top1/Top2/Top3指小验证集上的搜索排名，保留该命名，不按最终全量成绩重排。

| 指标 | 历史Baseline | 本次Baseline复验 | Top1：r8（D） | Top2：r22（A） | Top3：r21（C） |
|---|---:|---:|---:|---:|---:|
| 全量观测通过比例 | 0.674 | 0.708 | 0.708 | 0.685 | 0.730 |
| 已观测通过题数 | 60/89 | 63/89 | 63/89 | 61/89 | 65/89 |
| 相对历史Baseline | — | +3题 | +3题 | +1题 | +5题 |
| 相对本次Baseline复验 | — | — | 0题 | -2题 | +2题 |
| 有效完成题数 | 89 | 82 | 86 | 86 | 84 |
| HARNESS_FAILED | 0 | 3 | 3 | 3 | 5 |
| TIMEOUT | 0 | 4 | 0 | 0 | 0 |

“观测通过比例”统一用通过数除以89，方便记录全量结果，但不能理解为其余题都是有效0分。HARNESS_FAILED和TIMEOUT均单列为unavailable。历史Baseline与本次独立复验不是同一个Job，不能把历史60题直接当作严格同期对照。

### 共同有效题与holdout

| 比较口径 | 本次Baseline | Top1：r8 | Top2：r22 | Top3：r21 |
|---|---:|---:|---:|---:|
| 四组共同有效75题 | 59/75 | 55/75 | 57/75 | 59/75 |
| 预声明holdout75：通过/有效题 | 54/69 | 56/72 | 53/72 | 56/71 |
| holdout75内四组共同有效63题 | 51/63 | 49/63 | 49/63 | 51/63 |

这里的两个“75”不是同一集合：共同有效75题取四组有效结果交集；预声明holdout75则从89题中排除原先已暴露的14题，即2道TRAIN、10道原挑战题和2道控制题。后来搜索减少两题，不会把这两题重新算作未暴露的holdout。

与本次Baseline分别作配对比较：r8在共同有效80题上58比62，改善3题、退步7题；r22在79题上58比61，改善6题、退步9题；r21在78题上61比61，改善5题、退步5题。

### Time与Cost

| 指标 | 本次Baseline | Top1：r8 | Top2：r22 | Top3：r21 |
|---|---:|---:|---:|---:|
| 全量作业墙钟耗时 | 246.8分钟 | 251.6分钟 | 243.2分钟 | 244.8分钟 |
| 已报告agent_ms累计 | 38.25小时 | 44.62小时 | 45.19小时 | 46.64小时 |
| agent_ms覆盖任务数 | 86/89 | 86/89 | 86/89 | 84/89 |
| 评测推理Token / Cached Token | unavailable | unavailable | unavailable | unavailable |
| 评测费用 | unavailable | unavailable | unavailable | unavailable |

墙钟耗时按Job创建时间到最后一题更新时间计算，含运行、等待及评测环节；任务并行执行，因此累计Agent时间不等于墙钟时间。各组计时缺失不同，不能据此直接宣称效率提升。研究员自身的原始token事件另行归档，不能混作被测Harness的推理成本，也不能把可能累计的计数直接相加为账单。

## AReaL-RSI优化过程

本次由4个Research Agent保留各自会话和研究分支，累计完成30个有效、不同的候选：A提交7个、B提交8个、C提交8个、D提交7个。另有3次已归档搜索评测出现HARNESS_FAILED，未计入30轮；这表示执行无效，不足以证明对应prompt本身有缺陷。

研究员可读取2道TRAIN题的轨迹：build-pmars、cancel-async-tasks；VALIDATE仅反馈聚合成绩。搜索最初使用10道挑战题和2道控制题，后按用户要求排除path-tracing和make-doom-for-mips，形成8道挑战题加2道控制题，每题一次。已有结果从归档投影到统一10题口径，历史原始记录保留，不重跑Baseline。

8道挑战题：chess-best-move、extract-elf、financial-document-processor、model-extraction-relu-logits、nginx-request-logging、query-optimize、sqlite-with-gcov、tune-mjcf。2道控制题：log-summary-date-ranges、db-wal-recovery。导入的搜索Baseline为挑战0/8、控制2/2。

候选至少比Baseline多通过2道挑战题且控制题不退步才合格；随后依次按挑战成绩、控制成绩、较少执行故障、较小改动、较早提交排序。前三名均挑战7/8、控制2/2，按改动大小排为r8、r22、r21。

- 修改范围：30个有效候选的发布Harness规格之间，仅system_prompt不同；不涉及模型权重训练。
- 最终全量：Top3和原Harness各89题、每题一个样本，包含path-tracing及Doom；实际任务attempt均为1，无任务级补跑。任务级无重试不代表Harness内部模型请求无重试。
- 固定配置：模型gpt-5-6-luna-4ipic@1.0.0；Benchmark tb21-terminus2-full@2.1.1；Reward tb2-reward@4.0.16；原Harness terminus-2-tb21@1.0.10。

> 本文r表示有效候选账本的全局编号。r22不是在r21上继续修改，三个入选版本来自不同研究分支；不能将其解释为同一条线性版本链。

## Top1：r8版本（Research Agent D）

r8的最终prompt围绕“完成必须有直接证据”，主要包含以下要求：

1. **先明确交付物、约束和验收条件。**

> Before acting, identify the requested deliverable, constraints, and explicit acceptance checks.

让执行Agent在动手前明确要交付什么、必须满足什么，以及如何检查。

2. **不把写文件、编译成功或启动命令当作任务完成。**

> for every acceptance criterion, run the smallest direct verification available, including a functional smoke test when applicable, and inspect its output.

每项验收要求都要有直接验证；检查失败时继续诊断、局部修复并重新检查。

3. **遇到慢任务或资源阻塞，保留成果后切换可行方案。**

> Preserve completed work, identify an equivalent lower-cost or already-available route that still satisfies the stated constraints

采用有界等待和进度检查，避免无休止重复停滞操作。替代方案仍须符合任务约束，并记录切换原因。

r8搜索成绩为7/8挑战、2/2控制；全量观测通过63题，另3题执行不可用。

## Top2：r22版本（Research Agent A）

r22更强调将任务分阶段执行，并明确管理验收与恢复：

1. **安装、下载或重建前先检查环境和依赖。**

> identify available tools, versions, existing artifacts, and required capabilities; reuse suitable local inputs and installed components

优先复用本地工具和产物，减少不必要的网络安装与完整重建。

2. **每个阶段检查通过后，再进入下一阶段。**

> Structure the plan as explicit stage gates: separate prerequisite acquisition/setup, core implementation or modification, and verification/finalization

把前置准备、核心实现、验证收尾分开；若前一步失败，先恢复，避免在错误状态上继续堆叠命令。

3. **维护需求与证据清单。**

> Maintain an explicit requirements ledger

逐项记录交付物、限制和验收条件，并为每项要求提供观察到的证据，防止只完成显眼的部分。

4. **从实际状态恢复，明确区分失败类型。**

> Make execution recoverable and idempotent

失败或中断后先检查已发生的变更和运行进程，再决定恢复或回滚；区分命令失败、超时、环境能力缺失和验收不通过。

r22搜索成绩为7/8挑战、2/2控制；全量观测通过61题，另3题执行不可用。

## Top3：r21版本（Research Agent C）

r21的最终prompt更详细地约束了命令交互和独立验收：

1. **明确JSON响应及命令格式。**

> Return only one valid JSON object

规定analysis、plan、commands、task_complete四个输出字段、命令输入与等待时长格式，并默认不标记完成。这是prompt中的明确要求，不意味着修改了底层runner实现。

2. **缩小命令批次，逐步核对实际状态。**

> Use one logical operation or checkpoint per command batch

避免把有依赖关系的操作塞入长命令链。每步检查输出和退出状态，遇到前提不成立时先重新规划。

3. **长耗时操作前检查条件，执行中保留检查点。**

> Before any long, costly, network-dependent, or hard-to-reverse operation, apply a preflight-and-checkpoint protocol

检查工具、输入、目标路径、磁盘空间和连通性；设定时间或输出边界，停滞后保留检查点并采用可行替代路径。

4. **异步命令通过短轮询确认结束。**

> use short wait-and-poll cycles, inspect whether the process finished and whether the shell prompt returned

命令等待时长结束不等于进程结束；确认实际状态后，才能执行依赖它的下一步。

5. **完成前从原始需求重新独立验收。**

> Before setting task_complete true, perform an independent final audit from the original user request

重新核对交付物、精确路径、依赖、输出格式及代表性功能测试；适用时检查边界、错误和取消行为，避免只做语法或编译检查。

r21搜索成绩为7/8挑战、2/2控制；全量观测通过65题，另5题执行不可用，是三个候选中观测通过数最高的版本。

## 实验结论

小验证集上的排序未保持到全量评测：搜索第三名r21的全量观测通过数高于第一名r8和第二名r22。但r21在共同有效题及共同有效holdout题上均与本次原Harness持平，不能把相对历史Baseline增加5题直接解释为稳定的Harness提升。

本次4 Research Agent实验未达到70题目标，也没有单Research Agent的同条件对照，因此不能据此判断多Agent优于单Agent。仅一次全量样本、各组不同的执行异常以及缺失的费用数据，也限制了对稳定性和性价比的判断。

## Arena与审计

- [Top1：r8](https://envarena-prod.alipay.com/jobs/luna-tb21-official-final-841ed275cb4f5512-2609201824)
- [Top2：r22](https://envarena-prod.alipay.com/jobs/luna-tb21-official-final-top2-442963666194e06e-2609210258)
- [Top3：r21](https://envarena-prod.alipay.com/jobs/luna-tb21-official-final-top3-b9a4698ec59e7da5-2609210258)
- [本次原Harness独立复验](https://envarena-prod.alipay.com/jobs/luna-tb21-official-final-841ed275cb4f5512-baseline-2609202243)
- [历史Baseline](https://envarena-prod.alipay.com/jobs/gpt56luna-4ipic-terminus2-tb21-full-2609181411-2609180612)

本地审计目录：`.rsi/luna-tb21-30r/areal-multi/validation-redesign-20260920/original-baseline-v3/evaluation-records/final-comparison/`。其中summary.json保存四组汇总，per-task.json保存逐题结果，candidate-ledger.json保存30个候选及父digest版本链，attempt-audit.json保存搜索尝试，research-runtime-usage.json保存研究过程原始token事件。
