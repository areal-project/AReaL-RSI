.PHONY: check
check:
	python -m ruff format --check src examples/areal_rsi_tir tests
	python -m ruff check src examples/areal_rsi_tir tests
	python -m mypy src examples/areal_rsi_tir
	python -m pytest
