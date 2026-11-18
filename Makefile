RUN := cd backend && env -u VIRTUAL_ENV uv run --env-file ../.env

up:            ; docker compose up -d --wait
down:          ; docker compose down
migrate:       ; $(RUN) python -m kb.migrate
seed-download: ; $(RUN) python -m kb.seed.hpo download
seed:          ; $(RUN) python -m kb.seed.hpo load
embed:         ; $(RUN) python -m kb.seed.embed
api:           ; $(RUN) uvicorn api.main:app --reload
web:           ; cd frontend && pnpm dev
test:          ; $(RUN) pytest -q
probe:         ; $(RUN) python scripts/probe_models.py
split-check:   ; $(RUN) python -m evals.split_check.run
analyze:       ; $(RUN) python -m phenotype.graph "$(TEXT)"
e2:            ; $(RUN) python -m evals.e2.run
rank:          ; $(RUN) python -m ranking.rank $(TERMS)

.PHONY: up down migrate seed-download seed embed api web test probe split-check analyze e2 rank
