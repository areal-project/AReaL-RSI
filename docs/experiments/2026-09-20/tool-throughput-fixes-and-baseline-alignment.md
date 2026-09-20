# 工具延迟修复，以及 goal baseline 的评测对齐检查

2026-09-20。比较 `0920-exp-2` 的 Astra L2P71 纯 pass@4，与
`codex-full-gpt6astra-rsi8-hour1-20260916T030000/L2P71`。
未启动模型或 GPU 实验；性能检查使用已结束研究的只读数据。

## 本次修复

1. `PassKActions.state` 一次读取验证集合、比较契约和撤回集合，一次计算各人的最佳结果。
   leader 不再递归构建四份完整 researcher 状态；不再为每条验证重复读取整个 run 和撤回集合。
   复用仅限本次读取，不跨请求缓存；新结果、撤回和恢复后的数据仍立即生效。
2. pass@k 各模式的作业工具等待上限从 10 秒降到 1 秒。作业继续在后台执行，返回原 job ID，
   不结束原生 turn、不重新提交，也不把普通工具等待改成可扣除时间。
3. KernelBench client 的正常轮询间隔从 5 秒降到 1 秒，连接失败仍等待 5 秒再重试。
4. JSON 作业反馈以 `feedback.data` 返回，避免嵌套 JSON 字符串和截断的 JSON。
   非 JSON 文本保留 `feedback.content`，完整材料引用仍可读取。
   KernelBench 的字段选择在 example 内完成：默认保留正确性、成绩、耗时、波动、种子和错误摘要；
   完整回执仍在 `remote-job.json`，诊断日志仍单独保留。正式验证仍读取可信回执，摘要不参与计分。

这里没有改 collaboration 版本的 leader/researcher prompt，也没有改变评测精度、候选选择规则、
研究隔离或计时口径。v0.2.1.1 的问题分享限制仍是独立的可选模块。

## 修复的实际收益与边界

同一份历史数据、同一进程，分别加载上一部署快照和当前实现；每项普通耗时取两次的中位数。
另独立统计 `Path.read_text` 次数，避免把计数 instrumentation 混进耗时。

| 状态读取 | 修复前 | 修复后 | 文件读取次数，前 → 后 |
|---|---:|---:|---:|
| 纯 pass@4 L2P71 leader | 1.519 s | 0.073 s | 8,753 → 541 |
| 协作 L2P71 leader | 2.080 s | 0.067 s | 22,254 → 551 |
| 协作 L1P63 leader | 1.079 s | 0.060 s | 9,585 → 452 |
| 纯 pass@4 L2P71 R4 | 0.316 s | 0.067 s | 2,278 → 506 |
| 协作 L2P71 R4 | 0.457 s | 0.055 s | 4,925 → 516 |
| 协作 L1P63 R4 | 0.219 s | 0.051 s | 1,898 → 417 |

三组 leader 的全部 researcher 最佳结果和续跑状态一致；三名 R4 的最佳结果一致。
254 份历史 GPU 回执的反馈摘要平均从 3,951 字符降到 780 字符；正确性、分数、耗时、波动和种子
逐项保持一致，原有错误仍有摘要和完整回执入口。这是反馈正文大小，不是整个模型输入 token 数。

针对 pass@k、协作模块、恢复、验证与访问隔离的回归测试通过；新增的断言覆盖撤回后新结果立即可见、
相同源码的不同 researcher 不互相撤回，以及 JSON 错误反馈的结构和访问权限。Ruff 和核心修改的 mypy 通过。

这些数据证明重复读取已消除，不能直接换算成“每人必然多获得多少分钟推理”或保证成绩提升。
新的实际工具响应延迟、每人输出量和研究分数仍需下一轮实验验证。

## 评测方式是否完全一致

**正式评测的核心配置一致，但整个实验不是完全同一套输入和流程。**

| 项目 | Goal baseline | 当前 RSI |
|---|---|---|
| L2P71 reference.py | 与 RSI 逐字节一致 | SHA256 同为 `412f9c4244239814ab3cc6d58c0837c7ffbc94e5d63453c88f8388fb28309fad` |
| 正式精度 | strict-fp32-v1；FP32，TF32 关闭，matmul highest | 相同 |
| 正式检查与计时 | 5 次随机正确性检查，100 次 CUDA-event 计时 | 相同 |
| 种子 | 探索 42，最终 137 | 相同 |
| 硬件与主要依赖 | L20X、SM90、PyTorch 2.9.1+cu129、Triton 3.5.1 | 相同型号和版本 |
| 实际节点 | slurmd-62 | 回执包含 slurmd-0 和 slurmd-62 |
| 探索分数 | 每次实测 reference / candidate | 启动前校准的固定 reference / candidate |
| 最终评分 | 冻结最佳源码，seed 137 独立复测，当次 reference / candidate | 相同原则，排除已撤回候选 |
| 时间预算 | 1 小时 active time，扣除 API 故障和 GPU 排队 | 每名 researcher 1 小时，同样扣除口径 |
| 运行控制 | 原生 goal 与本地 kb/gpuq 命令 | RSI 监督与作业工具桥接 |

