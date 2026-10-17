# TIR 端到端验证记录

2026-10-16 完成独立 TIR 示例的真实 GPU 闭环验证。

运行入口与配置见 [示例说明](../examples/tir/README.md)。提交命令：

```bash
bash examples/tir/run_slurm.sh
```

## 实际运行

| 项目 | 结果 |
| --- | --- |
| Slurm job | `979072` |
| Reservation / 节点 | `rsi` / `slurmd-7` |
| 状态 / 退出码 / 用时 | `COMPLETED` / `0:0` / `00:04:56` |
| 资源 | 2 GPU、16 CPU、128 GiB |
| AReaL | `96b557f3a5bc3859d61ff19c3c7d931d75f8c955`，v1 FSDP + SGLang |
| 基模型 | 本地 `Qwen__Qwen2.5-Math-1.5B` |
| 数据 | 程序合成 train/dev/eval = 32/8/8，种子 17；评测种子 42 |
| 优化 | 2 个真实 GRPO step，每 batch 4 题，每题 4 次采样 |
| 参数变化探针 | 最大绝对变化 `3.0517578125e-05` |
| 固定 8 题正确率 | 基线 `1.0`，训练后 `1.0` |
| 候选选择 | 保留基线；仅在分数严格提高时选择训练候选 |

checkpoint 已保存并由独立 SGLang 评测进程重新加载。保存了 16 条评测轨迹，
其中包含 2 次真实工具调用及对应返回。此实验验证训练闭环，不足以证明训练收益。

实际使用的 RSI 工作树包含本次未提交实现。源码身份由归档和清单共同记录：

- RSI HEAD：`1ac10eb9b151f39c9ca54b15c4ea67c06b0cde8c`。
- RSI 源码清单 SHA-256：`dea6ca8c6fa86f242ad5939b0a21388a9ba5d8eacb88694414ea375e766a1e6f`。
- AReaL 源码清单 SHA-256：`e3f0d79c3e8e0e5d5879e19e79f093095cbfba57ad371abdf5ec12835c3d9521`。
- harness 版本：`harness-2389ac40a2c32e07de32`，两次评测使用同一版本。
- 训练模型版本：`model-c5bc93eb06538134dee5`，关联其基模型和 checkpoint。

## 验证证据

完整产物位于
[`runs/tir/tir-20261016T151812-2f6c9136/`](../runs/tir/tir-20261016T151812-2f6c9136/)。
该目录由本次运行生成，不纳入 Git。

- [对比报告](../runs/tir/tir-20261016T151812-2f6c9136/report.md)、
  [完整对比与版本引用](../runs/tir/tir-20261016T151812-2f6c9136/comparison.json)。
- [独立产物审计](../runs/tir/tir-20261016T151812-2f6c9136/audit.json)、
  [审计脚本](../runs/tir/tir-20261016T151812-2f6c9136/audit-script.py)。
- [Slurm 记账](../runs/tir/tir-20261016T151812-2f6c9136/slurm-accounting.txt)、
  [完整开发检查日志](../runs/tir/tir-20261016T151812-2f6c9136/checks.log)。

独立审计重新计算了逐题汇总，核对模型/harness 继承关系、数据去重与 split 隔离、
全部任务终态、轨迹查询及工具调用/返回配对；还逐文件检查了源码归档和模型权重的
SHA-256。运行前后源码清单一致，两个模型目录中的全部文件均与记录摘要一致。

## 开发检查

```bash
PATH="$PWD/runs/test-tools/node_modules/.bin:$PATH" make check
```

使用独立安装的 Codex CLI 0.162.1，未替换本机原有的 CLI：51 项 Python 测试通过，
5 个 subtest 通过，3 项 Node UI 测试通过，Ruff 与 Mypy 通过。
Linux 下跳过 1 项 macOS 专属的休眠断言集成测试。

测试覆盖真实子进程的故障、超时和中断、拒绝缺题/重复题的评测结果、拒绝过期/不完整
checkpoint、轨迹不可覆盖、数据可复现、重复运行隔离和未提交源码归档。
原有 `/rsi` 原生历史继承、持续执行与 TUI 测试也通过；本例尚未接入 `/rsi`。
