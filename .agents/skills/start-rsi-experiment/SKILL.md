---
name: start-rsi-experiment
description: 交互式配置并启动 AReaL-RSI 实验。用户要求新建、配置、运行或启动 RSI/AReaL-RSI 实验时使用；先收集必要实验参数，汇总并等待明确确认，再生成隔离配置并启动 Run。
---

# 启动 AReaL-RSI 实验

严格按“读取基线 → 收集参数 → 校验 → 汇总确认 → 启动 → 回报”的顺序执行。用户明确确认前不得启动进程或提交 Slurm 作业。

## 1. 读取固定基线

1. 用 `git rev-parse --show-toplevel` 定位仓库根目录，并确认其中存在 `pyproject.toml`、`config/rsi.yaml` 和 `config/areal.yaml`。
2. 完整读取 `config/rsi.yaml` 及其引用的 AReaL、EnvArena 配置。以这些文件为默认值，不凭记忆重建配置。
3. 检查仓库根目录的 `.env` 是否存在，以及 `ENVARENA_API_TOKEN` 是否已设置。只报告“已配置”或“未配置”，绝不读取后向用户展示、写入日志或放进命令行。
4. 默认保持下列部署配置不变，除非用户主动要求修改：
   - `run.run_root`
   - AReaL `cluster.fileroot` 和 `name_resolve` 根目录
   - EnvArena URL、token 环境变量名和客户端参数
   - AReaL 路径、镜像、Slurm 资源和 Researcher 配置
   - 训练方法 `sft` 与调度方式 `slurm`

## 2. 收集必要实验参数

一次集中询问尚未由用户给出的字段；用户已经提供的内容不要重复询问。

- `experiment_name`：本次 AReaL 实验的唯一名称，只能包含字母、数字、点、下划线和连字符。
- `initial_ckpt`：初始 checkpoint 的绝对路径。
- `benchmark`：用于无 Skill 轨迹分析和有 Skill 蒸馏的训练 benchmark 引用。
- `holdout_benchmark`：每个 iteration 开始时用于判断停止条件的 holdout benchmark 引用。允许与训练 benchmark 相同。
- `harness`：EnvArena harness 引用。
- `max_iterations`：大于等于 1 的最大迭代数。
- `target_mean_reward`：达到后停止的 holdout 平均 reward。

当前基线只支持 SFT 端到端启动，不要把 `method` 作为常规问题。用户主动要求其他方法时，先说明当前启动模板仅支持 SFT，不要静默降级或伪造支持。

## 3. 启动前校验

1. 校验字段非空、数值范围和实验名格式。
2. 在实际执行环境能访问共享存储时，确认 checkpoint、AReaL 目录、训练模板和 Researcher 参考 JSONL 存在。
3. 如果当前机器不能访问配置中的 `/storage/openpsi/...`，不要误在本地启动；说明需要在集群仓库中执行，或切换到用户已经授权的集群终端。
4. 不要因为固定配置已经有默认实验值就跳过收集上述实验参数。

## 4. 汇总并等待确认

用一个紧凑表格展示：实验名、checkpoint、训练 benchmark、holdout benchmark、harness、SFT、最大迭代数、目标 reward、Slurm、Arena backend，以及固定的 runs/experiments 目录。

另外注明：

- `ENVARENA_API_TOKEN` 是“已配置”还是“未配置”，不得显示值。
- Arena backend 如果仍为 mock，要明确标成 mock。
- 将从固定基线生成本次启动专用配置，不会覆盖基线文件。

以“请回复确认启动，或指出要修改的字段”结束这一阶段。没有收到明确确认就停止，不执行辅助脚本；模糊回复不视为确认。

## 5. 确认后启动

选择能导入 `areal_rsi` 且依赖完整的 Python。优先使用当前虚拟环境，其次使用仓库 `.venv/bin/python`；集群已知环境可使用 `/home/admin/.venvs/areal-rsi/bin/python`。

执行 `${SKILL_DIR}/scripts/launch.py`。Codex 中 `${SKILL_DIR}` 表示本 Skill 目录；如果宿主没有展开该变量，使用当前 `SKILL.md` 所在目录的绝对路径。

在集群上按项目约定使用 `sudo`：

```bash
sudo /home/admin/.venvs/areal-rsi/bin/python <skill-dir>/scripts/launch.py \
  --repo-root <repo-root> \
  --experiment-name <experiment-name> \
  --initial-ckpt <checkpoint> \
  --benchmark <training-benchmark> \
  --holdout-benchmark <holdout-benchmark> \
  --harness <harness> \
  --max-iterations <count> \
  --target-mean-reward <reward> \
  --launch
```

逐个参数传递，不拼接未经引用的 shell 字符串。不要把 token 作为参数或环境变量前缀写进命令。辅助脚本会加载仓库 `.env`、生成独立配置、后台启动 Controller，并输出 JSON receipt。

## 6. 回报结果

从 receipt 回报：

- `run_id` 和进程 PID
- Run 目录
- 本次 resolved RSI/AReaL 配置路径
- 启动日志路径
- 当前状态或启动失败原因

如果启动失败，只读取日志尾部排障；不得展示凭据，也不得未经再次确认而改变实验参数。环境或依赖修复可以在不改变已确认实验语义的前提下完成后重试。
