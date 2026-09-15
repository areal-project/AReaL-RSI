# 3D MODEL-001 任务适配器

保留公开 MODEL-001、ThreeModelBenchmark、ThreeModelVerifier、ModelEnvironment 和 FileTaskRuntime。任务是让 Task Agent 生成 `src/model.js`，在固定 Contract V2 环境中验证结构约束。

```bash
uv run --env-file .env --locked python examples/3d_bench/run_local.py --check
```

需要事先准备 `.rsi/datasets/3d-model-v2/` 的 template、snapshot 和 runtime；仓库不包含私有资产。验证不代表视觉质量评分，TRAIN/VALIDATE 使用相同公开 case，不构成泛化评估。

旧 ExampleApp/TUI 注册和固定三轮演化入口已移除。当前保留任务级适配能力，3D 的完整新模式研究配置尚未提供；本次统一入口验收覆盖数学和 LCB。接入新任务的方法见[执行接口](../../docs/interfaces/README.md)。
