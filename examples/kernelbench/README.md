# KernelBench harness 接入

Research 修改通用 harness，固定的 Task 模型通过 DSH 解题，再由 GPU evaluator 检查正确性与速度。初始 harness 和候选使用同一 search 题池比较；PyTorch reference 延迟不是 harness 的 baseline 分数。

## 环境与配置

主控使用 macOS、DSH 和 `RSI_TARGET_MODEL / RSI_TARGET_BASE_URL / RSI_TARGET_API_KEY`。GPU 侧可用 SSH Linux，需 Python 3.10、CUDA、匹配驱动的 torch ≥2.9；选择 Triton 后端时还需 Triton。KernelBench 固定版本为 `423217d9fda91e0c2d67e4a43bf62f96f6d104f1`，评测代码不能修改。

```bash
git clone https://github.com/ScalingIntelligence/KernelBench.git .rsi/KernelBench
git -C .rsi/KernelBench checkout 423217d9fda91e0c2d67e4a43bf62f96f6d104f1
uv run python -m areal_rsi.adapters.kernelbench prepare \
  --repository .rsi/KernelBench --config examples/kernelbench/config.json \
  --output .rsi/kernelbench-prepared
```

[config.json](config.json) 定义互斥的 development、search、final 题池，实际划分由用户决定。复制 [operator.example.json](operator.example.json) 到候选目录外，填写数据和初始 harness 的绝对路径、SSH 环境、每题预算与固定评测参数。

- `edit_scope=skills` 只允许修改 skill；`harness` 允许修改整个 harness。起点放在 `baseline_harness`。
- `gpu.kind=ssh` 使用已有 SSH key/agent；远端 Python 需安装同一 KernelBench 版本及评测依赖。
- phase 固定为 `diagnostic → development`、`search → search`、`final → final`。正式计时需独占 GPU。

## 启动

可直接在[工作台](../../README.md)向 Interaction 提供上述路径和要求，也可预先生成配置：

```bash
uv run python scripts/configure_research.py --example kernelbench \
  --operator /absolute/path/operator.json --output /absolute/path/research.json \
  --target-model FIXED_TARGET_MODEL --target 0.65
uv run --env-file .env --locked rsi workbench --config /absolute/path/research.json
```

配置生成器检查各阶段的环境和评测身份，不调用 Task 模型或产生分数。`--target`、研究员数量及其他预算由用户指定。

生成的是纯 pass@k 配置，默认 1 名研究员、4 小时墙钟探索预算，最终复测另计，不限制模型调用、Token 和验证次数。
可用 `--max-agents`、`--max-wall-seconds` 调整；模型调用、Token 和验证次数上限只接受 `none`，不支持的配置会在环境检查前报错。
探索达标或到时结束后，系统从已验证结果中选出最佳候选，自动进行一次独立 final 验证。

调度器入口为 `run.py --harness <candidate> --output <new-output> --config <operator.json> --phase diagnostic|search|final`。`diagnostic` 的轨迹从 `public/development/` 发布，仅作研究证据；正式分数从可信 `evaluation.json` 读取，不从 stdout 提取。只有固定题池的完整评测可参与比较，基础设施失败没有有效成绩。当前上游检查器仍在同一 Python 进程加载 kernel，不能视为恶意候选的完整安全沙箱。
