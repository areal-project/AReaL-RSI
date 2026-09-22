# 工具与采集

## 当前 GPU 队列

在自己的工作区准备脚本，用 `gpuq run -- python profile_kernel.py ...` 提交。
脚本应自包含，只依赖当前作业传入的候选、公开 reference 和已有库；不要假定本地的其他
Python 文件会自动同步。正式性能和正确性评测仍走 `kb eval model_new.py`。

工具检查是独立作业，有已有信息时不用重复：

```bash
gpuq run -- python profile_kernel.py --check-tools
```

这个检查实际执行 `ncu --version` 和 `nsys --version`，避免把失效入口当成安装成功。
版本可读也不保证 GPU 计数器权限正常。失败时保留原因，使用下面的替代证据；
不要在同一环境故障上反复重试或安装系统包。

## PyTorch Profiler

默认脚本先预热，随后记录少量 forward，输出算子 self/total time 和 kernel 汇总。
默认不启用 shape、stack 和内存追踪，减少额外干扰。需要定位额外张量时再打开对应选项。
特别注意多流重叠、包含子操作的 total time，以及被同步等待放大的 CPU 时间。

若采集不到 CUDA 活动，明确写“没有采到 GPU 事件”，不要解释成 GPU 没有执行。
先确认活动类型与环境支持，再用 CUDA event 计时和局部对照实验继续研究。

## 编译器资源

对自己的 Triton kernel，一次正常 launch 返回的 compiled kernel 对象通常可以提供
寄存器、spill、共享内存和生成代码信息，例如 `n_regs`、`n_spills`、`metadata.shared`、`asm`。
字段随版本变化，先检查实际对象；不要为了不存在的字段反复重编译。

比较相邻两种实现的资源变化，必要时定位自己的生成代码中的 load/store、同步、spill 或
地址计算。编译资源是静态信息，不等于实际运行时 occupancy、带宽或 stall。
遵守本题允许的资料来源范围，不读取其他 run 或未公开的实现。

## Nsight Compute 可用时

先从普通 profile 找到热点名称，再只采集这个热点：

```bash
gpuq run -- python profile_kernel.py --backend ncu --kernel '热点名称正则'
```

脚本通过 profiler start/stop 把预热排除在范围之外，并限制一次匹配的 launch。
默认 basic set。需要时通过 `--section` 选择更具体的内容，名称以安装版本为准：

- LaunchStats / Occupancy：grid、线程块和静态资源限制。
- MemoryWorkloadAnalysis：访存层次、事务效率与吞吐。
- SchedulerStats / WarpStateStats：是否缺少可发射 warp，以及等待线索。

先确认发射受限，再研究 stall 原因；某种 stall 占比高本身不等于应该优化它。
不要对整网默认收集 full set。指标可能需要多次重放，采集开销与缓存条件会偏离正常执行，
因此 ncu 的结果用于解释，而候选成绩仍来自正常评测。

原始选项与重放说明见 [Nsight Compute CLI](https://docs.nvidia.com/nsight-compute/NsightComputeCli/index.html)。
