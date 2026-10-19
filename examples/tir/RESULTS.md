# 原生 ToRL 真实 RSI 实验结果

2026-10-17，以 Qwen2.5-Math-1.5B 为学生模型、AReaL 为后端完成四轮真实 RSI 训练。
研究模型为用户提供的 gpt-6.1-sol。本次固定 harness，研发数据合成、训练配方与课程。

| 独立最终指标 | base | r4 | 差值 | 按题目聚类的配对 95% CI |
| --- | ---: | ---: | ---: | ---: |
| 原生 MathVerify | 78/374，20.86% | 105/374，28.07% | +7.22pp | [+2.64，+12.05]pp |
| 冻结 canonical boxed 诊断 | 92/374，24.60% | 116/374，31.02% | +6.42pp | [+1.59，+11.31]pp |

独立 test 集对应 ToRL 官方 test：194道独立题、374个官方采样行，包含 MATH500 **134题子集**
和 AIME24/25 各30题、每题四次。分数是逐行准确率；重复采样按问题聚类，不作为独立题。
原生提升主要来自 AIME，MATH500 子集为39.55%→41.79%。选择在 test 前冻结，没有据此调参。

| 正式轮次 | 权重 parent | 策略 | 更新 | 原生 valid /256 | canonical valid /256 |
| --- | --- | --- | ---: | ---: | ---: |
| r1 | base | 1,024 官方题，0 合成题 | 32 | 78 | 80 |
| r2 | r1 | 768 官方 +512 合成，768 token训练段 | 64 | 69 | 71 |
| r3 | r1 | 1,024 官方 +256 合成，依据实际奖励方差调整配比 | 48 | 79 | 78 |
| r4 | r1 | r3 rollout筛出的263官方题，0合成题 | 96 | 83 | 80 |

base valid 为72/256。宿主另做过4步smoke，不计正式轮次。选定权重谱系是 **base→r1→r4，
128次更新**；课程数据证据来自 r3。r2退化和r3弱收益均保留，不能将最终提升归因于增加
合成题。r4使用同一生成组内同时观察到reward 0/1的题：192个完整mixed组、71个部分mixed组。
课程提取接口、严格校验与复现用法见 [RESEARCH.md](RESEARCH.md)。

`synthesis.py` 提供15类可验算合成题、具名数据配方、答案表示策略和真实rollout课程选择；
历史版本的 `experiment.py` 保存冻结协议、源码归档、父checkpoint、实际更新、dev选择与独立valid。
执行、评测轨迹和训练校验复用公共infra。`/rsi`继续作为研究入口；其他任务可以只迭代harness。

所用原生评分器存在bare LaTeX gold解析问题，主评分保持冻结。二级诊断在valid前冻结，
复用原生成轨迹、要求最后一个完整boxed答案，不与主评分互为无损变换。原生超时后的线程
清理等待由独立进程隔离；r4隔离前后逐题结果完全一致，基模型记录1次原生5秒超时。
valid完整boxed率86.90%→74.87%，这项退化也保留。结果限于单训练种子、此模型与固定TIR设置。

实验交付时完整make check：Python 81 passed、1项macOS环境skip、5 subtests；UI 3 passed；Ruff、
Mypy通过。文档/新实验模板更新后的针对性回归39 passed。新首轮模板使用math_delimited，
历史r1使用preserve；逐字重放需读取原实验配方。

历史归档沿用实验时的文件与字段名；当前源码使用 train / valid / test，归档不重写。

以下使用 `$TIR_RESULTS_ROOT` 表示本地实验归档目录；查看产物时替换为实际绝对路径。

- 历史报告及证据目录：`$TIR_RESULTS_ROOT/`。
- 结果图：`$TIR_RESULTS_ROOT/metrics-v2.png`。
- 最终checkpoint：`$TIR_RESULTS_ROOT/execution/study-torl/jobs/r4-train/areal/checkpoints/root/rsi-tir/study-torl-r4-train/default/epoch5epochstep15globalstep95`。
- 真实会话 `run-fb43e606bfbe` 的原始事件、阶段迁移和17条宿主复核反馈位于 `rsi/`。
- 取消的r5不计完成轮次；其曾被删除的部分数据按原SHA恢复，未恢复的元数据如实列明，
  见 `recovery/r5/recovery.json`。原生报告更正另存 `research-report-corrections.md`，原稿保留。
