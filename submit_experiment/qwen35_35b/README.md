# Qwen3.5-35B-A3B 实验启动

本模型的两种训练方式独立维护：

- [rl/](rl/README.md)：Arena RL，入口为 `rl/submit.sh`，使用 controller 与共卡 actor/rollout。
- [sft/](sft/README.md)：纯文本 JSONL SFT，入口为 `sft/submit.sh`，单个 Slurm GPU 作业内运行 torchrun。
- `profile_config.py`：两种入口共享的 YAML 重复键检查与配置合并，不包含训练逻辑。

两种入口均支持 experiment/trial 命名、显式 reservation、`--dry-run` 和同步 CPU
`--check-only`。省略 `--reservation` 即公共池；日志和 checkpoint 使用 AReaL 默认目录，
不在代码目录创建 `runs/`。

凭据优先级：`--credentials-file` > 非空 `CREDENTIALS_FILE` > 当前模式目录的
`credentials.env` > 模型父目录的 `credentials.env`。原模型目录中的真实凭据无需移动，
已由 `.gitignore` 忽略，不复制到运行快照。SFT 无需 Arena 凭据；W&B key 和 URL
均非空时启用 online。各模式目录均有 `credentials.env.example`。
