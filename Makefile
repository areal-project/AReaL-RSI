.PHONY: help test check lint typecheck

UV ?= uv

help:
	@echo "make test       离线执行全部单元测试，不调用模型"
	@echo "make check      单元测试 + Ruff + Mypy"

test:
	$(UV) run --locked pytest tests

lint:
	$(UV) run --locked ruff check src tests examples/past_bench/run.py

typecheck:
	$(UV) run --locked mypy src tests examples/past_bench/run.py

check: test lint typecheck
