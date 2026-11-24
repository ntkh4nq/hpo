-- Cases kept until the doctor deletes them (docs/followup-plan.md §A, T116; replaces the Redis TTL of T92).
-- The graph state of a case lives in the checkpointer's own tables (AsyncPostgresSaver.setup(), library-managed).

CREATE TABLE case_record (
  case_id    uuid PRIMARY KEY,
  title      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX case_record_updated ON case_record (updated_at DESC);

-- What each turn showed the doctor: the display record. A card edit after the suggestions (POST /findings, no model)
-- is a 'findings' row, so a reopened case knows whether the summary still describes the list. Not all of it is in the graph state (the limit answer and
-- guard replacements never enter it), and it shows how each answer was produced (principle #6).
CREATE TABLE case_turn (
  case_id     uuid NOT NULL REFERENCES case_record ON DELETE CASCADE,
  turn_no     int  NOT NULL,                 -- 1, 2, ... in start order
  route       text NOT NULL CHECK (route IN ('message', 'review', 'findings')),
  input       jsonb NOT NULL,                -- {"text": ...}, the ReviewDecision json, or {edits, notes, ranking_changed}
  items       jsonb NOT NULL DEFAULT '[]',   -- ordered {"kind":"step","step":{...}} | {"kind":"text","message_id":...,"text":...}
  outcome     text CHECK (outcome IN ('review', 'done', 'error')),   -- NULL while the turn runs
  citations   jsonb NOT NULL DEFAULT '[]',   -- citations of the `done` event
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  PRIMARY KEY (case_id, turn_no)
);

-- Explanations of the suggestions (T82). Per case: the model input holds the doctor's description and notes,
-- so deleting the case deletes them.
CREATE TABLE explanation_cache (
  case_id    uuid NOT NULL REFERENCES case_record ON DELETE CASCADE,
  input_key  text NOT NULL,                  -- agent.explanations.Prepared.key (sha256 of the model input)
  result     jsonb NOT NULL,                 -- agent.explanations.ModelResult json
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, input_key)
);
