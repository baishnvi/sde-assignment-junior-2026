"""Orchestrates ingestion of a drop directory. One drop = one transaction."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import Workflow
from .events import ingest_event_file
from .leads import ingest_lead_file
from .pii import PiiVault


class LedgerError(Exception):
    pass


def _discover(drop: Path):
    """(kind, workflow_id|None, path) in processing order: leads by workflow+filename, then events."""
    items = []
    leads_dir = drop / "leads"
    if leads_dir.is_dir():
        for wf_dir in sorted(p for p in leads_dir.iterdir() if p.is_dir()):
            for f in sorted(wf_dir.glob("*.csv")):
                items.append(("leads", wf_dir.name, f))
    events_dir = drop / "events"
    if events_dir.is_dir():
        for f in sorted(events_dir.glob("*.jsonl")):
            items.append(("events", None, f))
    return items


def ingest_drop(conn, vault: PiiVault, workflows: dict[str, Workflow], drop_dir: Path, log=print) -> dict:
    drop = Path(drop_dir).resolve()
    if not drop.is_dir():
        raise LedgerError(f"not a directory: {drop_dir}")
    summary = {"ingested": 0, "skipped": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for kind, wf_id, path in _discover(drop):
            rel = str(path.relative_to(drop))
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()

            seen = conn.execute("SELECT sha256 FROM ingest_files WHERE drop_name=? AND rel_path=?",
                                (drop.name, rel)).fetchone()
            if seen:
                if seen[0] != digest:
                    raise LedgerError(
                        f"{drop.name}/{rel} was ingested before but its content has changed. "
                        "Refusing to ingest it again; use a fresh --db for different data.")
                summary["skipped"] += 1
                log(f"  skip   {drop.name}/{rel} (already ingested)")
                continue
            if kind == "leads" and wf_id not in workflows:
                log(f"  WARN   {drop.name}/{rel}: no schema for workflow {wf_id!r}; skipped")
                continue

            batch_id = path.stem if kind == "leads" else None
            file_id = conn.execute(
                "INSERT INTO ingest_files (kind, drop_name, rel_path, workflow_id, batch_id, sha256)"
                " VALUES (?,?,?,?,?,?)", (kind, drop.name, rel, wf_id, batch_id, digest)).lastrowid
            text = data.decode("utf-8-sig" if kind == "leads" else "utf-8", errors="replace")
            if kind == "leads":
                s = ingest_lead_file(conn, vault, workflows[wf_id], text, batch_id, file_id)
                conn.execute(
                    "UPDATE ingest_files SET rows_total=?, rows_rejected=?, duplicates_merged=?, field_warnings=?"
                    " WHERE id=?", (s["rows_total"], s["rows_rejected"], s["duplicates_merged"],
                                    s["field_warnings"], file_id))
            else:
                s = ingest_event_file(conn, vault, text, file_id)
                conn.execute(
                    "UPDATE ingest_files SET rows_total=?, malformed_events=?, duplicate_events=? WHERE id=?",
                    (s["rows_total"], s["malformed_events"], s["duplicate_events"], file_id))
            summary["ingested"] += 1
            log(f"  ingest {drop.name}/{rel}: {s}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")  # a failed drop leaves the database exactly as it was
        raise
    return summary
