Schema is defined by the SQLAlchemy models and created at startup via `Base.metadata.create_all`.

Schema v2 adds joint studies and atomic publication:

- `studies.kind` (`source` / `joint`), `current_run_id`, `current_batch_id`
- `study_members` (parent/child reference edges; uniqueness per pair)
- `publication_batches` / `publication_items` (batch status, reason, per-study frozen run)
- `observations.revision`, `updated_at`, `change_reason` (correction trail)
- `runs.batch_id`, `kind` (`manual` / `auto`), `aggregate`, `applied`, `study_fingerprint`

`src/config/schema.py` adds any missing columns to an existing volume with server defaults; nothing is dropped or rewritten. Back up named `db-data` before changing schemas, or start from a fresh volume with `docker compose down -v`.
