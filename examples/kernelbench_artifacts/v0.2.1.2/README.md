# v0.2.1.2：带研究偏好的四名 researcher

在已配置 API 和 et15 GPU 排队服务的本地工作台上运行：

```bash
.venv/bin/python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.2.1.2/experiment.json --port 8198
```

默认并行启动 Astra / xhigh 的 L3P10、L2P71、L1P63。每题四名 researcher，各有一小时有效时间，不限提交次数。API 故障和 GPU 排队按现有计时规则扣除，正常推理、编译和 GPU 执行计时。

身份依次为综合、算法与计算结构、数据布局与访存、GPU 执行与编译。现在从 `src/areal_rsi/resources/researcher_profiles/` 加载同样的人设正文；本目录的 `profiles/` 保留最初版本供查看。启动时将完整身份冻结进研究配置；后续编辑文件不会改变已经启动的研究。其他 bench 的资产生成和选择方式见[人设使用说明](../../researcher_profiles/README.md)。

协作方式选择 `passk_guided_v0212`，沿用 v0.2.1.1 的报告、指导和问题经验共享限制。四人都研究完整题目，身份只影响研究偏好。

启动前用 `RSI_MAX_CONCURRENT_RESEARCH=3` 设置工作台的全局并发上限。批次会打印 `batch.json` 的路径；准备或启动中断后使用 `batch.py --resume <batch.json>`，避免重复建立实验。只准备、不启动可加 `--prepare-only`。同名分组会追加研究。

完整的条件实验计划见 [实验计划](../../../docs/experiment-plan-v0.2.1.2.md)。本例不会自动执行后续阶段。
