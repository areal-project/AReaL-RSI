# Research Agent Prompt

- [简略版](research-agent-simple.md)：默认版本，保留研究职责、协作协议、工具边界和诚信要求。
- [详细版](research-agent-detailed.md)：保留上一轮完整草稿，作为详细版本的对照文本。

ResearchConfig 的 `research_prompt_variant` 可选 `simple`（默认）或 `detailed`，在创建研究时配置。
例如：`{"research_prompt_variant": "detailed", "brief": {"objective": "研究目标", "instructions": "研究要求"}}`。

运行时模板位于 `src/areal_rsi/agents/research/prompt.py` 和 `detailed_prompt.py`。
两版均支持 `standard` 与 `codex`，工具说明按实际后端及任务配置填入，诚信声明保留在末尾，不重复追加共享规则。
两版均明确 RSI 可通过 `message_researcher` 在当前阶段补充指导；正式报告后仍须等待 `assign`。
本目录展示 Codex 说明，运行时另有实际工作区及任务特定工具说明。
