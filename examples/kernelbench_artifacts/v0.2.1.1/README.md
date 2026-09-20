# v0.2.1.1：L1P63 单题复现

复现 2026-09-20 的 v0.2.1.1 分组中 L1P63 的实验配置：

- Leader 和 researcher 均使用 `gpt-6-astra` / `xhigh`，Codex 经 AReaL Agent Runtime 执行。
- 4 名 researcher 各有 3600 秒有效探索时间，扣除 API 故障等待和 GPU 排队；不限提交次数。
- 使用 `passk_guided_v0211`：leader 定向共享经验，未解决的问题最多再分享给一人；已验证成果可分享给多人。
- 使用原生 `kb eval` / `gpuq run` CLI，允许原生联网调研，每名 researcher 从公开 reference 独立开始。
- 探索采用固定 reference 校准、strict FP32、seed 42、5 次正确性检查及 100 次计时；预算结束后只对最佳有效候选做一次 seed 137 最终复测。

## 启动

使用 `zwh/v0.2.1.1` 分支，按仓库 README 安装依赖并在 `.env` 配置 API 地址和密钥。
`ssh et15` 须能访问 [et15.json](../et15.json) 中的环境，GPU 队列为
`http://33.180.160.37:38472`，与原实验一致。

如果已有当前版本的 8198 工作台，直接执行第二条命令；否则先启动工作台：

```bash
uv run --env-file .env --locked python run.py --type web --port 8198 --data-root .rsi/passk-workbench
```

在另一个终端、仓库根目录执行：

```bash
uv run --env-file .env --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.2.1.1/experiment.json --port 8198
```

脚本沿用工作台的 API 地址和凭据，重新校准 reference，创建独立研究并输出工作台链接。
重复执行会新建研究。若启动中断，使用输出的 `batch.json` 路径重试，保留原预算与研究 ID：

```bash
uv run --env-file .env --locked python examples/kernelbench_artifacts/batch.py --resume /path/to/batch.json
```

配置、Prompt 和启动回执保存在工作台数据目录的 `operator/<batch_id>/`，研究轨迹和最终结果在
`runs/<run_id>/`。本示例只运行 L1P63；模型探索和测量存在波动，不保证得到相同分数。
