# 0921-exp-1

两份 Luna / xhigh 的 L2P71 同时启动，每份四名研究员，每人一小时有效时间。

- control：原 v0.2.1.2。
- profile skill：原 v0.2.1.2，只向 GPU 执行与编译研究员追加 profiling 技能及文件。

leader、其他三名研究员的 Prompt、报告与分享规则均不变。不启用 v0.2.1.4 的
team_review 或 profile_resources。技能入口留在身份 Prompt 中；长篇方法按需读取，
不会要求所有人开局阅读资料。技能不包含题目答案或其他研究的结果。

在同一 API 配置、et15 GPU 服务及已部署的工作台上运行：

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/0921-exp-1/experiment.json --port 8198
```

启动记录、冻结的配置和 Prompt 保存在工作台 operator/batch_*/ 下。每份研究仍按
既有流程独立校准 reference；比较时同时看候选毫秒数，避免校准差异影响解读。
