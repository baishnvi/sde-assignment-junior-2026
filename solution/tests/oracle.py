"""A deliberately naive, database-free re-implementation of the spec, used only by tests to
cross-check the real pipeline on generated data. It re-reads the raw files and re-derives
every number with plain dicts and loops (slow, but easy to reason about)."""
import csv
import json
from collections import Counter
from pathlib import Path

from solution.config import load_dispositions, load_workflows
from solution.leads import parse_value
from solution.normalize import micros_to_ist, normalize_header, normalize_phone, parse_event_ts, to_micros


def run(drops: list[Path]):
    workflows, disp = load_workflows(), load_dispositions()
    leads, rejections, q = {}, [], Counter()
    seen_ids, events = set(), []

    for drop in drops:
        for wf_dir in sorted((drop / "leads").iterdir()):
            wf = workflows[wf_dir.name]
            for path in sorted(wf_dir.glob("*.csv")):
                rows = list(csv.reader(open(path, newline="", encoding="utf-8-sig")))
                names = {}
                for f in wf.fields:
                    for k in [f.name] + f.aliases:
                        names.setdefault(normalize_header(k), f.name)
                cols = {}
                for i, h in enumerate(rows[0]):
                    n = names.get(normalize_header(h))
                    if n and n not in cols:
                        cols[n] = i
                rn = 0
                for row in rows[1:]:
                    if not any(c.strip() for c in row):
                        continue
                    rn += 1
                    q["lead_rows_total"] += 1
                    vals, warn, bad = {}, 0, None
                    for f in wf.fields:
                        raw = row[cols[f.name]].strip() if f.name in cols and cols[f.name] < len(row) else ""
                        if not raw:
                            if f.required:
                                bad = f"missing_required:{f.name}"
                                break
                            continue
                        v = parse_value(f, raw)
                        if v is None:
                            if f.type == "phone":
                                bad = "invalid_phone"
                                break
                            warn += 1
                        else:
                            vals[f.name] = v
                    if bad:
                        q["lead_rows_rejected"] += 1
                        q["reason:" + bad] += 1
                        rejections.append((wf.workflow_id, path.stem, rn, bad))
                        continue
                    q["field_warnings"] += warn
                    key = (wf.workflow_id, vals[wf.phone_field.name])
                    if key not in leads:
                        leads[key] = {"batch": path.stem, "vals": {}}
                    else:
                        q["duplicate_lead_rows_merged"] += 1
                    leads[key]["vals"].update(vals)
        for path in sorted((drop / "events").glob("*.jsonl")):
            for line in open(path, encoding="utf-8"):
                if not line.strip():
                    continue
                q["events_total"] += 1
                try:
                    e = json.loads(line)
                    e["event_id"]
                except Exception:
                    q["malformed_events"] += 1
                    continue
                if e["event_id"] in seen_ids:
                    q["duplicate_events_dropped"] += 1
                    continue
                seen_ids.add(e["event_id"])
                e["us"] = to_micros(parse_event_ts(e["occurred_at"]))
                events.append(e)

    # link
    def lead_of(e):
        p = normalize_phone(e.get("phone"))
        return (e["workflow_id"], p) if p and (e["workflow_id"], p) in leads else None

    call_lead = {}
    for e in events:
        if e["event_type"] != "disposition.override" and lead_of(e):
            call_lead.setdefault((e["workflow_id"], e["call_id"]), lead_of(e))
    by_call = {}
    for e in events:
        if e["event_type"] == "disposition.override":
            k = call_lead.get((e["workflow_id"], e["call_id"]))
        else:
            k = lead_of(e)
        if k is None:
            q["orphan_events"] += 1
            continue
        if e["event_type"].startswith("disposition.") and e["payload"]["disposition_code"] not in disp[e["workflow_id"]]:
            q["unknown_disposition_events"] += 1
            continue
        by_call.setdefault((e["workflow_id"], e["call_id"]), []).append(e)

    calls_of = {k: [] for k in leads}
    for (wf, cid), evs in by_call.items():
        inits = [e for e in evs if e["event_type"] == "call.initiated"]
        if not inits:
            continue
        sets = sorted((e for e in evs if e["event_type"] == "disposition.set"), key=lambda e: e["us"])
        ovrs = sorted((e for e in evs if e["event_type"] == "disposition.override"), key=lambda e: e["us"])
        eff = (ovrs[-1] if ovrs else sets[-1] if sets else None)
        calls_of[call_lead[(wf, cid)]].append({
            "init": inits[0]["us"], "cid": cid,
            "conn": any(e["event_type"] == "call.connected" for e in evs),
            "fail": any(e["event_type"] == "call.telephony_failed" for e in evs),
            "eff": eff["payload"]["disposition_code"] if eff else None})

    lead_rows, batch, daily = [], {}, {}
    for (wf, phone), L in leads.items():
        cs = sorted(calls_of[(wf, phone)], key=lambda c: (c["init"], c["cid"]))
        cat = disp[wf]
        if not cs:
            b = "yet_to_reach_out"
        elif any(c["eff"] and cat[c["eff"]].is_terminal for c in cs):
            b = "completed"
        elif all(c["fail"] for c in cs):
            b = "in_progress_telephony_failed"
        else:
            b = "in_progress_dialed"
        batch.setdefault((wf, L["batch"]), Counter())[b] += 1
        with_disp = [c for c in cs if c["eff"]]
        lead_rows.append({
            "workflow_id": wf, "external_ref": L["vals"][workflows[wf].reference_field.name],
            "batch_id": L["batch"], "phone_masked": "+91******" + phone[-4:], "bucket": b,
            "total_calls": len(cs), "connected_calls": sum(c["conn"] for c in cs),
            "last_call_at": micros_to_ist(cs[-1]["init"]).isoformat(timespec="seconds") if cs else "",
            "current_disposition": with_disp[-1]["eff"] if with_disp else "",
            "ever_success": "true" if any(c["eff"] and cat[c["eff"]].is_success for c in cs) else "false"})
        for c in cs:
            day = micros_to_ist(c["init"]).date().isoformat()
            d = daily.setdefault((wf, day), {"t": 0, "c": 0, "s": set()})
            d["t"] += 1
            d["c"] += c["conn"]
            if c["eff"] and cat[c["eff"]].is_success:
                d["s"].add(phone)

    lead_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"], r["batch_id"], r["phone_masked"]))
    return {
        "lead_report": lead_rows,
        "batch_summary": [{"workflow_id": w, "batch_id": b, "total_leads": sum(c.values()),
                           **{k: c[k] for k in ["yet_to_reach_out", "in_progress_telephony_failed",
                                                "in_progress_dialed", "completed"]}}
                          for (w, b), c in sorted(batch.items())],
        "daily_overview": [{"workflow_id": w, "date": d, "total_calls": v["t"], "connected_calls": v["c"],
                            "connectivity_rate": f"{v['c'] / v['t']:.4f}", "success_leads": len(v["s"])}
                           for (w, d), v in sorted(daily.items())],
        "rejections": [dict(zip(("workflow_id", "batch_id", "row_number", "reason"), r))
                       for r in sorted(rejections)],
        "quality": q,
    }
