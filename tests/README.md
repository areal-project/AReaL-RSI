# 验证

`make check` 执行精简后的离线场景、Node UI 测试、Ruff 和严格 Mypy：

- `test_plugin.py`：请求去重、重启恢复；真实 MCP/Codex 的上下文交接、工具执行及宿主断开后继续完成 Goal。
- `test_research.py`：真实 CPU artifact 的候选、缓存、冻结选择、独立验收与篡改拒绝；慢快照中的查询和取消。
- `test_core.py`：项目配置/密钥隔离，经验按主题更新并拒绝过期写入。
- `test_architecture.py`：API、核心、执行/存储与 UI 的依赖方向。
- `ui/dsh/test.mjs`：原生组件渲染、输入和问答；不再保留另一套 Python PTY 测试。

模型端使用本地 Responses fixture，不访问付费 API 或正常 Codex 登录。需 Node >= 22、
Codex >= 0.162.1；本地缺少 Codex 时明确跳过对应场景，CI 安装固定版本并拒绝任何跳过。
这是一组主流程回归，不再逐项覆盖 Search/TIR 算法、Slurm 故障排列或各平台休眠行为。
真实 GPU/API 效果、集群环境和插件首次下载安装仍需独立冒烟测试。
