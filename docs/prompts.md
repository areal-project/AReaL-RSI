# Prompt 文件管理

Prompt 正文放在 Markdown 文件中，统一使用 `areal_rsi.prompts.load_prompt()` 读取。
Python 负责选择版本、组合需要的部分和传入动态数据。

## 目录

```text
src/areal_rsi/
├── prompts.py                         # 统一读取入口
├── agents/
│   ├── rsi/
│   │   ├── passk.md
│   │   ├── leader_guidance_v0.2.1.md
│   │   ├── leader_guidance_v0.2.1.1.md
│   │   ├── leader_guidance_v0.2.1.3.md
│   │   ├── advise_researcher_v0.2.1.3.md
│   │   ├── shared_experience_v0.2.1.3.md
│   │   ├── leader_guidance_v0.2.2.md
│   │   ├── leader_guidance_v0.2.3.md
│   │   ├── leader_guidance_v0.2.4.md
│   │   ├── leader_guidance_v0.2.4_continue.md
│   │   ├── sharing_quota.md            # v0.2.5 的增量
│   │   ├── intervention_review.md      # v0.2.6 的增量
│   │   └── researcher_restart.md       # v0.2.7 的增量
│   ├── research/
│   │   ├── passk.md
│   │   ├── leader_guidance_v0.2.1.md    # v0.2.2 也使用这份
│   │   ├── leader_guidance_v0.2.1.1.md
│   │   ├── leader_guidance_v0.2.1.3.md
│   │   ├── submit_report_v0.2.1.3.md
│   │   ├── leader_guidance_v0.2.3.md
│   │   ├── leader_guidance_v0.2.4.md
│   │   ├── continue.md
│   │   ├── sharing_quota.md            # v0.2.5 的增量
│   │   └── restart_task.md             # v0.2.7 新会话的任务模板
│   ├── interaction/prompt.md
│   └── shared/                        # 公共规则、工具说明
└── modules/prompts/                   # Runtime 工具调用、恢复、压缩等提示
```

已有其他工作流的模板也使用同一入口，保存在角色目录的 `evolution_*.md` 中。
任务运行时的消息、工具参数定义和状态数据仍由对应代码生成。

## 读取与组合

```python
from areal_rsi.prompts import load_prompt

prompt = load_prompt("agents/rsi/leader_guidance_v0.2.4.md")
```

路径相对于 `areal_rsi` 包，不依赖进程当前目录。Markdown 随安装包发布，
隔离 Runtime 所需的模板也会随 worker 一起复制。

读取时保留全部正文和换行，不自动添加标题、去除空白或附加规则。
可显式替换 `{{name}}` 字段，未提供的字段保留给后续阶段；插入值不会再次被当作模板解析。

```python
prompt = load_prompt(
    "agents/rsi/leader_guidance_v0.2.4.md",
    max_agents="4",
    tool_instructions=tool_instructions,
)
```

版本化的 researcher Markdown 包含该版本完整的诚信要求，便于独立阅读和比较。
Interaction 仍按原流程组合正文、公共 communication、工具说明和 integrity。
没有额外的 Markdown include 语法或模板框架。

v0.2.5 复用 v0.2.4 的完整正文，在工具说明之前加入角色对应的 `sharing_quota.md`。
这些片段由协作模块使用同一读取器组合，不复制整套提示词。
已发布版本使用的片段保持不变；以后需要改变这项策略时另建片段和版本。

v0.2.6 独立复用 v0.2.4，只为 RSI 添加 `intervention_review.md`；researcher 正文不变。
其固定模块列表不包含 v0.2.5 的共享限额。

v0.2.7 也从 v0.2.4 独立出发，为 RSI 添加 `researcher_restart.md`。
研究员角色正文不变；发生重启时，用 `restart_task.md` 组合原任务、恢复起点和本次交接，
不恢复旧会话的对话、compact 摘要或积压建议。三个增量由独立模块提供。

## 新增版本

v0.2.1.1 独立基于 v0.2.1，完整两份正文使用对应的 `leader_guidance_v0.2.1.1.md`。
`problem_sharing` 模块限制未解决问题的传播，解决后可扩大分享；不继承 v0.2.4–v0.2.7 的调度和策略。
实现边界及审阅入口见 [v0.2.1.1](leader-guidance-v0.2.1.1.md)。

v0.2.1.2 复用 v0.2.1.1 的角色正文，为 RSI 补充 `researcher_profiles.md`，
并在每名 researcher 正文开头加入其冻结的身份配置。
v0.2.1.3 保留身份机制，使用新的两份角色正文、报告与分享工具说明、共享消息模板；
全部存为 Markdown。共同的独立判断与报告要求放在角色正文，身份文件不重复维护。
见 [v0.2.1.3](leader-guidance-v0.2.1.3.md)。

v0.2.1.4 基于 v0.2.1.2，复用原角色正文。leader 只增加 `team_review.md`，
定时提醒使用 `team_review_reminder.md`；researcher 以自己的 AGENTS.md 为身份来源，
通过 `profile_resources.md` 加载冻结的必读知识与可选 skill。只有 KernelBench 的
GPU 执行与编译身份配置 profiling skill。见 [v0.2.1.4](leader-guidance-v0.2.1.4.md)。

1. 复制需要修改的角色 Markdown，例如新建 `leader_guidance_v0.2.5.md`。
2. 在 `coordination/collaboration.py` 中注册对应协作模块，通过 `load_prompt()` 选择新文件。
   没有修改的角色继续复用已有文件，无需新增 Python prompt 模块。
3. 保留旧版本文件和配置选项；已有研究继续按启动时冻结的协作配置及会话提示恢复。

本次仅迁移存储与加载方式，原有 prompt 文本及协作指纹保持一致。
历史实验的 `source-lock.json` 记录的是当时源码，保留原样，不随文件重构改写。
