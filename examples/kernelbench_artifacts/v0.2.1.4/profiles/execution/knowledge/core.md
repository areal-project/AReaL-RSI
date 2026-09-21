# 从测量理解 GPU 执行

完整运行时间、算子时间线、编译器资源信息和硬件计数器回答不同的问题。先知道时间花在哪里，再选择更细的工具。PyTorch Profiler 可以定位昂贵算子和 GPU kernel；编译信息可以说明寄存器、共享内存和生成指令；Nsight Compute 进一步观察运行时利用率、访存与调度。任何一种都不能独自代替完整性能验证。

寄存器和共享内存会限制同一 SM 上可以同时驻留的工作量，但占用率越高不一定越快。增加 warp、减小 tile 或减少流水级数，可能提高驻留能力，也可能破坏数据复用和指令效率。静态寄存器数不是运行时停顿的直接证据；需要用对照实验或硬件指标检查解释。

对于多阶段计算，数据依赖、同步与加载延迟可能使计算单元空闲。更多异步操作并不自动减少等待；应检查是否形成实际重叠，以及新增缓冲和同步是否抵消收益。kernel 很短或数量很多时，还应观察 CPU 发起工作和 GPU 执行之间的空隙，而不只优化单个 kernel 的数学运算。

同一种高层运算可能生成不同指令。FP32 输入的矩阵乘是否使用矩阵指令、采用什么乘法和累加方式，取决于硬件、编译器与具体精度选项。Triton 的 `ieee`、`tf32`、`tf32x3` 是不同选择；支持范围和数值结果应查当前版本并独立验证，不能用其中一种的失败推断另外几种。该区别是工具背景，不保证某种模式适用于当前题目。[Triton dot 文档](https://triton-lang.org/main/python-api/generated/triton.language.dot.html)

硬件指标同样需要解释：缓存命中率高不一定表示总访存少，带宽没有打满也不一定表示计算受限。warp 的某种 stall 比例高，只有在调度器缺少可发射工作时才可能成为主要限制。优先把指标转化为可检验的问题，例如“减少这次同步是否会缩短关键阶段”，而不是把报表中的最大数字直接称为瓶颈。

profiling 会增加开销，采样与重放也可能改变运行条件。它用于定位和解释；完整候选的正确性与最终性能仍以正常评测为准。profile 前排除首次编译等一次性成本，保持任务要求的训练状态、精度、输入形状和数据条件。profile 观察到了什么、尚未测到什么，分别说明。

资料入口：[PyTorch Profiler](https://docs.pytorch.org/docs/2.8/profiler.html)、[Nsight Compute Profiling Guide](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)、[CUDA 执行与资源使用](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html)。详细操作见本身份的 kernel-profile skill。
