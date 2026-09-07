# GSM8K Harness 自迭代示例

使用 [OpenAI 官方 GSM8K](https://github.com/openai/grade-school-math)，固定修订版 `3101c7d5072418e28b9008a6636bde82a006892c`，下载时校验 SHA-256。数据遵循上游 MIT License，下载目录保留许可证。

## 为什么不直接拿官方 test 做每轮选版

反复使用 test 分数选择 Harness 会让 test 参与研发。这里将官方 train 按题面 SHA-256 稳定排序，每十题留一题作为 RSI VALIDATE，其余作为 RSI TRAIN。官方 test 完全不参与这段自迭代；面板中的分数是留出验证集结果，不能宣称是 GSM8K 官方 test 成绩。

## 使用

```bash
uv run python -m areal_rsi.modules.gsm8k --directory .rsi/datasets/gsm8k
export RSI_DATA_ROOT='.rsi/gsm8k-research'
export RSI_BENCHMARK='gsm8k'
export RSI_GSM8K_DATA='.rsi/datasets/gsm8k'
export RSI_TRAIN_LIMIT=16
export RSI_VALIDATE_LIMIT=16
uv run rsi tui
```

沿用 [启动说明](../../README.md#启动) 中的本机模型配置。在对话中输入“请启动三轮 Harness 自迭代，固定模型”，确认计划后运行。每轮候选都重新执行选定 VALIDATE 题目，不根据 iteration 人为加分。先用小题数检查成本，再增加题数；完整 train 有 7473 题，留出后 TRAIN 为 6725 题、VALIDATE 为 748 题。

## 数据与隔离

- `train.json`、`validate.json` 只有公开 Task，不含答案、解题过程或私有文件路径。
- `private/answers.json` 由宿主 Verifier 读取，不复制到 Task/Research Agent 工作区。
- `manifest.json` 固定数据来源、划分方法和各文件摘要。Benchmark 名称包含数据与抽样身份，恢复时改动题数会被拒绝。
- Verifier 检查任务和 Solution 身份，只解析最后一行 `#### NUMBER`，反馈不暴露正确答案。
- 已准备数据不能原地覆盖；改变数据需使用新目录和新实验。

`make test` 使用明确标注的合成 fixture 检查加载器、分区、答案格式、摘要和隔离契约，不联网下载官方数据。真实下载和模型实验是独立联调步骤。
