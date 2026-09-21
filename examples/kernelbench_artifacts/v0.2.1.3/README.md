# v0.2.1.3：及时报告与客观分享

在加载了本版代码、已配置 API 和 et15 GPU 排队服务的工作台上运行：

```bash
.venv/bin/python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.2.1.3/experiment.json --port 8198
```

配置并行启动 Astra / xhigh 的 L3P10、L2P71、L1P63，放在同一新分组。
每题四名 researcher，各有一小时有效时间，不限提交次数。沿用既有 API / key、
CLI 评测方式和 GPU 服务；工作台使用 `RSI_MAX_CONCURRENT_RESEARCH=3` 限制全局研究并发。

四份身份继续使用 [v0.2.1.2](../v0.2.1.2/README.md) 的配置：一名综合研究员，
以及算法、访存、GPU 执行三种偏好。启动器会把身份正文冻结到研究配置。
本版只增加 `objective_sharing` 模块，改变报告、分享的 prompt 和消息中的身份说明；
没有增加自动突破推送。具体边界和完整 prompt 见 [版本说明](../../../docs/leader-guidance-v0.2.1.3.md)。

这是启动配置，添加文件本身不会运行实验。只准备可加 `--prepare-only`；
中断后使用 `batch.py --resume <batch.json>`，避免重复创建。

七题 Luna / xhigh 批次使用 `luna-seven.json`，排除 L2P80：

```bash
.venv/bin/python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.2.1.3/luna-seven.json --port 8198
```

这个批次按七题同时运行设计，工作台需以 `RSI_MAX_CONCURRENT_RESEARCH=7` 启动。
每题的人数、有效时间和身份配置与上面的三题例子相同。
