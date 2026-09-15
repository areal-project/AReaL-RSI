# 配置 RSI 和 Research Agent

复制 `research-example/` 为一个新方法目录，在 `experiment.yaml` 中填写实际研究任务，
并加入已有研究配置所需的起点、作业命令和验证器。示例目录只演示接口，不是 TB 跑分配置。

```yaml
brief:
  objective: 你的研究目标
  instructions: 任务范围和要求
agents:
  rsi:
    base: default
    prompt: rsi/prompt.md
    skills: rsi/skills
  research:
    base: default
    prompt: research/prompt.md
    skills: research/skills
```

从仓库中运行（配置路径也可以是绝对路径）：

```bash
uv run --env-file .env --locked rsi research --config experiments/research-example/experiment.yaml
```

配置支持 YAML 和 JSON。`prompt` 和 `skills` 都相对于配置文件所在目录解析，
与启动时的工作目录无关，也接受绝对路径。模型连接仍通过原有配置和环境变量设置。

- `base: default` 选择当前角色的默认 Agent。省略 base 也使用 default，目前不接受其他 base。
  只填写需要替换的组件，未填写的组件继承默认实现和配置。
- `prompt` 完整替换新版持续会话中当前角色的默认 prompt，不做追加。
  自定义 prompt 应说明该角色的方法和职责，例如 Research 何时汇报、RSI 如何派单。
  工具说明和每轮系统状态仍由框架提供。
- `skills` 可单独配置，不提供 `prompt` 时保留默认角色 prompt。
  子目录中的文本文件会列在对应角色的 prompt 中，Agent 通过 `read_agent_skill(path)`
  按需读取，例如 `evidence/SKILL.md`。引用文件的路径同样相对于 skills 根目录。
  支持 UTF-8 文本资料及脚本文本；该工具不会执行脚本，不支持二进制资源。
- `agents.rsi` 和 `agents.research` 分别配置，省略某个角色时该角色保持默认行为。
  两个角色即使使用同名 skill 路径，也只会读取各自快照中的内容。
- 配置不修改 Interaction 或 Task Agent 的 prompt。
  该入口不改变旧固定工作流的 `propose()`，也不替换 RSI 调度器的 Python 实现。
  可以通过 RSI prompt 调整模型如何分工、继续派单和整合成果。

加载配置时会读取并冻结方法内容，创建 run 后保存到
`runs/<run_id>/coordination/config.json` 的 `agents.<角色>.snapshot`，包含实际生效的角色 prompt、
skills 内容及 SHA-256 摘要。实际发送的完整 prompt 仍存放在每个会话的 `prompts/` 目录中。
恢复 run 和继续派单都使用快照，原始实验目录修改或删除不会改变已有 run。
要测试新版本，请修改文件后重新加载配置并创建新 run。
方法文件不会复制到候选工作区，因此不会被当作 Task Agent Harness 或待验证产物提交。

文件缺失、空 prompt、空 skills 目录、符号链接以及超出 128 个 skill 文件或总计 1 MB
文本的配置会在加载时失败。通过 API 直接提供配置时，文件路径使用绝对路径。
`snapshot` 是系统生成的运行记录字段，不需要手工填写。

当前可替换的组件是 prompt 和 skills；Harness、memory 以及 Interaction 的覆盖尚未开放。
这些未支持的字段会报错，不会被静默忽略。默认 Harness 和 memory 实现不变，运行数据仍按 run 隔离。
