RUN := cd backend && env -u VIRTUAL_ENV uv run --env-file ../.env

migrate:       ; $(RUN) python -m kb.migrate
seed-download: ; $(RUN) python -m kb.seed.hpo download
seed:          ; $(RUN) python -m kb.seed.hpo load
embed:         ; $(RUN) python -m kb.seed.embed
api:           ; $(RUN) uvicorn api.main:app --reload
# API that records model answers and replays them when the input repeats (core/llm_cache.py): run make e2e against it
api-replay:    ; LLM_CACHE=$(CURDIR)/data/cache/llm.sqlite $(RUN) uvicorn api.main:app --reload
web:           ; cd frontend && pnpm dev
test:          ; $(RUN) pytest -q
probe:         ; $(RUN) python scripts/probe_models.py
split-check:   ; $(RUN) python -m evals.split_check.run
analyze:       ; $(RUN) python -m phenotype.graph "$(TEXT)"
e2:            ; $(RUN) python -m evals.e2.run
rank:          ; $(RUN) python -m ranking.rank $(TERMS)
ingest-download: ; $(RUN) python -m kb.ingest.cli download $(SOURCE)
ingest:        ; $(RUN) python -m kb.ingest.cli ingest $(SOURCE)
ingest-worker: ; $(RUN) python -m kb.ingest.worker $(ARGS)
e2e:           ; $(RUN) python -m scripts.e2e_case

.PHONY: migrate seed-download seed embed api api-replay web test probe split-check analyze e2 rank ingest-download ingest ingest-worker e2e
