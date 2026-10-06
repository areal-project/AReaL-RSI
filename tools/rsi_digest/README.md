# RSI 调研工具

运行 `.venv/bin/python tools/rsi_digest/digest.py collect` 采集候选。由本任务 Codex 按 WORKFLOW.md 完成四渠道搜索、核验和 report.json 编写，再执行 `render`、`publish`。不是单独执行 Python 就会自动生成 AI 总结。

配置在 config.json；日报、候选、网页证据、去重记录和发布回执在 `.rsi/research-digest/`。Python 3.11+，仅标准库，使用本机 yuque 登录；可选 GITHUB_TOKEN、YUQUE_BIN。API 查询有结果上限，微信及小红书为公开检索。任务依赖本机 Codex、网络与登录态。

2026-09-21：恢复此前缺失的工具，并增加从上次发布日期补采、关注仓库 commit 记录和发布前同标题核对。保留本地工具备份用于后续恢复。未改动 RSI 实验代码。
