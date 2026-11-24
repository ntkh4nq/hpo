-- Disease codes an admin declares for a source document whose source declares none (pmc, upload)
-- (docs/evidence-plan.md §6). Keyed by the source document, not by a version, so new versions keep them.
CREATE TABLE declared_disease (
  source      TEXT NOT NULL,
  source_ref  TEXT NOT NULL,
  disease_ids TEXT[] NOT NULL,
  declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, source_ref)
);
