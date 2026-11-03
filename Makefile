PYTHON ?= python3.12
VENV := backend/.venv
BIN := $(VENV)/bin

OLLAMA_MODEL ?= qwen3-vl:2b-instruct

.PHONY: setup env backend-install frontend-install db-start db-create migrate \
        dev-api dev-web worker test test-llm test-e2e lint build-web create-admin llm-setup \
        eval-letters demo-files

## First-time setup on a clean machine (after `brew install postgresql@16 tesseract ollama`)
setup: env backend-install frontend-install db-start db-create migrate llm-setup

## Start Ollama and download the local vision model (about 2 GB)
llm-setup:
	brew services start ollama
	ollama pull $(OLLAMA_MODEL)

env:
	$(PYTHON) backend/scripts/init_env.py

backend-install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e "backend[dev]"

frontend-install:
	cd frontend && npm install

db-start:
	brew services start postgresql@16

db-create:
	cd backend && .venv/bin/python -m scripts.create_local_db

migrate:
	cd backend && .venv/bin/alembic upgrade head

dev-api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000 --no-access-log

dev-web:
	cd frontend && npm run dev

## Background worker: processes uploads (text extraction, OCR, AI profile)
worker:
	cd backend && .venv/bin/python -m app.pipeline.worker

test:
	cd backend && .venv/bin/pytest

## Slow checks against the real local model (Ollama must be running)
test-llm:
	cd backend && .venv/bin/pytest -m ollama -s

## Fictional demo patients (documents + letters) in demo/
demo-files:
	cd backend && .venv/bin/python -m scripts.make_demo_files

## Browser walkthrough of the whole flow (services running): E2E_EMAIL=.. E2E_PASSWORD=.. make test-e2e
test-e2e: demo-files
	@test -n "$(E2E_EMAIL)" -a -n "$(E2E_PASSWORD)" || (echo 'usage: E2E_EMAIL=you@x E2E_PASSWORD=... make test-e2e'; exit 1)
	cd backend && .venv/bin/python e2e/portal_walkthrough.py ../e2e-screenshots

## Extraction on a folder of letters: make eval-letters LETTERS=~/dir [KEY=~/dir/key.json]
eval-letters:
	@test -n "$(LETTERS)" || (echo 'usage: make eval-letters LETTERS=folder [KEY=answer_key.json]'; exit 1)
	PYTHONPATH=backend $(BIN)/python -m scripts.evaluate_letters --letters "$(LETTERS)" $(if $(KEY),--key "$(KEY)",)

lint:
	cd backend && .venv/bin/ruff check app tests scripts e2e alembic/env.py

## Create the first admin account in the local database (prompts for a password)
create-admin:
	@test -n "$(EMAIL)" -a -n "$(NAME)" || (echo 'usage: make create-admin EMAIL=you@clinic.com NAME="Full Name"'; exit 1)
	cd backend && .venv/bin/python -m scripts.create_admin --local --email "$(EMAIL)" --name "$(NAME)"

build-web:
	cd frontend && npm run build
