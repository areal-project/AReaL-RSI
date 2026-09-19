# GPU 队列的简单隔离

实现位于 `examples/kernelbench_artifacts/queue_sandbox.py`，部署为现有 GPUQ 的
`sandbox_exec.py`。沿用队列已经建立的 mount/PID namespace 和 GPU 分配，不新增调度器。

每次作业建立一个私有根目录，只提供：

- 当前作业的输入和输出，以及属于当前 task/run 的编译缓存；匿名管理作业使用自己的缓存。
- 只读的 Python、CUDA、驱动库与评分模块。公开 KernelBench 源码和题目复制到只读临时根，
  避免共享目录的 owner-only 权限迫使作业保留读取特权。
- 分配到的 GPU 节点与驱动控制设备、私有 `/tmp` 和 `/dev/shm`。
- 本作业的 `/proc`；CUDA 所需的线程命名接口可写，内核控制入口只读。

进入根目录后移除 Linux capabilities，启用 no-new-privileges；网络 namespace 没有外部
连接。候选和诊断因此不能通过文件、其他进程、SSH 或队列 HTTP 读取其他 run。
Agent 本身的原生网页调研运行在主控端，不受 GPU 作业断网影响。

部署只需备份并原子替换队列启动脚本。队列每次作业都会重新加载它，无需重启调度器。
部署方应确认所有节点均实际启用该入口；KernelBench `prepare(check=True)` 校验安装脚本
与仓库 SHA256 一致，并继续核对评分协议。不要通过伪造健康检查字段跳过安装与验证。

当前 et15 的两个节点已用于隔离与 Triton 探针。安装前的脚本备份和操作回执位于远端
`dsh-kernelbench/runtime/isolation-check/`；本地操作记录位于
`.rsi/passk-workbench/operator/queue-isolation-0918/`。原研究保持停止，未重启工作台。

该脚本适配现有 KernelBench 容器布局。更换镜像或安装目录时，需要重新验证只读依赖，
而不是开放整个共享目录。它解决跨 run 数据访问，不代替评分器自身的正确性与计分保护。
