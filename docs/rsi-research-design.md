# RSI 多 Agent 研究设计

系统支持开放研究：模型决定研究方法，代码负责状态、消息、执行和恢复。启动方式见[README](../README.md)。

## 职责与流程

| 角色 | 职责 |
| --- | --- |
| Interaction | 与用户讨论需求，整理资源和目标等硬限制，以及研究建议、环境用法等自然语言说明 |
| RSI | 调研资料、制定计划、分配任务、处理报告、共享经验、选择结果；需要用户处理阻碍时直接提问 |
| Research | 在一条方向上连续研究，完成本轮阶段问题后汇报，等待 RSI 的下一条指令 |
| Task | 仅在优化 harness 时需要，使用固定目标模型和当前候选 harness 执行题目 |
| 调度与执行系统 | 保存记录、投递消息、管理资源与作业、验证结果、处理暂停和恢复 |

```text
用户与 Interaction 确定需求
            ↓
    RSI 调研、规划并派发任务
            ↓
  Research 自主尝试、实验和改进
            ↓
   报告具体结论，结束回合等待
            ↓
RSI 决定继续、调整、共享、合并或停止
            ↓
   下一项任务进入原 Research 会话
```

RSI 自主决定分工与并行度，候选实现、调参和反复实验交给 Research。派单围绕有待判断的阶段问题，说明范围和汇报点；不按工具次数机械切分，也不默认等整条方向做完才交流。各方向异步推进，RSI 收到报告即可处理。

`max_agents` 约束同时存活的 Research Agent（包括等待回复、暂停或待重试），不计 RSI 和 Interaction，也不限制累计创建数。RSI 可随研究进展新增或替换方向；`stop_researcher` 释放名额，历史会话、报告和产物继续保留，新方向可引用这些记录。同一方向的后续任务用 `assign` 延续原会话。

RSI 首次派发前必须结合目标、约束和已有材料做实质调研；允许联网时主动搜索并阅读原文，形成有依据的方向。调研足够支持下一步就派单，其余调研与执行并行。遇到持续受阻、改进停滞或证据矛盾时再针对性调研。优先验证影响整体结果的假设，及时整合互补成果；报告足够决策时先回复，避免等待无关记录或全员汇报。资料来源与局限存入 run 内的 evidence，不规定具体研究方法或搜索次数。

Research 的正常最终回复会自动归档为报告，说明做了什么、具体结果、问题与局限、下一步建议。简单任务几句话，复杂任务几段；详细材料用引用关联。报告后必须等 RSI 回复才能开始下一项任务。RSI 用 `message_researcher` 补充当前任务，用 `assign` 回复报告并安排下一轮。

## 会话、等待与恢复

每个 Agent 保持持久会话和工作区。RSI 的任务作为 Research 的 user 消息追加，不逐轮新建会话。报告、用户问答和作业完成事件都先落盘，再触发调度；等待期间没有模型请求。

RSI 可以在其他方向运行时继续调研和协调；没有可推进事项时等待。只有用户能解决的阻碍，通过 `ask_user` 说明现状和需要的帮助，用户回复后在原会话继续。

消息、模型响应、工具效果和作业具有稳定身份。临时 API 故障自动重试；暂停和重启沿用已有会话与完成记录，结果不明的外部操作先核查，不自动重复提交。compact 根据上下文用量生成摘要，结合最新系统状态恢复；完整原文继续归档。

## 数据与验证

每个 run 集中保存自己的需求、节点、报告、会话、消息、事件、证据、作业和结果：

```text
runs/<run_id>/
  run.json
  briefs/ assignments/ iterations/ reports/
  evidence/ candidates/ verifications/
  agents/<agent_id>/             # 状态、连续会话和工作区
  coordination/ messages/ events/ jobs/
  execution-inputs/ execution-outputs/
```

`ResearchIteration` 是通用研究节点，保存任务、状态、父节点、报告、材料引用和时间；不规定任务类别，不要求每次都有候选或 verify。没有起点可以直接研究，不能把后来产生的候选冒充 baseline。

需要保存产物时创建不可变 `CandidateSnapshot`；`VerifiedResult` 绑定冻结候选、比较规则、阶段和结果。有效且可比较的 search 成绩用于选优，基础设施故障不计为模型成绩；有 final 要求时，对选定版本完成最终验证后才能成功结束。未达标停止时保留最佳结果并说明原因。

执行命令可选配 `auto_verify_output`（作业输出中的产物目录）。成功且产生文件时，系统自动保存不可变产物、关联原作业及研究节点，再调用原验证器更新成绩。产物可以是数据、模型、文档或代码；未配置或没有产物时不额外验证，普通回执不能冒充可信成绩。记录具有稳定 ID，重放不会重复发布。Interaction 已导入完整执行配置时，系统在研究开始计时前校验、冻结并准备初始材料，无需 RSI 重复接入。

任务执行适配器可以使用 `SolutionVersion` 组合 Model / Harness / Artifact。研究编排只使用通用任务、报告、候选和验证记录；旧固定工作流与 EvolutionResult 已移除。

共享经验限定在 run 内，由 RSI 发布并保留来源和局限。不可变内容可在根目录 `content/` 去重，其他 run 的 memory 不会自动混入。工作台消费结构化状态和事件，展示研究图、任务报告、作业、成绩与等待原因。

验证器由操作员配置，候选不能修改计分程序或读取私有验证材料。隔离能力取决于执行后端：DSH shell 使用宿主账户权限，可信计分协议本身不等于进程沙箱。用户约定的资源、修改范围、模型和验证条件在开始或接入冻结后保持不变。

固定实验可通过 API 直接提交 `ResearchConfig`，与 Interaction 共用运行系统。可选的 `finalizer` 在探索结束后产出冻结候选，再由原验证器评测并归档，不调用研究模型。`metadata.time_limit_scope` 默认 `total`（包含收尾），可选 `exploration`（仅探索限时，最终验证另计）；后者向收尾命令传入 `RSI_EXPLORATION_FINISHED_AT`，供适配器按截止时刻选择产物，禁止用最终反馈继续优化。所有阶段记录仍属于同一 run，工作台按研究创建时间倒序展示。

## 代码位置

- `agents/*/prompt.py`：中文角色职责；`modules/responses.py`：会话、重试和 compact。
- `api/research.py`、`base/runs.py`、`coordination/store.py`：数据记录与 run 归属。
- `coordination/`：需求交接、任务与消息、调度、工具和验证；`execution/`：后台作业。
- `research_app.py`、`ui/workbench*`：应用入口与本地工作台。
- `adapters/`、`examples/`：任务接入；`app.py`、`remote_app.py`、`workflows/`：固定工作流。
