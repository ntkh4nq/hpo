-- Case data saved after every turn (agent.state.SAVED_KEYS). The case checkpoint lives in Redis with a TTL and is
-- rebuilt from this column and case_turn when it expired (T139, docs/checkpoint-plan.md).
ALTER TABLE case_record ADD COLUMN state jsonb;   -- NULL: no turn has written state yet
