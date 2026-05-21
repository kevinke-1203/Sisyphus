PYTHON ?= python3.12
VENV ?= .venv
EVO := $(VENV)/bin/evo
PIP := $(VENV)/bin/python -m pip
HOST ?= 127.0.0.1
PORT ?= 8080

.PHONY: help venv install web list validate clean

help:
	@echo "Available targets:"
	@echo "  make install                  Create .venv and install dependencies"
	@echo "  make web                      Start Evo web dashboard"
	@echo "  make list                     List available workflows"
	@echo "  make validate                 Validate workflow config"
	@echo ""
	@echo "  HOST=127.0.0.1 PORT=8080      Web dashboard bind address"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)

venv: $(VENV)/bin/python

install: venv
	$(PIP) install -e .

web: venv
	@PIDS=$$(lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$PIDS" ]; then \
		echo "Stopping process(es) listening on port $(PORT): $$PIDS"; \
		kill $$PIDS 2>/dev/null || true; \
		sleep 1; \
		PIDS=$$(lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
		if [ -n "$$PIDS" ]; then \
			echo "Force stopping process(es) listening on port $(PORT): $$PIDS"; \
			kill -9 $$PIDS 2>/dev/null || true; \
		fi; \
	fi
	$(EVO) web --host "$(HOST)" --port "$(PORT)"

list: venv
	$(EVO) list

validate: venv
	$(EVO) validate

clean:
	rm -rf .pytest_cache build dist *.egg-info src/*.egg-info
