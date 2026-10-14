# Harness 优化示例

这组示例优化通用 prompt/skill，让**固定的 Task 模型**在固定题池上得到更好的结果。
原 `examples/local`、`examples/remote` 只是运行位置，不能说明研究对象，现统一归到这里。

| 示例 | 研究员修改什么 | 谁完成 Task | 评测 |
| --- | --- | --- | --- |
| arithmetic | prompt/skill | 固定本地接入的目标模型 | 3 道 TRAIN、3 道 VALIDATE 原创算术题 |
| livecodebench | system prompt | 固定 EnvArena 模型与 Harness | 远端 LCB Reward |
| [kernelbench](kernelbench/README.md) | 通用 kernel 生成 harness | 固定目标模型生成 kernel | GPU 检查正确性与速度 |

## 算术 harness

在 `.env` 配置研究模型 `RSI_*` 和目标模型 `RSI_TARGET_*`，在仓库根目录执行：

```bash
uv run --env-file .env --locked rsi prepare arithmetic --directory .rsi/math/setup
uv run --env-file .env --locked rsi research --config .rsi/math/setup/research.json --data-root .rsi/math
```

这是原创 arithmetic-v1 小规模连通示例，不是官方 GSM8K。答案保留在宿主验证器中。

## LiveCodeBench harness

[livecodebench.yaml](livecodebench.yaml) 固定 Benchmark、Harness、Model、Reward，以及
TRAIN=2727、VALIDATE=2777。配置研究模型 `RSI_*`；Arena 认证读取
`ARENA_OPENAPI_TOKEN` 或 `~/.envarena/config.toml`，不写入研究配置。

```bash
uv run --env-file .env --locked rsi prepare livecodebench \
  --arena-config examples/harness/livecodebench.yaml --directory .rsi/lcb/setup
uv run --env-file .env --locked rsi research --config .rsi/lcb/setup/research.json --data-root .rsi/lcb
```

两种准备命令都只生成配置，不启动研究。默认由一个 Leader 执行 goal，1200 秒总时限；
`--time-limit` 调整时限。Leader 用 diagnostic 做起点诊断、search 比较候选，
选定候选后在独立 VALIDATE 上运行 final，检查结果并提交本 run 的验收。
完整 TRAIN 轨迹通过显式公开目录提供；基础设施失败不会被记作有效分数。
