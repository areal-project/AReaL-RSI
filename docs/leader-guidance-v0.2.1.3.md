# v0.2.1.3：及时报告与客观分享

基于 v0.2.1.2，在相同研究身份和消息通道上，调整报告与分享的方式。
工作台选项为 `passk_guided_v0213`，模块为：

```json
{
  "mode": "passk",
  "collaboration_modules": [
    "leader_guidance",
    "problem_sharing",
    "researcher_profiles",
    "objective_sharing"
  ]
}
```

## 这次改变了什么

- 研究员得到重要结果后，先调用 `submit_report`，再继续研究。报告只需说明做了什么、
  观察到什么并附记录；无需等完整解释或下一步计划。自己的进展说明不等于发给了 RSI。
- Leader 分享来源做法、观察结果、适用条件和原始证据。自己的推测另作说明；
  结合接收者的困难与研究偏好选择有用的信息，不把别人的局部成功当成全队必须采用的路线。
- 每条指导消息附上来源 agent ID / 身份、接收者 ID / 身份和研究偏好提醒。
  身份来自启动时冻结的配置，不靠 leader 填写。普通建议的来源为 RSI。
- 研究员结合自己的证据判断，可以借鉴局部方法、提出不同解释或继续原路线；
  来源身份不代表权威，自己的身份也不限制合法方法。

新角色正文和两个工具的说明一起更新，避免工具仍要求“下一步指令”或详细报告。
四份身份正文保持 v0.2.1.2 原样；上述共同要求放在持续保留的研究员角色 prompt 中，
不在每份身份文件里重复。原有身份注入与 compact 恢复流程继续使用。

## 实现边界

`ObjectiveSharingActions` 继承 `ProblemSharingActions`，只替换工具说明和补充消息中的身份。
原有报告、消息持久化、证据引用、收件箱投递、原始轨迹查阅和调度流程继续使用。
重要进展由研究员判断和报告，没有增加自动检测突破、自动向 leader 推送结果的逻辑。

未解决的问题仍只允许增加一名接收者；有新证据确认解决后可扩大分享。
候选和作业权限、跨 run 隔离、个人时间预算、最终选优与评测都不变。
旧版本选项、prompt 和默认选择保持不变，可继续做对照。

## Prompt 审阅

- [RSI 完整正文](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.3.md)
- [Researcher 完整正文](../src/areal_rsi/agents/research/leader_guidance_v0.2.1.3.md)
- [分享工具说明](../src/areal_rsi/agents/rsi/advise_researcher_v0.2.1.3.md)
- [报告工具说明](../src/areal_rsi/agents/research/submit_report_v0.2.1.3.md)
- [共享消息模板](../src/areal_rsi/agents/rsi/shared_experience_v0.2.1.3.md)

## 下一轮观察

这次修正降低了报告负担并明确发送时机，但是否及时报告仍取决于 agent 行为。
实验时应核对重要结果到 `submit_report` 的间隔，以及报告到 leader 收到消息的间隔，
区分漏报和消息投递问题。同时看分享是否带有适用条件、研究员是否独立判断，
而不是只比较四人的最后分数是否接近。

可复用的三题启动配置见 [KernelBench example](../examples/kernelbench_artifacts/v0.2.1.3/README.md)。