核对了 baseline 的 60 份通过探索回执、RSI 的 202 份通过探索回执，以及远端两次作业保留的
`evaluation-spec.json`。远端规格都是 level 2 / problem 71 / mode eval / seed 42，候选和输出路径不同。
当前 worker 固定 5/100 次，当前 KernelBench FP32 `allclose` 的 atol、rtol 都是 1e-4。
历史回执支持精度和试验次数一致；没有两次运行各自不可变的 evaluator 源码快照，不能进一步声称历史
worker 的每一个字节都已核验一致。

固定 reference 是此前明确要求的稳定计分方式，不能为了对齐而撤销。对这份 baseline 回放，按
实测 speedup 选优与按最短 candidate runtime 选优，均选中同一份 0.277 ms 源码。因此它不是这次
baseline 获胜的直接原因。跨节点性能仍有噪声，宜同时比较 candidate runtime，而非只比较 speedup。

## 题目 query 是否完全一致

不一致。对比的是实际运行冻结的 `system-prompt.txt` 与 RSI 本轮冻结的 task、rules、researcher、
execution 和 tools prompt，不是仓库里已经过期的十次提交默认模板。

共同要求包括：完成整题 ModelNew，保持 constructor/forward 语义；自定义 CUDA/Triton；禁止计算回退、
缓存答案、改变精度开关或计时；允许固定形状优化；禁止自动调参和批量扫描；持续探索到时限。
baseline 也明确要求支持有效输入，不能只针对预想的随机值，因此不能把它理解为“只过五个随机样本即可”。

主要差别：

- RSI 在角色 prompt、题目和 RULES 中都要求开头主动联网调研；baseline 没有这一强制步骤。
- RSI 有报告、保存/撤回候选、作业工具等额外工作流；baseline 使用原生 goal 和较短的 CLI 反馈。
  当前 RSI 明确要求发现错误时撤回受影响历史候选；baseline 控制器主要从正式通过的历史提交选优。
- RSI 诊断入口统一关闭 TF32；baseline 诊断脚本自行配置。这份 L2P71 baseline 的数值诊断实际也
  关闭了两种 TF32，因此没有证据将本例差距归因于它使用了宽松的 TF32 reference。
- 题目路径、说明语言和角色身份不同。baseline 开头称 H200-class，实际回执为 L20X；
  没有观察到两边 GPU 型号不同，不能仅凭这句描述认定设备不一致。

这些差异可能改变探索策略和开销，但现有轨迹无法单独估计 prompt 因素造成了多少分差。

## 一个具体的额外诊断差异

Baseline 的数值脚本对 signed、large、small 和 changed-parameters 输入，打印 `max_abs`、相对 L2
误差与 finite，没有做逐元素 `allclose` 判定。large 输入反馈是 max_abs 6.3125、relative L2 约 4.15e-6。
这只能说明它的整体相对误差较小，不能证明每个元素都满足正式容差。

RSI R3 的 `diagnostic_numerics.py` 同时检查逐元素 1e-5 和 1e-4；早期另一些脚本只检查 1e-5，
比正式 FP32 的 1e-4 还严。它的研究记录明确写着 BF16 三乘积通过公开随机测试、但未通过大输入诊断，
随后撤回；后续增加数值保护，最终方案约 0.447 ms。baseline 保留无范围保护的 BF16 三乘积，正式复测 0.280 ms。

这里有实际的探索取舍差异：一方继续压低正常路径的耗时，另一方投入了更多边界检查和保护。
但两者不是同一份源码，诊断形状和检查方式也不完全相同；没有在共同补充测试下复测 baseline，
不能据此认定 baseline 错误，也不能认定 R3 的每次撤回都多余。FP16 大输入溢出属于具体错误，
更不能用“它通过正式随机测试”掩盖。

下一次公平比较应使用同一份任务约束文本，明确固定形状支持范围、数值容差和共同的补充检查。
角色差异只保留协作所需部分；同样的候选有效性规则同时用于 baseline 和 RSI。这样才不会让
各个 agent 自行理解出的验证负担混进协作方式的比较。

## 本地复核资料

`.rsi/passk-workbench/operator/tool-throughput-fixes-0920/` 保存：

- `performance.json`：修复前后状态读取和反馈长度。
- `alignment.json`：全部通过回执的配置汇总、题目哈希和 baseline 选优回放。
- `evaluator-audit.txt`：远端保留的作业规格，以及当前 worker/evaluator 的字段和源码哈希。
- `baseline-numerical-diagnostic.py`：从该 baseline 冻结 GPU 作业中读取的公开诊断脚本。
- `changes.patch`：相对于上一部署快照的本次修改。

上述分析资料只供操作者使用，没有输入给 researcher，也没有修改历史候选或成绩。
