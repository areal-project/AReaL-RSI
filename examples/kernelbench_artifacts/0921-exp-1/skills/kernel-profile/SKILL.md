---
name: kernel-profile
description: 定位 GPU 实现中的耗时与瓶颈，将算子时间、编译资源和可用硬件指标转化为可验证的优化实验。适用于实现已经能运行但速度原因不清楚，或连续调参没有稳定收益时。
---

# Kernel 性能诊断

这项技能帮助你解释为什么慢，选择下一次有信息量的实验。它不指定实现路线，也不要求
每次修改都做完整 profile。已有明确假设时直接验证；不知道时间花在哪里时再采集数据。

先读自己的公开任务和规则，核对输入 shape、stride、dtype、模型状态和计时边界。
诊断与正式评测使用相同的工作负载语义；不能为了方便把 training 改成 eval，或改变精度规则。
不要把某个精度选项的名字直接当成正确性结论，候选是否正确仍交给公开要求下的正式评测。

## 从一份能解释问题的测量开始

本地没有 CUDA，诊断也通过 GPU 队列运行。附带脚本可直接复制到自己的工作区：

```bash
cp researcher/skills/kernel-profile/scripts/profile_kernel.py profile_kernel.py
gpuq run -- python profile_kernel.py --target candidate
```

脚本预热后输出 PyTorch 算子时间表和 GPU kernel 的次数、总时间、平均时间。
可用 `--target reference` 观察公开参考实现；需要 shape 或分配信息时才加
`--record-shapes`、`--profile-memory`。脚本保留构造时的模型状态，使用公开输入接口、
no_grad 和关闭参考 TF32 的设置；若本题要求不同，按公开要求调整诊断脚本。
默认诊断 seed 为 0；遵守任务对诊断数据和保留复测数据的限制。

不要把“最大 kernel 占了 90%”当成诊断终点。接着区分：是计算量大、访存组织差、
依赖等待、资源压力，还是工作量太少无法利用 GPU？挑一个能区分这些解释的实验。
观察支持哪种解释，就继续那条线；观察不支持时修改假设。

## 按问题查阅

- 时间表如何读、如何做公平的 A/B、如何估算值得优化的上限：
  [计时与实验设计](references/measurement.md)。
- 单个热点很慢，想区分访存、计算、资源和并行度问题：
  [瓶颈判断与验证](references/bottlenecks.md)。
- 需要 Nsight、编译器资源信息，或工具缺失时的替代办法：
  [工具与采集](references/tools.md)。

先用现有低开销工具回答问题，必要时再采集更详细的信息。Nsight 不可用时，
PyTorch Profiler、CUDA event 和受控的实现变化也能提供证据，不要把时间花在安装系统工具上。

有意义的报告包含：测到什么、对应源码和作业、你如何解释、下一步准备验证什么。
事实与推测分开，继续沿用原有非阻塞报告方式。修改后用 `kb eval model_new.py`
验证完整实现；profile 的采集时间和单个热点速度都不是正式成绩。
