RUN := cd backend && env -u VIRTUAL_ENV uv run --env-file ../.env

up:      ; docker compose up -d --wait
down:    ; docker compose down
migrate: ; $(RUN) python -m kb.migrate
api:     ; $(RUN) uvicorn api.main:app --reload
web:     ; cd frontend && pnpm dev
test:    ; $(RUN) pytest -q
probe:   ; $(RUN) python scripts/probe_models.py

.PHONY: up down migrate api web test probe
