# Dev/CI entry points. Uses `sh`, not bash, to match the project's own
# no-bashisms rule for bin/cc-yank and install.sh.
SHELL := /bin/sh

.PHONY: all test lint check

all: check

test:
	python3 -m unittest discover -s tests -v
	sh tests/test_cli.sh

lint:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck bin/cc-yank install.sh tests/test_cli.sh; \
	else \
		echo "lint: shellcheck not installed, skipping shell lint"; \
	fi
	@if python3 -m pyflakes --version >/dev/null 2>&1; then \
		python3 -m pyflakes hooks/extract.py tests/*.py; \
	else \
		echo "lint: pyflakes not installed, falling back to py_compile"; \
		python3 -m py_compile hooks/extract.py tests/*.py; \
	fi

check: lint test
