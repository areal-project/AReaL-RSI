# TUI 选择与来源

选择直接移植 DSH 的终端视图，保留原生 Codex 执行内核。

## 比较方法

2026-10-15 检查官方仓库的固定提交，统计对应目录中的源码行；这个指标衡量搬运范围，
并非运行时内存或纯生产代码体积。Codex 存在大量内联测试，不能把目录总行数当作业务代码行数。

| 来源 | 固定提交 | 范围 | 行数 |
| --- | --- | --- | ---: |
| Codex | `3ee47c10513f1bb57d869ea650aba28638e3d11c` | `codex-rs/tui`，1,135 个 Rust 文件，含测试 | 457,704 |
| DSH | `7248b5ec8f8769f882f12fd521504fa48e97bcf3` | `packages/ui/tui`，38 个 TS 文件，含测试 | 18,004 |
| 同一 DSH 版本 | 同上 | `packages/ui/tui/src`，24 个文件 | 7,683 |

Codex TUI 还依赖多项内部 Rust crate；直接分离需要带入明显更大的依赖图。
DSH 的消息、工具卡片、Markdown、问答弹窗可以在不带入其 Agent 执行系统的情况下使用。
因此采用 DSH。这里没有把这两个项目做运行时性能基准，也不据行数断言内存差异。

DSH 当前主线已经删除这个 TUI：删除提交是 `10bb9cbf4a22b5095bb9ff04d1425907af8f08af`。
我们固定到其父提交，而非依赖一个不存在的 npm 包。历史版本采用 **BSD-3-Clause**；
随源码保留该版本许可证，不能套用当前仓库的其他版本许可证。

## 搬运边界

`src/areal_rsi/ui/dsh/upstream/` 保留八个原始源码文件，逐字节不改；
`provenance.json` 记录提交及 SHA-256。原版的 `pi-tui@0.80.7` 编辑器补丁也原样保留，
首次启动时由 `patch.mjs` 应用。`vendor.mjs` 是这些组件的可重建 bundle。

复用：原版消息渲染、流式回复、工具展开、Markdown、主题、问答弹窗，以及它使用的
pi-tui 输入编辑器、补全、粘贴和终端刷新。自己的 `main.mjs` 只拼装组件并转换 RSI 事件/输入。
没有重写这些组件，也没有接入 DSH 的模型客户端、服务容器、工具系统或调度器。
这不是搬运整个 DSH 产品：其专用命令和执行功能不适用于 RSI。

`make check` 校验原文件及补丁哈希、重建 bundle、运行 xterm 终端渲染和键盘测试，
并通过真实 PTY 驱动 TUI → Python → Codex。

来源：[Codex 固定版本](https://github.com/openai/codex/tree/3ee47c10513f1bb57d869ea650aba28638e3d11c/codex-rs/tui)、
[DSH 历史 TUI](https://github.com/deepseek-ai/deepseek-harness/tree/7248b5ec8f8769f882f12fd521504fa48e97bcf3/packages/ui/tui)、
[DSH 历史许可证](https://github.com/deepseek-ai/deepseek-harness/blob/7248b5ec8f8769f882f12fd521504fa48e97bcf3/LICENSE)。
