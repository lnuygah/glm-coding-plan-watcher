SHELL := /bin/bash

PYTHON ?= $(shell if command -v python3.12 >/dev/null 2>&1; then command -v python3.12; elif command -v python3.11 >/dev/null 2>&1; then command -v python3.11; elif command -v python3 >/dev/null 2>&1; then command -v python3; fi)
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
GLM := $(VENV)/bin/glm-plan
PLAYWRIGHT := $(VENV)/bin/playwright
PYINSTALLER := $(VENV)/bin/pyinstaller

CONFIG ?= config.yaml
HOST ?= 127.0.0.1
PORT ?= 0
DB ?= daemon.sqlite3
HANDSHAKE ?= daemon.handshake.json

UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),arm64)
TARGET_TRIPLE ?= aarch64-apple-darwin
else ifeq ($(UNAME_M),aarch64)
TARGET_TRIPLE ?= aarch64-apple-darwin
else
TARGET_TRIPLE ?= x86_64-apple-darwin
endif

.PHONY: setup doctor doctor-python check login watch serve debug test lint gui app clean

setup: doctor-python
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,server]"
	$(PLAYWRIGHT) install chromium
	@if [ ! -f "$(CONFIG)" ]; then \
		$(GLM) init-config --output "$(CONFIG)"; \
	else \
		echo "$(CONFIG) already exists; leaving it unchanged."; \
	fi

doctor: doctor-python
	@echo "Python candidate: $(PYTHON)"
	@$(PYTHON) --version
	@if command -v cargo >/dev/null 2>&1; then \
		cargo --version; \
	else \
		echo "cargo: missing (needed only for GUI/app; install with: brew install rust)"; \
	fi
	@if command -v node >/dev/null 2>&1; then \
		node --version; \
	else \
		echo "node: missing (needed only for GUI/app; install with: brew install node)"; \
	fi
	@if command -v npm >/dev/null 2>&1; then \
		npm --version; \
	else \
		echo "npm: missing (needed only for GUI/app; install with: brew install node)"; \
	fi
	@if [ -x "$(GLM)" ]; then \
		echo "glm-plan: $(GLM)"; \
	else \
		echo "glm-plan: missing; run make setup"; \
	fi

doctor-python:
	@if [ -z "$(PYTHON)" ]; then \
		echo "Python 3.11+ not found. Install with: brew install python@3.12"; \
		exit 1; \
	fi
	@$(PYTHON) -c 'import sys; v=sys.version_info; print(f"python: {sys.executable} ({sys.version.split()[0]})"); raise SystemExit(0 if v >= (3, 11) else "Python 3.11+ is required; install with: brew install python@3.12")'

check:
	@status=0; $(GLM) check --config "$(CONFIG)" || status=$$?; \
	if [ "$$status" = "1" ]; then \
		echo "check returned 1: selected plan is not available or was not found."; \
		exit 0; \
	fi; \
	exit "$$status"

login:
	$(GLM) login --config "$(CONFIG)"

watch:
	$(GLM) watch --config "$(CONFIG)"

serve:
	$(GLM) serve --host "$(HOST)" --port "$(PORT)" --db "$(DB)" --handshake "$(HANDSHAKE)"

debug:
	$(GLM) debug-selectors --config "$(CONFIG)" --headful

test:
	$(VENV_PYTHON) -m compileall src
	$(VENV_PYTHON) -m pytest

lint:
	$(VENV_PYTHON) -m ruff check .

gui:
	npm install
	GLM_WATCHER_DAEMON_BIN="$(PWD)/$(GLM)" npm run tauri:dev

app:
	$(PIP) install -e ".[server,packaging]"
	$(PYINSTALLER) packaging/glm-plan-daemon.spec --noconfirm --distpath sidecar/bin
	cp sidecar/bin/glm-plan-daemon sidecar/bin/glm-plan-daemon-$(TARGET_TRIPLE)
	chmod +x sidecar/bin/glm-plan-daemon-$(TARGET_TRIPLE)
	npm install
	npm run tauri:build

clean:
	rm -rf .pytest_cache .ruff_cache build dist sidecar/build sidecar/dist node_modules src-tauri/target ui-dist
	rm -rf daemon.sqlite3 daemon.sqlite3-* worker-configs daemon.handshake.json *.handshake.json handshake.json
