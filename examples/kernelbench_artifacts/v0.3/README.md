# v0.3：任务选择人设与人设自演进

每个人设包含身份 prompt、`knowledge/core.md` 和专属 `skills/`。默认提供综合、算法、访存、
GPU 执行四种人设；GPU 人设包含完整的 kernel-profile 方法与可执行诊断脚本。
源身份沿用 `src/areal_rsi/resources/researcher_profiles/`，知识和技能放在
`src/areal_rsi/resources/profile_library/<id>/`。

## 生命周期

1. 创建研究时，从内置资产与共享库的最新修订加载并冻结本次可选人设。
2. Leader 读取任务和人设目录，通过 `start_researchers(profile_ids, reason)` 选择团队。
   数量等于 `max_agents`，允许重复；没有固定配额或默认轮换。启动后选择不能改变。
3. 各 researcher 的 system prompt 包含身份和必读知识、skill 正文，资源文件落在其工作区
   `researcher/`。每人继续独立完成整题，使用原有报告与定向分享规则。
4. 探索结束且自动最终验证完成或失败后，同一 leader 模型进入新的复盘会话。
   它阅读本次轨迹和证据，检查身份、知识与技能，调用 `finish_profile_review` 提交更新。
   可将耗时探索后才找到的方法写成技能，也可明确记录无需更新。
5. 更新写入共享库，后续新建研究自动加载。已经创建的 run 继续使用其冻结版本。

## 运行

沿用已经配置模型连接和 GPU 排队环境的工作台：

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/v0.3/experiment.json --port 8198
```

此命令会启动真实实验。只准备配置用 `--prepare-only`。查看 run 的 API 字段
`profile_selection` 和 `profile_evolution`；后者为 `completed` 后再创建下一轮实验，
即可验证更新的复用。`conflict` 或 `failed` 表示需要检查复盘记录，不代表研究成绩失败。

通用 ResearchConfig 可选择：

```json
{
  "mode": "passk",
  "collaboration_modules": [
    "leader_guidance", "problem_sharing", "researcher_profiles",
    "profile_resources", "profile_evolution"
  ],
  "profile_library_dir": "./persona-library"
}
```

不要同时指定固定 `researcher_profile_ids` 或 `metadata.researcher_profiles`。
`profile_library_dir` 在文件配置中相对该配置解析，HTTP API 使用服务器上的绝对路径。
省略时使用工作台 `<data_root>/profile-library/`，不同任务共享；隔离实验条件时指定不同目录。

## 记录与原型边界

- 共享库：`<profile_library_dir>/library.json`，包含最新完整资源和按 run 保存的复盘记录。
- 冻结目录：`<run>/coordination/profile-library.json`。
- 所选人设：`<run>/coordination/selected-profiles.json`，选择理由在 state 的 `profile_selection`。
- 复盘：`<run>/profile-evolution/{input,prompt,proposal,review,response}.json`；原始会话在
  `<run>/agents/rsi/sessions/profile-review-v03/`。

复盘最多五分钟，独立于 researcher 探索预算；不改变本次候选或成绩。
用户主动停止、尚未启动 researcher 时跳过复盘。断线/进程中断可随 `auto_resume` 恢复，
已生成提案会直接重放提交，已完成发布不会重复应用。模型失败会记为 `failed`，不无限重试。

更新支持新增/替换身份 Markdown、知识 Markdown、skill Markdown/Python 文件；不自动删除文件、
新增人设 ID 或执行新脚本。提案需要本 run 的真实证据引用。持久化采用锁和原子写入，
同一人设被并发更新时保留整个冲突提案，不覆盖先发布的版本。人工核对后可编辑共享库合并。
这是基于 leader 判断的原型，尚无自动验证“新知识正确”或“下一轮成绩提升”的能力。
基准隔离应使用独立人设库；只保留通用知识、方法与条件，不沉淀题目完整答案或隐藏测试。
