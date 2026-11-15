"""Lead-file ingestion: header mapping, validation, normalisation, rejection, merge."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .config import Field, Workflow
from .normalize import normalize_header, normalize_phone, parse_date, parse_decimal, parse_enum
from .pii import PiiVault


def parse_value(f: Field, raw: str):
    """Return the normalised value, or None if `raw` is invalid for this field type."""
    if f.type == "string":
        return raw
    if f.type == "phone":
        return normalize_phone(raw)
    if f.type == "date":
        return parse_date(raw)
    if f.type == "decimal":
        return parse_decimal(raw)
    if f.type == "enum":
        return parse_enum(raw, f.values)
    raise ValueError(f"unsupported field type {f.type!r}")


def map_headers(header: list[str], wf: Workflow) -> dict[str, int]:
    """field name -> column index. If two columns match the same field, the first wins."""
    lookup: dict[str, Field] = {}
    for f in wf.fields:
        for key in [f.name, *f.aliases]:
            lookup.setdefault(normalize_header(key), f)
    mapped: dict[str, int] = {}
    for idx, h in enumerate(header):
        f = lookup.get(normalize_header(h))
        if f is not None and f.name not in mapped:
            mapped[f.name] = idx
    return mapped


def validate_row(row: list[str], mapped: dict[str, int], wf: Workflow):
    """Walk fields in schema order. Returns (values, warnings, reject_reason)."""
    values: dict[str, str | None] = {}
    warnings = 0
    for f in wf.fields:
        idx = mapped.get(f.name)
        raw = row[idx].strip() if idx is not None and idx < len(row) else ""
        if raw == "":
            if f.required:
                return values, 0, f"missing_required:{f.name}"
            values[f.name] = None
            continue
        parsed = parse_value(f, raw)
        if parsed is None:
            if f.type == "phone":
                return values, 0, "invalid_phone"
            if f.required:
                return values, 0, f"invalid_value:{f.name}"
            warnings += 1  # optional + present + invalid => null + one warning
        values[f.name] = parsed
    return values, warnings, None


def ingest_lead_file(conn, vault: PiiVault, wf: Workflow, text: str, batch_id: str, file_id: int) -> dict:
    stats = {"rows_total": 0, "rows_rejected": 0, "duplicates_merged": 0, "field_warnings": 0}
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        return stats
    mapped = map_headers(header, wf)
    phone_f = wf.phone_field
    ref_f = wf.reference_field
    rejections = []
    row_number = 0

    for row in reader:
        if all(c.strip() == "" for c in row):  # blank row: skipped, not counted
            continue
        row_number += 1
        stats["rows_total"] += 1
        values, warnings, reason = validate_row(row, mapped, wf)
        if reason:
            stats["rows_rejected"] += 1
            rejections.append((file_id, wf.workflow_id, batch_id, row_number, reason))
            continue
        stats["field_warnings"] += warnings

        canonical = values[phone_f.name]
        phone_hash = vault.phone_hash(canonical)
        new_attrs, new_pii = {}, {}
        for f in wf.fields:
            v = values.get(f.name)
            if f.type == "phone" or v is None:
                continue
            (new_pii if f.pii else new_attrs)[f.name] = vault.encrypt(v) if f.pii else v
        ref = values.get(ref_f.name) if ref_f else None

        existing = conn.execute(
            "SELECT id, attrs, pii FROM leads WHERE workflow_id=? AND phone_hash=?",
            (wf.workflow_id, phone_hash)).fetchone()
        if existing is None:
            for f in wf.fields:  # defaults apply to fields that are still null after merging
                if f.default is not None and f.type != "phone" and values.get(f.name) is None:
                    (new_pii if f.pii else new_attrs)[f.name] = vault.encrypt(f.default) if f.pii else f.default
            conn.execute(
                "INSERT INTO leads (workflow_id, phone_hash, phone_last4, external_ref, batch_id,"
                " attrs, pii, first_file_id) VALUES (?,?,?,?,?,?,?,?)",
                (wf.workflow_id, phone_hash, canonical[-4:], ref or "", batch_id,
                 json.dumps(new_attrs, sort_keys=True), json.dumps(new_pii, sort_keys=True), file_id))
        else:
            lead_id, attrs_s, pii_s = existing
            attrs, pii = json.loads(attrs_s), json.loads(pii_s)
            attrs.update(new_attrs)  # last write wins, only where the new value is non-null
            pii.update(new_pii)
            conn.execute(
                "UPDATE leads SET attrs=?, pii=?, external_ref=COALESCE(?, external_ref) WHERE id=?",
                (json.dumps(attrs, sort_keys=True), json.dumps(pii, sort_keys=True), ref, lead_id))
            stats["duplicates_merged"] += 1

    if rejections:
        conn.executemany(
            "INSERT INTO rejections (file_id, workflow_id, batch_id, row_number, reason) VALUES (?,?,?,?,?)",
            rejections)
    return stats
