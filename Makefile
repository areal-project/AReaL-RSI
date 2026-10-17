.PHONY: help test check lint typecheck architecture ui-test

UV ?= uv

help:
	@echo "make test       离线执行全部单元测试，不调用模型"
	@echo "make check      架构边界 + 单元测试 + Ruff + Mypy"

test:
	$(UV) run --locked pytest tests

lint:
	$(UV) run --locked ruff check src tests examples

typecheck:
	$(UV) run --locked mypy src tests examples/tir/pipeline.py examples/tir/data.py examples/tir/reward.py examples/tir/benchmark.py examples/tir/synthesis.py examples/tir/experiment.py examples/tir/rsi.py examples/search/pipeline.py examples/search/data.py examples/search/reward.py examples/search/goal.py examples/search/analysis.py examples/search/final_evaluation.py examples/search/official_output.py

architecture:
	$(UV) run --locked pytest tests/test_architecture.py

ui-test:
	cd src/areal_rsi/ui/dsh && npm ci --ignore-scripts --no-audit --no-fund
	cd src/areal_rsi/ui/dsh && node patch.mjs && npm run build && npm test

check: ui-test test lint typecheck
