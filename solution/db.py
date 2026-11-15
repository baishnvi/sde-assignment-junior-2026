"""SQLite schema and connection helper.

Tables
------
ingest_files  one row per ingested file (the idempotency registry + per-file counters)
leads         one row per (workflow, phone). PII is never stored in plaintext.
rejections    rejected lead rows (reason only, no PII)
events        the append-only interaction ledger (UPDATE / DELETE are blocked by triggers)

Nothing about *linking* is stored: an event is joined to its lead at report time on
(workflow_id, phone_hash). That is what makes events-before-leads work without any
re-processing step, and makes the result independent of drop order.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS ingest_files (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    drop_name        TEXT NOT NULL,
    rel_path         TEXT NOT NULL,
    workflow_id      TEXT,
    batch_id         TEXT,
    sha256           TEXT NOT NULL,
    rows_total       INTEGER NOT NULL DEFAULT 0,   -- leads: non-blank data rows; events: non-empty lines
    rows_rejected    INTEGER NOT NULL DEFAULT 0,
    duplicates_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings   INTEGER NOT NULL DEFAULT 0,
    malformed_events INTEGER NOT NULL DEFAULT 0,
    duplicate_events INTEGER NOT NULL DEFAULT 0,
    ingested_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (drop_name, rel_path)
);

CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY,
    workflow_id   TEXT NOT NULL,
    phone_hash    TEXT NOT NULL,        -- HMAC-SHA256(canonical phone); the matching key
    phone_last4   TEXT NOT NULL,        -- only for report masking
    external_ref  TEXT NOT NULL,        -- schema field marked "reference"
    batch_id      TEXT NOT NULL,        -- file stem of the first valid row; never changes
    attrs         TEXT NOT NULL,        -- JSON of non-PII fields
    pii           TEXT NOT NULL,        -- JSON of Fernet-encrypted PII fields
    first_file_id INTEGER NOT NULL REFERENCES ingest_files(id),
    UNIQUE (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES ingest_files(id),
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY,   -- arrival order
    event_id         TEXT NOT NULL UNIQUE,  -- dedupe key, across all drops
    event_type       TEXT NOT NULL,
    workflow_id      TEXT NOT NULL,
    call_id          TEXT NOT NULL,
    occurred_us      INTEGER NOT NULL,      -- UTC microseconds since epoch (naive input = IST)
    occurred_raw     TEXT NOT NULL,         -- original string, for audit
    phone_hash       TEXT,                  -- NULL: no phone (overrides) or invalid phone
    disposition_code TEXT,                  -- copied out of payload for disposition.* events
    payload          TEXT NOT NULL,
    file_id          INTEGER NOT NULL REFERENCES ingest_files(id)
);
CREATE INDEX IF NOT EXISTS ix_events_link ON events (workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS ix_events_call ON events (workflow_id, call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'interaction ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'interaction ledger is append-only'); END;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    gi = db_path.parent / ".gitignore"
    if db_path.parent.name == ".ledger" and not gi.exists():
        gi.write_text("*\n")  # keep db + key out of git by default
    conn = sqlite3.connect(db_path, isolation_level=None)  # we manage transactions explicitly
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    return conn
