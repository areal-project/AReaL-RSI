---
name: kernel-profile
description: 用 GPU 诊断脚本定位完整实现中的耗时与执行瓶颈，再针对关键 kernel 检查资源或硬件指标。适用于有可运行实现但性能原因不清楚，或连续调参没有稳定收益时。
---

# Kernel 性能诊断

先说明想通过测量回答什么，再选择工具。完整模型的瓶颈还不清楚时，先看算子和 kernel 时间；已有明确的热点，再检查它的资源使用与运行时指标。普通参数变化不必每次都做完整 profile。

## 在当前实验环境运行

本地工作区没有 CUDA。把诊断脚本放在自己的工作区，通过 `gpuq run -- python 脚本.py` 提交，日志随作业保留。正式评测仍用 `kb eval model_new.py`。诊断脚本只依赖被传入 GPU 作业的 `model_new.py`、`reference.py` 和环境已有库；不能假定本地其他文件也会传过去。

本技能提供了一个自包含脚本：

```bash
cp researcher/skills/kernel-profile/scripts/profile_kernel.py profile_kernel.py
gpuq run -- python profile_kernel.py --check-tools
gpuq run -- python profile_kernel.py --target candidate
```

检查工具和正式 profile 是两个作业，按现有规则逐个提交。已有环境信息时不必重复检查。`--check-tools` 会真正运行 `ncu --version`、`nsys --version`，而不只检查命令名是否存在；可执行入口存在不代表安装完整。版本检查成功也不代表有硬件计数器权限，仍要看实际 profile 结果。

脚本通过公开 `reference.py` 的输入接口构造工作负载，保留模型构造后的 train/eval 状态，关闭参考矩阵乘和 cuDNN 的 TF32，使用 no_grad。运行前核对这些条件与本题规则是否一致；如规则规定了不同输入分布或状态，按公开要求修改诊断脚本。它只生成诊断信息，不承担正确性裁决。

## 先定位时间花在哪里

脚本先预热，再用 PyTorch Profiler 输出 CPU/CUDA 算子表和 GPU kernel 的调用次数、总时间及平均时间。默认不收集 shape、stack 和内存分配信息；需要解释布局或额外分配时再加 `--record-shapes` 或 `--profile-memory`，它们会增加开销。`--target reference` 可对公开参考实现做同类测量。

看表时区分 self time 与包含子操作的 total time，不能把父子算子的时间相加。先检查少数昂贵 kernel，或大量很短的 kernel；如果 GPU 时间不足以解释整题耗时，再考虑 CPU 调度、同步和调用间隙。profile 中的一次测量不宜直接作为正式成绩。

需要时间线时可加 `--trace trace.json`。该文件留在远端诊断工作区，当前 CLI 不保证自动下载；脚本会把主要 GPU 事件摘要打印到作业日志，不要反复在本地寻找远端文件。

## 对热点做进一步诊断

Triton kernel 编译后，可以检查返回的 compiled kernel 对象的 `n_regs`、`n_spills`、`metadata.shared` 和 `asm`，具体字段以当前版本为准。它们帮助判断生成代码和静态资源使用，但不能直接证明运行时带宽、占用率或 stall。只分析自己的候选代码，遵守任务对资料来源的要求。

Nsight Compute 可用时，对已经定位的 kernel 做一次有范围的采集：

```bash
gpuq run -- python profile_kernel.py --backend ncu --kernel '你已定位的kernel名称正则'
```

脚本使用 `--profile-from-start off`，预热结束后才打开采集，并限制为一个匹配的 kernel launch。默认 basic set；若要回答更具体的问题，可加 `--section MemoryWorkloadAnalysis`，或选择 `SchedulerStats`、`WarpStateStats` 等当前版本实际提供的 section。不要一开始对整网收集 full set。

结合问题解释数据：

- LaunchStats / Occupancy：查看寄存器、共享内存和驻留限制；更高 occupancy 不是独立目标。
- SpeedOfLight / MemoryWorkloadAnalysis：区分计算、访存吞吐与访问效率；低带宽利用率也可能来自等待或不足的并行工作。
- SchedulerStats / WarpStateStats：先看是否缺少可发射的 warp，再判断主要等待来自什么。某种 stall 比例高不一定限制整体性能。

若工具缺失、计数器没有权限或采集失败，保留原始错误，改用 PyTorch Profiler、CUDA event 分段计时和编译器信息。不要把空报表当成“没有瓶颈”，也不在研究期间安装系统工具或反复重试同一个环境故障。

## 把结果变成下一项实验

向 RSI 报告最有用的观察、作业引用，以及由此准备验证的问题。例如“时间主要花在布局转换，准备比较直接写目标布局”，而不是只转发一张表。把测到的事实与解释分开。按这个判断修改实现后，用正常 `kb eval` 检查正确性和整题收益；profiling 的采集、重放开销不计作候选成绩。

查阅入口：[PyTorch Profiler](https://docs.pytorch.org/docs/2.8/profiler.html)、[Nsight Compute CLI](https://docs.nvidia.com/nsight-compute/NsightComputeCli/index.html)、[Nsight Compute 指标与重放](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)。
