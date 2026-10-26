---
name: hamlet-agent
description: 当需要查询或管理同组 Agent 时使用，提供 Hamlet 的实例身份、CLI 和管理接口说明。普通任务执行不需要加载管理操作。
---

# 同组 Agent 管理

Hamlet 是当前多 Agent 任务的会话与消息管理服务。admin 可以管理同组 worker；worker 通常不需要与该服务交互，专注完成分配的任务。

admin 需要查询或管理同组 Agent 时，阅读 [管理操作](references/admin.md)，其中包含 CLI 入口、凭据使用方式和接口参考。当前身份可通过该文档中的 whoami 查询。

消息的路由、回复、排队与拒绝确认规则见相应工具描述。
