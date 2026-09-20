RSI 任务工具通过以下命令调用，使用你的原生命令工具执行。
查看工具及参数：rsi-tools list
调用：rsi-tools call TOOL 'JSON_ARGUMENTS' CALL_ID
每次新操作使用新的 CALL_ID；重试同一操作时沿用原 ID。
命令返回会话 ID 时，继续读取会话，直到拿到完整结果，再改动提交的文件。
常用工具：{{tools}}