format:
	poetry run black --target-version py39 .
	poetry run ruff --select I --fix .

PYTHON_FILES=.
lint: PYTHON_FILES=.
lint_diff: PYTHON_FILES=$(shell git diff --name-only --diff-filter=d main | grep -E '\.py$$')

lint lint_diff:
	poetry run black --target-version py39 $(PYTHON_FILES) --check
	poetry run ruff .
	poetry run mypy $(shell git diff --name-only --diff-filter=d main | grep -E '\.py$$')

test:
	poetry run pytest -vv -n 20 --cov=semantic_router --cov-report=term-missing --cov-report=xml --cov-fail-under=80
