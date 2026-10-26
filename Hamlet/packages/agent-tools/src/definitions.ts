// Model-facing descriptions and argument help live here, separate from transport.
export const messageTools = [
  {
    name: "message_to",
    description:
      "向同组 Agent 发送消息，不接受 mode 参数。fork 模式的逻辑短 ID 创建新会话；真实实例长 ID resume 指定会话；resident 逻辑 ID 创建或复用常驻会话。每条接受的消息触发一轮执行，包括给自己发送；不会插入正在运行的回合。成功回执只表示入队。目标会话已有超过 5 条 queued 消息时，保存 rejected 记录并返回拒绝原因、queued_count 和 message_id；根据任务需要确认继续时使用 message_force_push。被拒绝消息不会自动入队。任务完成后服务不会自动回复发送者。",
    inputSchema: {
      type: "object" as const,
      properties: {
        target: {
          type: "string",
          minLength: 1,
          description:
            "目标逻辑短 ID 或真实实例长 ID。同一任务的追问和结果使用已记录的 working_instance_id 或 reply_to_instance_id；未指定回复目标时使用本消息来源中的真实实例 ID。不要猜测实例 ID。来自 Owner 或事件脚本的消息没有默认实例回复地址。",
        },
        prompt: {
          type: "string",
          description: "本次任务或回复正文；省略时使用目标的默认任务模板。",
        },
        system_prompt: {
          type: "string",
          description: "仅创建新会话时覆盖 system prompt；resume 时忽略。",
        },
        vars: {
          type: "object",
          additionalProperties: true,
          description:
            "目标模板的变量值；prompt 和 system_prompt 中直接提供的文本不做模板替换。",
        },
        dedup_key: {
          type: "string",
          description:
            "可选去重键；窗口内同一发送者、目标和键复用原消息。原消息被拒绝时返回相同拒绝 ID；已失败或取消的消息不会因去重重发而执行。",
        },
        dedup_window: {
          type: "string",
          description: "去重窗口，例如 24h、7d；默认 24h。",
        },
      },
      required: ["target"],
      additionalProperties: false,
    },
  },
  {
    name: "message_force_push",
    description:
      "确认将自己发送、因积压被拒绝的消息加入目标队尾。使用 message_to 错误中的 message_id，保留原目标和内容。仅接受仍为 rejected 的消息；未知、属于其他发送者或已推送的 ID 会报错。",
    inputSchema: {
      type: "object" as const,
      properties: {
        id: {
          type: "string",
          minLength: 1,
          description: "message_to 拒绝回执中的 message_id。",
        },
      },
      required: ["id"],
      additionalProperties: false,
    },
  },
];
