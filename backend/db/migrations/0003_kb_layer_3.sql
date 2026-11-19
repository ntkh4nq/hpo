-- Layer 3 · documents, sections, chunks, ingestion queue (docs/ingestion-plan.md, Appendix A; T60–T76)
-- No foreign keys to layer 1–2 tables, so `make seed` never touches layer 3 data.

CREATE TABLE document (
  doc_id       BIGSERIAL PRIMARY KEY,
  source       TEXT NOT NULL,      -- 'orphanet_def' | 'orphanet_summary' | 'pubmed' | 'genereviews' | 'pmc' | 'upload'
  source_ref   TEXT NOT NULL,      -- 'ORPHA:881' | 'PMID:39407794' | 'NBK1124' | 'PMC11476750' | sha256 của file upload
  version      INT NOT NULL DEFAULT 1,
  status       TEXT NOT NULL,      -- 'PROCESSING' | 'FAILED' | 'PENDING' | 'ACTIVE' | 'REJECTED' | 'RETIRED'
  title        TEXT NOT NULL,
  citation     TEXT,
  url          TEXT,
  license      TEXT NOT NULL,
  language     TEXT,               -- 'en' | 'vi'
  doc_type     TEXT NOT NULL,      -- 'definition' | 'summary' | 'abstract' | 'review_chapter' | 'article' | 'upload'
  release      TEXT NOT NULL,      -- thư mục ngày trong data/raw (nối kb_manifest), hoặc ngày upload
  raw_path     TEXT NOT NULL,      -- đường dẫn tương đối từ gốc repo
  content_hash TEXT NOT NULL,      -- sha256 của file tài liệu
  disease_ids  TEXT[] NOT NULL DEFAULT '{}',   -- chỉ mã nguồn khai báo (T75)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, source_ref, version)
);

CREATE TABLE section (
  section_id   BIGSERIAL PRIMARY KEY,
  doc_id       BIGINT NOT NULL REFERENCES document ON DELETE CASCADE,
  ord          INT NOT NULL,
  heading_path TEXT[] NOT NULL,
  UNIQUE (doc_id, ord)
);

CREATE TABLE chunk (
  chunk_id        BIGSERIAL PRIMARY KEY,
  doc_id          BIGINT NOT NULL REFERENCES document ON DELETE CASCADE,
  section_id      BIGINT NOT NULL REFERENCES section ON DELETE CASCADE,
  strategy        TEXT NOT NULL,                 -- tên chiến lược trong chunking.toml
  ord             INT NOT NULL,
  kind            TEXT NOT NULL,                 -- 'text' | 'table' | 'figure'
  role            TEXT NOT NULL DEFAULT 'leaf',  -- 'leaf' | 'parent' | 'child'
  parent_id       BIGINT REFERENCES chunk ON DELETE CASCADE,
  display_text    TEXT NOT NULL,                 -- nguyên văn; bảng = markdown; hình = caption
  embed_text      TEXT,                          -- NULL với chunk cha
  figure_path     TEXT,
  tsv             TSVECTOR,
  embedding       VECTOR(1024),
  embedding_model TEXT,
  image_embedding HALFVEC(2048),                 -- vector của ảnh (T72)
  image_model     TEXT,
  disease_ids     TEXT[] NOT NULL,               -- chép từ document (T62)
  is_active       BOOLEAN NOT NULL,              -- đúng khi và chỉ khi document ACTIVE
  UNIQUE (doc_id, strategy, ord)
);
CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunk_image_idx ON chunk USING hnsw (image_embedding halfvec_cosine_ops);  -- index cho vector trên 2.000 chiều phải dùng halfvec
CREATE INDEX chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX chunk_disease_idx ON chunk USING gin (disease_ids);

CREATE TABLE figure_description (
  image_sha256   TEXT NOT NULL,
  model          TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  description    TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (image_sha256, model, prompt_version)
);

CREATE TABLE figure_embedding (
  image_sha256 TEXT NOT NULL,
  model        TEXT NOT NULL,
  embedding    HALFVEC(2048) NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (image_sha256, model)
);

CREATE TABLE ingest_run (
  run_id      BIGSERIAL PRIMARY KEY,
  command     TEXT NOT NULL,          -- dòng lệnh hoặc endpoint đã tạo run
  source      TEXT NOT NULL,          -- tên nguồn, 'upload' hoặc 'rechunk'
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  counts      JSONB                    -- {"done": n, "skipped": n, "failed": n}
);

CREATE TABLE ingest_job (
  job_id       BIGSERIAL PRIMARY KEY,
  run_id       BIGINT NOT NULL REFERENCES ingest_run,
  kind         TEXT NOT NULL,          -- 'ingest' | 'rechunk'
  source       TEXT NOT NULL,
  source_ref   TEXT NOT NULL,
  raw_path     TEXT NOT NULL,
  strategy     TEXT NOT NULL,
  doc_id       BIGINT REFERENCES document ON DELETE SET NULL,
  state        TEXT NOT NULL DEFAULT 'queued',  -- 'queued' | 'running' | 'done' | 'skipped' | 'failed'
  step         TEXT,                   -- 'register' | 'parse' | 'ids' | 'figures' | 'chunk' | 'embed' | 'write'
  attempts     INT NOT NULL DEFAULT 0,
  last_error   TEXT,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ
);
CREATE INDEX ingest_job_queue_idx ON ingest_job (state, available_at, job_id);
