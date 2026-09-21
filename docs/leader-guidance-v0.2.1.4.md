# v0.2.1.4：定期观察与人设资料

基于 v0.2.1.2，保留原来的完整独立探索、报告格式、非阻塞指导、证据引用和问题传播限制。
没有继承 v0.2.1.3 的 `objective_sharing`、角色改写及消息包装。

## 两个独立增量

- `team_review`：约每 600 秒向 leader 的现有 inbox 发送一条检查提醒。正在运行的 leader 在消息安全边界收到，空闲的 leader 被唤醒。最多保留一条尚未送达的提醒，暂停和结束时不发。只提醒观察，是否指导、指导谁仍由 leader 根据原始轨迹判断。
- `profile_resources`：每个身份带有 `AGENTS.md` 和知识；skills 可选。启动时将必读正文装配进原有固定身份指令，同时将文件复制到该研究员工作区的 `researcher/`。当前只有 GPU 执行与编译身份带有 `kernel-profile` skill。

两项可以独立选择；完整预设是 `passk_guided_v0214`。底层仍使用 AReaL Agent Runtime，未添加另一套模型或工具执行循环。

## Prompt 审阅

- leader 主体沿用 [v0.2.1.1 正文](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.1.md)和 [v0.2.1.2 身份说明](../src/areal_rsi/agents/rsi/researcher_profiles.md)，只增加 [定期观察](../src/areal_rsi/agents/rsi/team_review.md)。提醒正文见 [team_review_reminder.md](../src/areal_rsi/agents/rsi/team_review_reminder.md)。
- researcher 主体沿用 [原正文](../src/areal_rsi/agents/research/leader_guidance_v0.2.1.1.md)，个人固定指令由 AGENTS.md、[资料说明](../src/areal_rsi/agents/research/profile_resources.md)和必读文档构成。
- [综合研究员](../src/areal_rsi/agents/research/resources/general/AGENTS.md)及其知识属于通用能力。
- 三份领域身份、知识和 profiling skill 位于 [KernelBench example](../examples/kernelbench_artifacts/v0.2.1.4/profiles/)，核心协调代码不包含 GPU 或题目规则。

## 资料生命周期

任务配置传入身份的 `resources`（相对文件名到文本的映射）与 `required_reading`。
注册时已将本地文件读入配置；研究会话准备时计算内容摘要，保存到 `coordination/researcher-profiles.json`。
每人的原有固定身份指令直接包含 AGENTS.md 和必读知识、skill 正文，脚本按需执行。
这保证首个模型调用实际收到材料，而不依赖某个后端是否自动发现磁盘上的 AGENTS.md；不能据此声称模型一定理解或采用了材料。

文件放在 `workspace/researcher/`，不覆盖被研究项目原有的根 AGENTS.md，也不让导入的同伴资料改变本人的固定身份。
正文随原有固定指令保留；恢复状态只带身份和资料摘要，不在每次状态更新中重复携带整库。
必读内容控制在核心知识与一个 skill；详细脚本保留在文件中。阅读计入原有效时间。
运行中的研究按冻结配置恢复，修改仓库资料不会热更新旧研究。

## Profiling 边界

提供可直接通过 GPUQ 运行的 PyTorch Profiler 脚本，以及有 kernel 过滤和预热边界的 Nsight Compute 入口。
脚本检查工具实际版本执行情况，保留工具缺失/权限错误，不在实验期间安装工具。
诊断遵守公开任务状态、输入和精度要求；正式成绩仍由正常评测产生。资料仅整理通用方法和官方文档，不含历史 run 的题目答案或最优配置。

旧版本的协作预设和 prompt 指纹不变。验证重点是资料进入首次 Runtime 输入与恢复、提醒合并及暂停行为，以及 profiling 脚本可实际运行。

## 本次验证

2026-09-21：15 项相关测试通过，源码及新增测试的类型检查通过；11 个已有预设的 manifest 与修改前一致。
通过同一 GPUQ 环境运行独立的 L2P71 诊断作业，PyTorch Profiler 成功输出 CUDA kernel 时间。
该作业使用公开参考模型作为脚本验证输入，不是研究实验，也不产生可比较的候选成绩。
实际环境中的 `ncu` 是缺少安装主体的入口，`nsys` 不存在，因此只验证了 PyTorch 路径，未验证 Nsight 硬件计数器采集。

本地 8198 工作台已加载新预设，原有 85 项研究状态文件保持不变。本次没有启动新研究。
