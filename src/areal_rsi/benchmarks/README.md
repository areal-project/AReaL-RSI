# Benchmark 适配

这个目录负责发现和装配 AReaL-RSI 支持的 Benchmark。Benchmark 定义评测内容、
数据来源、Verifier 和 Evaluation；它不决定实验优化 Harness、Model 还是 Artifact，
优化对象由 ResearchConfig 的 `evolution_target` 分类声明，实际边界由任务说明、
可修改路径和可信执行/评测配置共同决定。Catalog 不负责研究编排。

## 目录结构

```text
benchmarks/
├── config/
│   └── supported_benchmarks.yaml  支持的 Benchmark 及其配置参数
├── adapters.py                    内置可信 Adapter
├── catalog.py                     Manifest 加载、校验和 Registry
└── README.md
```

`BenchmarkCatalog` 将 Manifest 解析为不含答案和凭证的公开描述。
`BenchmarkRegistry` 解析配置并通过已注册的 Adapter 创建 `BenchmarkBundle`：

```text
BenchmarkBundle
├── Benchmark   提供公开 Task
├── Verifier    使用宿主侧隐藏答案验证结果
└── Evaluation  聚合 VerificationResult
```

Manifest 中的 `id` 是稳定的 Benchmark 类型，例如 `gsm8k`。Bundle 的
`instance_id` 标识实际数据版本、分区和抽样配置；有效任务发生变化时，实例 ID 也必须变化。

## 添加 Benchmark

1. 在 `src/areal_rsi/modules/` 实现或复用 `Benchmark`、`Verifier` 和 `Evaluation`。
2. 在 `adapters.py` 添加一个薄 Adapter，负责校验组件组合并创建 `BenchmarkBundle`。
3. 在 `config/supported_benchmarks.yaml` 注册公开描述、数据 Provider 和参数。
4. 为数据隔离、实例身份、正常及异常验证结果添加离线测试。

不要在 `ResearchApp` 中增加 Benchmark 名称分支。Manifest 只能引用程序注册的可信 Adapter，
不能包含任意 Python 导入路径、Shell 命令、凭证或隐藏答案。TRAIN/VALIDATE 的公开 Task
不得包含 expected；Verifier 所需的私有数据只能由宿主程序读取。

仓库内的 [`benchmark-adapter` Skill](../../../skills/benchmark-adapter/SKILL.md) 描述了完整适配流程。
