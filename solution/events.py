"""Event-file ingestion into the append-only ledger.

At ingest time we only do what can be decided from the line itself and from event_ids
already seen: malformed check, dedupe, timestamp/phone normalisation. Linking to leads,
orphans and unknown dispositions depend on *which leads exist*, so they are derived at
report time from the final state (see reports.py).
"""
from __future__ import annotations

import json

from .normalize import normalize_phone, parse_event_ts, to_micros
from .pii import PiiVault


def _nonempty_str(x) -> bool:
    return isinstance(x, str) and x.strip() != ""


def ingest_event_file(conn, vault: PiiVault, text: str, file_id: int) -> dict:
    stats = {"rows_total": 0, "malformed_events": 0, "duplicate_events": 0}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        stats["rows_total"] += 1

        try:
            obj = json.loads(line)
        except ValueError:
            stats["malformed_events"] += 1
            continue
        if not isinstance(obj, dict):
            stats["malformed_events"] += 1
            continue
        event_id = obj.get("event_id")
        # Beyond the spec's two malformed cases we also require the fields every report relies on.
        ts = parse_event_ts(obj.get("occurred_at"))
        if (isinstance(event_id, (dict, list, bool)) or event_id is None or str(event_id).strip() == ""
                or not _nonempty_str(obj.get("event_type")) or not _nonempty_str(obj.get("workflow_id"))
                or not _nonempty_str(obj.get("call_id")) or ts is None):
            stats["malformed_events"] += 1
            continue

        etype = obj["event_type"].strip()
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        code = None
        if etype.startswith("disposition."):
            c = payload.get("disposition_code")
            code = c if isinstance(c, str) else None

        phone_hash = None
        if obj.get("phone") is not None:
            canonical = normalize_phone(obj["phone"])
            if canonical:
                phone_hash = vault.phone_hash(canonical)  # raw phone is never stored

        cur = conn.execute(
            "INSERT OR IGNORE INTO events (event_id, event_type, workflow_id, call_id, occurred_us,"
            " occurred_raw, phone_hash, disposition_code, payload, file_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(event_id), etype, obj["workflow_id"].strip(), obj["call_id"].strip(), to_micros(ts),
             str(obj["occurred_at"]), phone_hash, code,
             json.dumps(payload, sort_keys=True, ensure_ascii=False), file_id))
        if cur.rowcount == 0:
            stats["duplicate_events"] += 1
    return stats
