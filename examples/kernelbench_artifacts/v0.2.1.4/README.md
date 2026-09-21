# v0.2.1.4

基于 v0.2.1.2，增加 leader 定期观察和人设资料。仍由四名研究员各自完成整题；报告、分享与评测沿用原方式。

在配置好 API 和 `et15` GPUQ 的同一环境、工作台已运行时：

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.2.1.4/experiment.json --port 8198
```

该配置是 Astra / xhigh 的 L3P10、L2P71、L1P63，各四名研究员、每人一小时有效时间。
复制 JSON 可以调整题目、模型和分组。运行上述命令才会启动实验。

七题 Luna / xhigh 使用同目录的 `luna-seven.json` 替换上面的 `experiment.json`，
分组为 `v0.2.1.4-luna`，七题并行启动；研究员数量与有效时间预算相同。

batch.py 冻结一名通用研究员和三份领域资料，每人包含 AGENTS.md、knowledge/core.md。
只有 execution 身份包含 `skills/kernel-profile`，附自包含诊断脚本。
通用资料位于 `src/areal_rsi/agents/research/resources/general/`；领域资料在本目录 `profiles/`。
注册后的资料和必读清单保存在研究配置中，源文件变化不会影响既有研究。

可选的两个增量分别为 `team_review`、`profile_resources`；都依赖 v0.2.1.2 的三个基础模块。
若直接通过 API 创建研究，可传入 metadata.researcher_profiles 的 resources/required_reading。
没有提供资料包时使用原身份文字与通用研究知识，不会自动给其他领域配 GPU skill。

实现边界和 prompt 入口见 [设计说明](../../../docs/leader-guidance-v0.2.1.4.md)。
