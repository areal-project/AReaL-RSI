.PHONY: help test check lint typecheck architecture

UV ?= uv

help:
	@echo "make test       离线执行全部单元测试，不调用模型"
	@echo "make check      架构边界 + 单元测试 + Ruff + Mypy"

test:
	$(UV) run --locked pytest tests

lint:
	$(UV) run --locked ruff check src tests

typecheck:
	$(UV) run --locked mypy src tests

architecture:
	$(UV) run --locked pytest tests/test_architecture.py

check: test lint typecheck
