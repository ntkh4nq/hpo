-- The case checkpoint moved to Redis with a TTL; case data is in case_record.state (T139, docs/checkpoint-plan.md §3).
DROP TABLE IF EXISTS checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations;
