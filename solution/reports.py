"""Derive the five report files from the ledger.

Everything is computed from the *final* database state on every run, so the output only
depends on which files have been ingested, never on how they were split into drops or in
what order the events arrived.

Linking rules (spec):
  * an event links to the lead with the same (workflow_id, phone_hash);
  * a disposition.override has no phone and links through its call_id to the lead of the
    call's other events.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .normalize import micros_to_ist

_US_PER_DAY = 86_400_000_000
_IST_OFFSET_US = 19_800_000_000
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()

BUCKETS = ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]


class _Call:
    __slots__ = ("wf", "lead", "init_us", "connected", "failed", "set_best", "ovr_best")

    def __init__(self, wf, lead):
        self.wf, self.lead = wf, lead
        self.init_us = None      # earliest call.initiated
        self.connected = False
        self.failed = False
        self.set_best = None     # (occurred_us, event_id, code) of latest disposition.set
        self.ovr_best = None     # same, for disposition.override

    def effective(self):
        best = self.ovr_best or self.set_best  # an override beats any set, never edits it
        return best[2] if best else None


def _ist_date(us: int) -> date:
    return date.fromordinal(_EPOCH_ORDINAL + (us + _IST_OFFSET_US) // _US_PER_DAY)


def compute(conn, dispositions) -> dict:
    """Return all report tables as python structures."""
    leads = {r[0]: r[1:] for r in conn.execute(
        "SELECT id, workflow_id, external_ref, batch_id, phone_last4 FROM leads")}

    calls: dict[tuple, _Call] = {}
    overrides = []
    orphans = unknown = 0

    cur = conn.execute(
        "SELECT e.event_id, e.event_type, e.workflow_id, e.call_id, e.occurred_us, e.disposition_code, l.id"
        " FROM events e LEFT JOIN leads l ON l.workflow_id = e.workflow_id AND l.phone_hash = e.phone_hash"
        " ORDER BY e.id")
    for event_id, etype, wf, call_id, us, code, lead_id in cur:
        key = (wf, call_id)
        if etype == "disposition.override":
            overrides.append((key, us, event_id, code, wf, lead_id))  # resolved after the pass
            continue
        if lead_id is None:
            orphans += 1
            continue
        call = calls.get(key)
        if call is None:
            call = calls[key] = _Call(wf, lead_id)
        if etype == "call.initiated":
            if call.init_us is None or us < call.init_us:
                call.init_us = us
        elif etype == "call.connected":
            call.connected = True
        elif etype == "call.telephony_failed":
            call.failed = True
        elif etype == "disposition.set":
            if code not in dispositions.get(wf, {}):
                unknown += 1
            elif call.set_best is None or (us, event_id) > call.set_best[:2]:
                call.set_best = (us, event_id, code)

    for key, us, event_id, code, wf, direct_lead in overrides:
        call = calls.get(key)
        lead = direct_lead or (call.lead if call else None)
        if lead is None:
            orphans += 1
        elif code not in dispositions.get(wf, {}):
            unknown += 1
        elif call is not None and (call.ovr_best is None or (us, event_id) > call.ovr_best[:2]):
            call.ovr_best = (us, event_id, code)

    # ---- per-lead facts -------------------------------------------------------------
    by_lead: dict[int, list] = defaultdict(list)
    for (wf, call_id), call in calls.items():
        if call.init_us is not None:  # a call exists only if its call.initiated is present
            by_lead[call.lead].append((call.init_us, call_id, call))

    lead_rows, batch = [], defaultdict(lambda: dict.fromkeys(BUCKETS, 0))
    daily: dict[tuple, dict] = {}
    for lead_id, (wf, ref, batch_id, last4) in leads.items():
        cl = sorted(by_lead.get(lead_id, []), key=lambda t: (t[0], t[1]))
        catalog = dispositions.get(wf, {})
        effs = [(c.effective(), c) for _, _, c in cl]
        any_terminal = any(e and catalog[e].is_terminal for e, _ in effs)
        ever_success = any(e and catalog[e].is_success for e, _ in effs)
        if not cl:
            bucket = "yet_to_reach_out"
        elif any_terminal:
            bucket = "completed"
        elif all(c.failed for _, c in effs):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"
        batch[(wf, batch_id)][bucket] += 1

        current = next((e for e, _ in reversed(effs) if e), "")
        lead_rows.append({
            "workflow_id": wf, "external_ref": ref, "batch_id": batch_id,
            "phone_masked": "+91******" + last4, "bucket": bucket,
            "total_calls": len(cl), "connected_calls": sum(1 for _, c in effs if c.connected),
            "last_call_at": micros_to_ist(cl[-1][0]).isoformat(timespec="seconds") if cl else "",
            "current_disposition": current, "ever_success": "true" if ever_success else "false",
        })

        for (init_us, _, c), (e, _) in zip(cl, effs):
            d = daily.setdefault((wf, _ist_date(init_us)),
                                 {"calls": 0, "connected": 0, "success": set()})
            d["calls"] += 1
            d["connected"] += c.connected
            if e and catalog[e].is_success:
                d["success"].add(lead_id)

    lead_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"], r["batch_id"], r["phone_masked"]))

    batch_rows = [{"workflow_id": wf, "batch_id": b, "total_leads": sum(v.values()), **v}
                  for (wf, b), v in sorted(batch.items())]
    daily_rows = [{
        "workflow_id": wf, "date": d.isoformat(), "total_calls": v["calls"],
        "connected_calls": v["connected"], "connectivity_rate": f"{v['connected'] / v['calls']:.4f}",
        "success_leads": len(v["success"]),
    } for (wf, d), v in sorted(daily.items())]

    rejection_rows = [dict(zip(("workflow_id", "batch_id", "row_number", "reason"), r)) for r in conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM rejections"
        " ORDER BY workflow_id, batch_id, row_number")]
    by_reason = dict(conn.execute(
        "SELECT reason, COUNT(*) FROM rejections GROUP BY reason ORDER BY reason").fetchall())

    sums = conn.execute(
        "SELECT"
        " COALESCE(SUM(CASE WHEN kind='leads'  THEN rows_total END),0),"
        " COALESCE(SUM(CASE WHEN kind='leads'  THEN rows_rejected END),0),"
        " COALESCE(SUM(CASE WHEN kind='leads'  THEN duplicates_merged END),0),"
        " COALESCE(SUM(CASE WHEN kind='leads'  THEN field_warnings END),0),"
        " COALESCE(SUM(CASE WHEN kind='events' THEN rows_total END),0),"
        " COALESCE(SUM(CASE WHEN kind='events' THEN malformed_events END),0),"
        " COALESCE(SUM(CASE WHEN kind='events' THEN duplicate_events END),0)"
        " FROM ingest_files").fetchone()
    quality = {
        "lead_rows_total": sums[0], "lead_rows_rejected": sums[1], "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": sums[2], "field_warnings": sums[3], "events_total": sums[4],
        "malformed_events": sums[5], "duplicate_events_dropped": sums[6],
        "orphan_events": orphans, "unknown_disposition_events": unknown,
    }
    return {"batch_summary": batch_rows, "daily_overview": daily_rows, "lead_report": lead_rows,
            "rejections": rejection_rows, "data_quality": quality}


_COLUMNS = {
    "batch_summary": ["workflow_id", "batch_id", "total_leads", *BUCKETS],
    "daily_overview": ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
    "lead_report": ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                    "connected_calls", "last_call_at", "current_disposition", "ever_success"],
    "rejections": ["workflow_id", "batch_id", "row_number", "reason"],
}
_FILENAMES = {"batch_summary": "batch_summary.csv", "daily_overview": "daily_overview.csv",
              "lead_report": "lead_report.csv", "rejections": "rejections.csv"}


def write_reports(conn, dispositions, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = compute(conn, dispositions)
    for name, cols in _COLUMNS.items():
        with open(out_dir / _FILENAMES[name], "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(data[name])
    (out_dir / "data_quality.json").write_text(
        json.dumps(data["data_quality"], indent=2) + "\n", encoding="utf-8")
    return data
