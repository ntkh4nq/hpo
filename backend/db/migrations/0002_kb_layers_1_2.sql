-- KB layers 1–2 (docs/kb-architecture.md §3.1, §3.2, §3.4). Filled by kb.seed, never edited by hand.

CREATE TABLE kb_manifest (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL,          -- 'hpo' | 'translations'
  release       TEXT NOT NULL,
  file          TEXT NOT NULL,
  url           TEXT,
  sha256        TEXT NOT NULL,
  downloaded_at TIMESTAMPTZ NOT NULL,
  license       TEXT,
  UNIQUE (source, release, file)
);

-- Layer 1 · HPO vocabulary
CREATE TABLE hpo_term (
  hpo_id            TEXT PRIMARY KEY,
  label_en          TEXT NOT NULL,
  definition_en     TEXT,
  is_obsolete       BOOLEAN NOT NULL,
  replaced_by       TEXT,
  prenatal_relevant BOOLEAN NOT NULL DEFAULT false,
  prenatal_basis    TEXT,               -- 'subtree:HP:...;subtree:HP:...'
  ic                DOUBLE PRECISION    -- NULL = no disease annotated with the term or its descendants
);

CREATE TABLE hpo_name (
  id              BIGSERIAL PRIMARY KEY,
  hpo_id          TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  text            TEXT NOT NULL,
  lang            TEXT NOT NULL,        -- 'en' | 'vi'
  kind            TEXT NOT NULL,        -- 'label' | 'exact_syn' | 'related_syn' | 'broad_syn' | 'narrow_syn' | 'definition' | 'translation'
  source          TEXT NOT NULL,        -- 'hpo' | 'doctor' | 'claude' | 'chatgpt'
  text_norm       TEXT NOT NULL,        -- lowercase, no diacritics, single spaces
  embedding       VECTOR(1024),
  embedding_model TEXT,
  UNIQUE (hpo_id, lang, kind, source, text)
);
CREATE INDEX hpo_name_embedding_idx ON hpo_name USING hnsw (embedding vector_cosine_ops);
CREATE INDEX hpo_name_trgm_idx ON hpo_name USING gin (text_norm gin_trgm_ops);
CREATE INDEX hpo_name_text_idx ON hpo_name USING hash (text);  -- embedding fill updates by text

CREATE TABLE hpo_edge (
  child_id  TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  parent_id TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  PRIMARY KEY (child_id, parent_id)
);

CREATE TABLE hpo_ancestor (             -- transitive closure of hpo_edge, incl. distance 0 (the term itself)
  hpo_id      TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  ancestor_id TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  distance    INT NOT NULL,             -- shortest path
  PRIMARY KEY (hpo_id, ancestor_id)
);
CREATE INDEX hpo_ancestor_ancestor_idx ON hpo_ancestor (ancestor_id);

CREATE TABLE hpo_alt_id (
  alt_id TEXT PRIMARY KEY,
  hpo_id TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE
);

-- Layer 2 · structured relations
CREATE TABLE disease (
  disease_id TEXT PRIMARY KEY,          -- 'OMIM:154700' | 'ORPHA:558' | 'DECIPHER:...'
  name       TEXT,                      -- NULL when the disease only appears in genes_to_disease
  db         TEXT NOT NULL
);

CREATE TABLE disease_phenotype (
  id           BIGSERIAL PRIMARY KEY,
  disease_id   TEXT NOT NULL REFERENCES disease ON DELETE CASCADE,
  hpo_id       TEXT NOT NULL REFERENCES hpo_term ON DELETE CASCADE,
  is_negated   BOOLEAN NOT NULL,        -- qualifier 'NOT': the disease is recorded WITHOUT this phenotype
  freq_raw     TEXT,                    -- as in the source: 'HP:0040282' | '12/45' | '22%'
  freq_num     INT,
  freq_den     INT,
  freq_hpo     TEXT,
  freq_pct     DOUBLE PRECISION,
  onset_hpo    TEXT,
  sex          TEXT,
  modifiers    TEXT[],
  aspect       CHAR(1) NOT NULL,        -- P | I | C | M | H
  evidence     TEXT,                    -- IEA | PCS | TAS
  "references" TEXT[],                  -- PMID:… | OMIM:… | ORPHA:…
  biocuration  TEXT
);
CREATE INDEX disease_phenotype_hpo_idx ON disease_phenotype (hpo_id);
CREATE INDEX disease_phenotype_disease_idx ON disease_phenotype (disease_id);

CREATE TABLE gene (
  ncbi_gene_id TEXT PRIMARY KEY,
  symbol       TEXT NOT NULL
);

CREATE TABLE gene_disease (
  ncbi_gene_id     TEXT NOT NULL REFERENCES gene ON DELETE CASCADE,
  disease_id       TEXT NOT NULL REFERENCES disease ON DELETE CASCADE,
  association_type TEXT,                -- MENDELIAN | POLYGENIC | UNKNOWN
  source           TEXT,
  PRIMARY KEY (ncbi_gene_id, disease_id)
);
CREATE INDEX gene_disease_disease_idx ON gene_disease (disease_id);

-- Gene–phenotype only ever exists through a disease; disease_id is always kept.
CREATE VIEW phenotype_gene_via_disease AS
SELECT dp.hpo_id, dp.disease_id, gd.ncbi_gene_id, g.symbol, gd.association_type,
       dp.is_negated, dp.freq_raw, dp.evidence, dp."references"
FROM disease_phenotype dp
JOIN gene_disease gd USING (disease_id)
JOIN gene g USING (ncbi_gene_id);
