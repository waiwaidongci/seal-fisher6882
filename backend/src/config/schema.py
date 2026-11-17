"""Non-destructive schema upkeep for the small local deployment.

``Base.metadata.create_all`` only creates missing tables. When an existing data
volume predates a new column, add it with a server default so old rows stay valid.
No column or table is ever dropped.
"""
import logging

from sqlalchemy import inspect, text

_log = logging.getLogger(__name__)

# table -> {column: DDL fragment}
_ADDED_COLUMNS = {
    'studies': {
        'kind': "VARCHAR(20) NOT NULL DEFAULT 'source'",
        'current_run_id': 'INTEGER NULL',
        'current_batch_id': 'INTEGER NULL',
    },
    'observations': {
        'revision': 'INTEGER NOT NULL DEFAULT 1',
        'updated_at': "VARCHAR(40) NOT NULL DEFAULT ''",
        'change_reason': "VARCHAR(500) NOT NULL DEFAULT ''",
    },
    'runs': {
        'batch_id': 'INTEGER NULL',
        'kind': "VARCHAR(20) NOT NULL DEFAULT 'manual'",
        'aggregate': 'INTEGER NOT NULL DEFAULT 0',
        'applied': 'INTEGER NOT NULL DEFAULT 0',
        'study_fingerprint': "VARCHAR(64) NOT NULL DEFAULT ''",
    },
}


def _backfill(engine):
    """Give pre-v2 completed runs the 'published' marker so review/export keep working."""
    statements = [
        "UPDATE runs SET applied = 1 WHERE status = 'completed' AND applied = 0 AND batch_id IS NULL",
        # A source batch's newest completed manual run becomes its visible version.
        "UPDATE studies SET current_run_id = ("
        " SELECT MAX(r.id) FROM runs r WHERE r.study_id = studies.id AND r.status = 'completed'"
        ") WHERE current_run_id IS NULL AND kind = 'source' AND EXISTS ("
        " SELECT 1 FROM runs r WHERE r.study_id = studies.id AND r.status = 'completed'"
        ")",
    ]
    for sql in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception:  # pragma: no cover - older schema/partial state
            _log.exception('backfill step failed: %s', sql)


def ensure_schema(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue
        present = {c['name'] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in present:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                _log.warning('schema upgraded: %s.%s added', table, name)
            except Exception:  # pragma: no cover - concurrent start or restricted DDL
                _log.exception('could not add %s.%s; continuing', table, name)
    _backfill(engine)
