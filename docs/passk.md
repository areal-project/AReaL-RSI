# Legacy pass@k

`legacy_passk` 保留当前 `main` 的 v0.1 独立完整探索语义。基线来源为 `4771407`（2026-09-21），不是独立 `v0.1` 分支，也不是 feature/dev 后来的 Researcher goal。

监督者通过 `start_researchers` 一次启动固定 k 个 Researcher。每人获得同一完整 brief、独立会话/工作区与相同起点；禁止读取其他尝试的候选、作业和报告。监督者仅使用 `start_researchers`、`get_state`、`continue_researcher`、`wait`，不拆分任务或提供研究建议。进展报告不会结束尝试，回合结束后在原会话继续。

研究员不具备 `update_goal` 或 run 完成权限。满足正式 search 目标、达到墙钟/个人有效时间限制或用户 stop 时停止；配置了最终确认时由系统对候选进行 final 验证。最终结果和是否达到目标分别记录，不能把结束实验等同于达到目标。

```json
{
  "mode": "legacy_passk",
  "brief": {
    "objective": "独立完成同一任务",
    "instructions": "在各自工作区探索并保存产物",
    "limits": {
      "max_agents": 4,
      "max_nodes": null,
      "max_total_tokens": null,
      "max_verify_calls": null,
      "max_wall_seconds": 1800
    }
  }
}
```

正式评测需另配 verifier 和固定协议身份。开始后 brief 冻结，TUI 可以查看、暂停、恢复和停止，不能向个别尝试发送指导。模型/执行接入和记录格式使用本分支基础设施；旧 run 与旧配置不兼容。个人有效时间规则见 [effective-time.md](effective-time.md)。
