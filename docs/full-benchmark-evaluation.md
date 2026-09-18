# 全量 baseline 与最终复测

新研究工作流支持由框架强制执行全量评测。PAST-Bench 的 configure 默认启用；其他
Benchmark 适配器需要声明自己的全量任务清单，框架不能从一个子集结果猜出完整 Benchmark。
既有研究和未启用此规则的配置保留原行为。

1. 启动时冻结初始候选，自动提交 baseline。在全量结果被验证之前，不启动研究模型或实验。
2. 中途允许 diagnostic/search 子集实验，保留最后一次验证额度用于最终全量复测。
3. RSI 选择候选并提交 final 后冻结选择、停止 Research Agent，不再允许修改方向或换候选。
4. 最终结果通过与 baseline 相同的覆盖和协议校验后，才允许 finish。失败、中断或预算耗尽
   不构成已完成的全量结论。最终复测失败可在预算允许时重试同一候选。

零分或 `passed: false` 仍可作为有效测量；基础设施失败、缺任务不可以。
若另设了目标分数，finish 仍需满足目标。全量初始与最终结果以及数值差值保存在
`coordination/full-comparison.json`，同名辅助指标也会比较。它不是未见任务的泛化测试。

## 复用已有 baseline

在实验 YAML 中配置：

```yaml
metadata:
  full_benchmark_evaluation: true
  full_baseline_source: ./baseline/evaluation.json
```

文件路径相对于 YAML 所在目录解析。也支持 HTTP(S) URL，或将完整 JSON 结果以 YAML 对象
直接写入 `full_baseline_source`。应提供适配器产生的机器可读 evaluation.json，不是只包含
一个总分的网页或文字摘要。

框架检查：完成且非模拟、有限分数、指标一致、初始候选/Harness 摘要一致，以及完整的
benchmark 身份（协议、模型、manifest、按顺序排列且无重复的全部任务 ID）。模型等字段
由适配器纳入身份；PAST-Bench 已包含这些字段。提供的外部结果是操作者信任输入，身份
校验不能证明外部评测真实执行过。

有效结果归档为 `coordination/imported-baseline.json`，不提交 baseline 作业、不消耗验证额度；
恢复运行使用归档，不重复下载或依赖原文件仍存在。最终全量复测仍必须执行。
导入失败会暂停并记录 baseline_error，不会未经明确处理就改为付费重跑。

PAST-Bench 配置入口：

```bash
uv run examples/past_bench/run.py configure \
  --lock /absolute/path/protocol.lock.json \
  --harness /absolute/path/seed \
  --output /absolute/path/research.json \
  --baseline-source /absolute/path/evaluation.json
```

## 接入其他 Benchmark

配置需有 seed_dir、verifier 和 verification contract。在 metadata 中启用
`full_benchmark_evaluation`，并声明 `verification_identity.phases.baseline` 与 `.final`。
两者必须完全一致，包含非空 protocol_id、manifest_id 以及有序、唯一、非空 task_ids。
适配器必须独立获取完整任务清单，并从实际执行产物核对任务覆盖，不能直接回填预期 ID。
适配器需要接受 baseline 阶段，并让 baseline/final 都执行完整清单。

当前支持可信结果适配器 harness、kernelbench、content。不要与旧 finalizer 同时配置；
该组合会被拒绝，避免旧收尾路径绕过冻结规则。有限验证预算至少为两次；导入 baseline 时
至少一次。运行失败或手动停止不会自动选出候选或伪造最终复测。
