# 在其他 benchmark 上运行 v0.2.1.2

前提：你的 bench 已有可运行的 RSI `ResearchConfig` JSON，包含任务、环境和评测命令。
人设只改变研究偏好，不负责接入新的评测环境。

## 1. 让 Codex 生成人设

在本仓库中告诉 Codex：

> 请使用 `.agents/skills/rsi-researcher-profiles/SKILL.md`，根据 `<bench 说明或目录>`，
> 为 v0.2.1.2 准备 1 名综合研究员和 3 种研究偏好，并更新我的 `<research.json>`。
> 先准备配置，不启动实验。

生成的资产放在 `assets/researcher_profiles/<bench>/`。每份文件就是一个带名称和简短提醒的 prompt：

```markdown
---
name: 结构与算法研究员
reminder: 优先检查问题结构与算法选择，并验证完整任务的收益。
---

你是结构与算法研究员，独立负责整个任务。先理解问题结构和主要成本，再寻找更合适的算法或计算组织。

这是研究偏好，不是方法限制。你仍需要独立完成调研、实现、验证和持续改进，可以根据实验更换方向。
```

## 2. 选择人设并启动

在原有配置中设置下面这些字段。假设配置与 `assets/` 都在仓库根目录：

```json
{
  "mode": "passk",
  "collaboration_modules": ["leader_guidance", "problem_sharing", "researcher_profiles"],
  "researcher_profile_dir": "assets/researcher_profiles",
  "researcher_profile_ids": ["general", "mybench/structure", "mybench/implementation", "mybench/measurement"]
}
```

这是配置片段，不是完整任务。将示例 ID 换成生成的 ID，`brief.limits.max_agents` 设为 4，其他字段沿用自己的 bench 配置。顺序对应 Researcher 1–4，每人都独立完成整题。

```bash
uv run --locked python -m areal_rsi.researcher_profiles list --directory assets/researcher_profiles
uv run --env-file .env --locked rsi research --config research.json
```

内置资产有 `general`、`kernelbench/algorithm`、`kernelbench/memory`、`kernelbench/execution`，位于 `src/areal_rsi/resources/researcher_profiles/`。只用内置资产时可以省略 `researcher_profile_dir`。自定义目录中的同名 ID 优先。

创建研究时会将所选 prompt 完整保存进该 run；之后修改资产不会影响已有研究或恢复。HTTP API 也接受这些字段，资产目录请用**工作台服务器上的绝对路径**。旧版 `metadata.researcher_profiles` 内联配置仍可使用；指定资产 ID 时以资产选择为准。
