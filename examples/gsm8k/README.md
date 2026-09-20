# GSM8K 数据组件

`areal_rsi.modules.gsm8k` 保留官方 GSM8K 的固定版本下载、TRAIN/VALIDATE 划分和
宿主评分器，作为可复用的 Task benchmark 组件。旧的独立实验入口已删除。

```bash
uv run python -m areal_rsi.modules.gsm8k --directory .rsi/datasets/gsm8k
```

数据加载器固定修订版与 SHA-256，公开 Task 不包含答案。官方 test 不参与研究阶段
选版，不能将内部 VALIDATE 分数宣称为官方 test 成绩。

当前开箱即用的数学 ResearchApp 示例使用原创算术题，见[数学示例](../local/README.md)。
接入其他数据集时，使用 ResearchApp 的操作员验证命令，不启动独立研究调度器。
