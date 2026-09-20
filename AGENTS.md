# 开发约束

- 契约放 `api/`，核心实现放 `infra/`，`ui/` 仅是可选入口；不再增加顶层 base/modules/app 层。
- `api/records.py`、`research.py`、`execution.py` 放数据契约，`interfaces.py` 放协议，`control.py` 放控制请求。
- 系统入口是 `infra/research.py` 的 `ResearchSystem`；API 不导入 infra，infra 不导入 UI。
- 优先删减重复逻辑、合并薄模块；不要为目录对称、潜在扩展或缩短文件新增层次。
- 不增加单实现注入接口、转发类、空生命周期方法或兼容壳；需要抽象时先说明真实复用点。
- JSON 操作共用 `api/control.py` 校验；具名控制方法接收 run_id 并保留领域授权和状态检查。
- 数据目录、候选摘要、冻结协议和 source-lock 不是源码目录，移动包时不能顺手重写这些记录。
- Runtime 移动须同步复制包、插件 entrypoint、资源定位和整个源码包的沙箱拒绝范围。
- 测试覆盖真实行为/故障，不穷举字段和转发层。跨模块修改运行 `make check` 并报告跳过环境。

coordination 顶层放状态/调度与记录，策略放 policies，Agent 工具放 tools，研究评测放 evaluation；不要再平铺一次性辅助模块。
