.PHONY: help test check lint typecheck architecture frontend

UV ?= uv

help:
	@echo "make test       离线执行全部单元测试，不调用模型"
	@echo "make check      架构边界 + 单元测试 + Ruff + Mypy + 前端测试"

test:
	$(UV) run --locked pytest tests

lint:
	$(UV) run --locked ruff check src tests

typecheck:
	$(UV) run --locked mypy src tests

architecture:
	$(UV) run --locked pytest tests/test_architecture.py

frontend:
	node --test tests/frontend/workbench.test.mjs

check: test lint typecheck frontend
