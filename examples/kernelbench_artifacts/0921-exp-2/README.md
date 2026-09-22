# 0921-exp-2

同时运行 L2P71 的三组 Luna / xhigh 实验：v0.1 纯 pass@4、v0.2.1.2、
v0.2.1.2 + profile skill。每组四名研究员，每人一小时有效时间。
skill 使用 0921-exp-1 的同一份文件，仅提供给 GPU 执行与编译研究员。

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/0921-exp-2/experiment.json --port 8198
```

使用已配置的 API 和 et15 GPU 服务。各组独立校准 reference；比较探索候选耗时、
正式加速比和研究轨迹。配置与 Prompt 在 operator/batch_*/ 中冻结保存。
