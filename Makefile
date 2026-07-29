# Developer convenience commands. Every target here is a thin wrapper
# around a plain command documented in docs/TESTING.md / docs/CONTRIBUTING.md
# -- if `make` isn't available on your system (e.g. plain Windows cmd.exe),
# copy the command out of the matching target below and run it directly.
#
# NOTE: MetaTrader5 (a runtime dependency) only ships Windows wheels, so
# `install`/`test`/`run` require Windows. `lint`/`format`/`typecheck`/`docs`
# have no such constraint.

.PHONY: install install-dev test test-determinism test-goldens lint format \
        format-check typecheck docs run run-prod clean

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt -r requirements-dev.txt

## Full suite (see docs/TESTING.md for what "full" covers: unit tests,
## hand-worked scenario tests, and golden-file regression tests together).
test:
	pytest -v

## Determinism gate: run the suite twice, then once more in reverse file
## order. See docs/ARCHITECTURE.md#21-deterministic-processing.
test-determinism:
	pytest -q
	pytest -q
	pytest -q $$(ls tests/test_*.py | sort -r)

## Isolate just the golden-file regression tests.
test-goldens:
	pytest -v -k "golden"

## Blocking lint gate (matches .github/workflows/ci.yml's `lint` job).
lint:
	ruff check app/ main.py

## Auto-fix what ruff can fix automatically (production code only --
## review the diff before committing).
format:
	ruff check --fix app/ main.py

## Reports black's proposed diff without applying it. Currently
## informational only -- see pyproject.toml's [tool.black] note for why.
format-check:
	black --check --diff app/ main.py tests/

## Currently informational only -- see pyproject.toml's [tool.mypy] note.
typecheck:
	mypy app/ main.py

## Regenerates nothing by itself -- prints where the documentation set
## lives. There is no static-site build step for docs/ today.
docs:
	@echo "Documentation lives under docs/ -- see docs/ARCHITECTURE.md as the entry point."
	@echo "This project does not currently build docs/ into a static site."

## Run the API locally (requires a running, logged-in MT5 terminal).
run:
	uvicorn main:app --reload

## Production Readiness Certification, Task 3: no --reload (no file
## watcher, no unwanted restarts), --workers 1 (deliberate -- see
## docs/DEPLOYMENT.md for why worker count is pinned rather than
## scaled). Adjust --host/--port for your environment; set
## MT5_AI_BRIDGE_API_KEY before binding beyond 127.0.0.1 (see
## docs/DEPLOYMENT.md and SECURITY.md). Requires a running, logged-in
## MT5 terminal, same as `run`. Intended to run under a process
## supervisor (docs/DEPLOYMENT.md#restart-policy), not directly in an
## interactive shell long-term.
run-prod:
	uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1

clean:
	find . -type d -name "__pycache__" -not -path "./venv/*" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache
