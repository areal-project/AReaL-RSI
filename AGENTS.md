# 开发约束

- 契约放 `api/`，核心实现放 `infra/`，`ui/` 仅是可选入口；不增加顶层 base/modules/app 层。
- `api/records.py`、`research.py`、`execution.py` 放数据契约，`interfaces.py` 仅在存在真实多个实现时放协议，`control.py` 放控制请求和 JSON 边界校验。
- 最小 demo 的执行入口在 `src/areal_rsi/examples/tir/cycle.py`；`api/` 与 `scripts/` 保持任务无关，API 不导入 infra，infra 不导入 UI 或示例。
- 优先删减重复逻辑、合并薄模块；不为目录对称、潜在扩展或缩短文件新增层次。
- 不增加单实现注入接口、转发类、空生命周期方法或兼容壳；需要抽象时先说明真实复用点。
- 固定业务结构使用具名 dataclass/enum，禁止用多层 `dict[str, Any]` 承载内部状态、重试和结果；字典仅用于按 ID 索引、同类型映射或 JSON/插件边界。边界解码后使用类型字段。
- JSON 操作共用 `api/control.py` 校验；具名控制方法接收 run_id 并保留领域授权和状态检查。
- 数据目录、候选摘要、冻结协议和 source-lock 不是源码目录，不得迁移或重写旧分支中的记录。
- Runtime 移动须同步复制包、插件 entrypoint、资源定位和整个源码包的沙箱拒绝范围。
- 测试覆盖真实行为/故障，不穷举字段和转发层。跨模块修改运行 `make check` 并报告跳过环境。
- `coordination/` 顶层放状态/调度与记录，策略放 `policies/`，Agent 工具放 `tools/`，研究评测放 `evaluation/`；不要平铺一次性辅助模块。

首版部署只支持 Slurm。不要为 Ray 添加控制分支、探针、配置或兼容代码。
