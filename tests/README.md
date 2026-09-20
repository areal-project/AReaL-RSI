# 测试约定

测试主要覆盖用户会遇到的行为、真实故障回归，以及数据和权限边界。
字段名、枚举值、函数签名和上游库的内部实现不单独测试。

同一行为尽量复用已有测试。配置开关选有代表性的组合，不穷举组合；
原生 Runtime 测试保留启动、继续、恢复、停止的实际过程，避免为小差异反复启动进程。

日常修改运行相关文件，例如：

```sh
.venv/bin/pytest tests/test_effective_time.py
```

跨模块修改或合入前再运行完整的 `.venv/bin/pytest`。

跨模块交付运行 `make check`，包含 Python 行为/依赖测试、Ruff、Mypy 与前端测试。
前端依赖首次安装：`npm ci --prefix tests/frontend --ignore-scripts`。
