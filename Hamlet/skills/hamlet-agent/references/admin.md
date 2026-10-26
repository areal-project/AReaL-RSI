# Admin 管理操作

admin 可以在本组查询和通信，并管理本组 worker。运行时提供当前实例身份与连接，CLI 调用方式：

```bash
"$HAMLET_NODE" "$HAMLET_CLI" call whoami '{}'
"$HAMLET_NODE" "$HAMLET_CLI" call group_agents '{}'
```

whoami.id / HAMLET_INSTANCE_ID 是当前真实实例长 ID，group_agents 返回组员的逻辑 ID 与实例列表。凭据通过 HAMLET_URL/HAMLET_TOKEN 使用，不打印凭据、不读取 Owner secret 或替换身份重试。认证失败时报告实际错误。

- 查询、创建、更新或停止本组 worker：按需阅读 [接口](api.md)。
- 设置模型、模板、工作目录和 doorbell：按需阅读 [配置](configuration.md)。

这些权限限定 Hamlet API 操作，不构成文件系统沙箱。
