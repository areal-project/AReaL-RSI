# Admin 接口参考

统一格式：`"$HAMLET_NODE" "$HAMLET_CLI" call 工具名 'JSON'`。多行内容使用 `--stdin` 或 `--file`。HTTP 等价入口为 HAMLET_URL 下的 POST /api/tools/工具名，使用环境中的 HAMLET_TOKEN 作 Bearer 认证。

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| whoami | {} | 实例 ID、角色、group、权限、共享目录 |
| group_agents | {} | 本组逻辑 Agent 和实例 |
| agent_get / agent_list | {id} / {} | 查询组员配置 |
| instance_get | {id} | 查询本组实例、pending_messages、runs |
| run_events | {id,limit?,offset?,order?} | 查看本组运行日志，limit 为 1–500 |
| message_to | {target,prompt?,system_prompt?,vars?,dedup_key?,dedup_window?} | 提交消息，可能因目标积压而拒绝 |
| message_list | {from_id?,target?,status?,limit?,offset?,order?} | 查询本组消息，包括 rejected 记录 |
| message_force_push | {id} | 确认推送自己发送的 rejected 消息 |

message_to 的 prompt 替代目标实例默认任务模板；省略时渲染 prompt_tpl。system_prompt 仅在新会话替代 system_prompt_tpl，resume 会忽略。直接传入的文本不做模板替换，vars 只用于模板。dedup_window 支持 24h、7d 等时长。去重按发送者、目标和键隔离。角色名解析为逻辑节点，角色名#序号解析为真实实例；使用已记录的 ID 可明确路由。

成功返回 JSON；CLI 失败退出码 1，stderr 为 {error,details}，HTTP 使用对应状态码。积压拒绝返回 409：details 包含 code=target_queue_overloaded、message_id、target_id、queued_count、threshold=5、status=rejected 和 confirmation 调用参数。强推成功返回原消息，其状态变为 queued；重复强推返回 409，未知 ID 返回 404，强推其他发送者的消息返回 403（跨组目标不可见）。

Admin 还可管理本组 worker：agent_create、agent_update、agent_delete、instance_archive、instance_renew_resident、run_stop、doorbell_control。涉及创建或更改配置时，从 `$HAMLET_SKILL_DIR/references/configuration.md` 阅读选项；Owner 专用的 group 管理和全局消息重试不向后台实例开放。
