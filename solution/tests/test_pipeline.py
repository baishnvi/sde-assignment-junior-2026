import pytest

from .helpers import HDR, Env, ev, jsonl, write

P = "9876543210"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def one_lead_drop(tmp_path, events, name="drop_1", phone=P):
    d = tmp_path / name
    write(d / "leads" / "motor_renewal" / "batch_001.csv", HDR + f"Asha Rao,{phone},POL1,01/12/2026,Rs 100,hi\n")
    write(d / "events" / "events_0001.jsonl", jsonl(*events))
    return d


def lead(rep):
    assert len(rep["lead_report"]) == 1
    return rep["lead_report"][0]


def test_bucket_yet_to_reach_out(env, tmp_path):
    env.ingest(one_lead_drop(tmp_path, []))
    r = lead(env.report())
    assert (r["bucket"], r["total_calls"], r["last_call_at"], r["current_disposition"]) == \
        ("yet_to_reach_out", 0, "", "")


def test_bucket_telephony_failed_only_when_every_call_failed(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.telephony_failed", "c1", "2026-10-01T10:00:03Z", reason="sip_503"),
        ev("e3", "call.initiated", "c2", "2026-10-02T10:00:00Z"),
        ev("e4", "call.telephony_failed", "c2", "2026-10-02T10:00:03Z", reason="sip_503"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    assert lead(env.report())["bucket"] == "in_progress_telephony_failed"


def test_bucket_dialed_when_one_call_did_not_fail(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.telephony_failed", "c1", "2026-10-01T10:00:03Z", reason="x"),
        ev("e3", "call.initiated", "c2", "2026-10-02T10:00:00Z"),
        ev("e4", "call.ended", "c2", "2026-10-02T10:00:30Z", duration_sec=0, end_reason="no_answer"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    assert lead(env.report())["bucket"] == "in_progress_dialed"


def test_completed_beats_telephony_failed_and_uses_terminal_flag(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="not_interested"),
        ev("e4", "call.initiated", "c2", "2026-10-02T10:00:00Z"),
        ev("e5", "call.telephony_failed", "c2", "2026-10-02T10:00:02Z", reason="x"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    r = lead(env.report())
    assert r["bucket"] == "completed" and r["ever_success"] == "false"
    assert r["current_disposition"] == "not_interested"


def test_non_terminal_disposition_is_not_completed(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="callback_requested"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    assert lead(env.report())["bucket"] == "in_progress_dialed"


def test_override_wins_without_touching_original_and_latest_override_wins(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="not_interested"),
        ev("e4", "disposition.override", "c1", "2026-10-02T10:00:00Z", phone=None, disposition_code="callback_requested"),
        ev("e5", "disposition.override", "c1", "2026-10-03T10:00:00Z", phone=None, disposition_code="renewed"),
        # arrives earlier in the file but is older by occurred_at: must lose
        ev("e0", "disposition.override", "c1", "2026-10-01T23:00:00Z", phone=None, disposition_code="dnd"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    rep = env.report()
    r = lead(rep)
    assert r["current_disposition"] == "renewed" and r["ever_success"] == "true"
    assert env.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='disposition.set'").fetchone()[0] == 1
    assert rep["data_quality"]["orphan_events"] == 0


def test_latest_set_wins_by_occurred_at_not_file_order(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e4", "disposition.set", "c1", "2026-10-01T10:09:00Z", disposition_code="payment_link_sent"),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="not_interested"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    assert lead(env.report())["current_disposition"] == "payment_link_sent"


def test_unknown_disposition_ignored_and_counted(env, tmp_path):
    events = [
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="renewed"),
        ev("e4", "disposition.override", "c1", "2026-10-02T10:00:00Z", phone=None, disposition_code="NOPE"),
        ev("e5", "disposition.set", "c1", "2026-10-02T10:00:00Z", disposition_code="policy_feedback_only"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    rep = env.report()
    assert lead(rep)["current_disposition"] == "renewed"  # bogus override ignored
    assert rep["data_quality"]["unknown_disposition_events"] == 2


def test_catalogue_is_per_workflow(env, tmp_path):
    # 'feedback_captured' exists only for policy_feedback, so it is unknown for motor_renewal
    events = [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
              ev("e2", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="feedback_captured")]
    env.ingest(one_lead_drop(tmp_path, events))
    assert env.report()["data_quality"]["unknown_disposition_events"] == 1


def test_call_without_initiated_is_not_counted(env, tmp_path):
    events = [ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
              ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", disposition_code="renewed")]
    env.ingest(one_lead_drop(tmp_path, events))
    rep = env.report()
    assert lead(rep)["total_calls"] == 0 and lead(rep)["bucket"] == "yet_to_reach_out"
    assert rep["daily_overview"] == [] and rep["data_quality"]["orphan_events"] == 0


def test_daily_overview_uses_ist_date_and_success_leads(env, tmp_path):
    events = [  # 19:00Z on the 3rd = 00:30 IST on the 4th
        ev("e1", "call.initiated", "c1", "2026-10-03T19:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-03T19:00:05Z"),
        ev("e3", "disposition.set", "c1", "2026-10-03T19:05:00Z", disposition_code="renewed"),
        ev("e4", "call.initiated", "c2", "2026-10-04 11:00:00"),  # naive => IST, same IST date
        ev("e5", "call.ended", "c2", "2026-10-04 11:00:30", duration_sec=0, end_reason="busy"),
    ]
    env.ingest(one_lead_drop(tmp_path, events))
    rows = env.report()["daily_overview"]
    assert rows == [{"workflow_id": "motor_renewal", "date": "2026-10-04", "total_calls": 2,
                     "connected_calls": 1, "connectivity_rate": "0.5000", "success_leads": 1}]


def test_orphan_events_resolve_when_lead_arrives_later(env, tmp_path):
    d1 = tmp_path / "drop_1"
    write(d1 / "events" / "events_0001.jsonl", jsonl(
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z"),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z"),
        ev("e3", "disposition.override", "c1", "2026-10-02T10:00:00Z", phone=None, disposition_code="renewed")))
    env.ingest(d1)
    rep = env.report()
    assert rep["data_quality"]["orphan_events"] == 3 and rep["lead_report"] == []

    d2 = tmp_path / "drop_2"
    write(d2 / "leads" / "motor_renewal" / "batch_003.csv", HDR + f"Asha Rao,{P},POL1,,,\n")
    env.ingest(d2)
    rep = env.report()
    assert rep["data_quality"]["orphan_events"] == 0
    r = lead(rep)
    assert r["batch_id"] == "batch_003" and r["total_calls"] == 1 and r["ever_success"] == "true"


def test_override_for_unknown_call_is_orphan(env, tmp_path):
    env.ingest(one_lead_drop(tmp_path, [ev("e1", "disposition.override", "ghost", "2026-10-02T10:00:00Z",
                                           phone=None, disposition_code="renewed")]))
    assert env.report()["data_quality"]["orphan_events"] == 1


def test_event_with_invalid_phone_or_other_workflow_is_orphan(env, tmp_path):
    events = [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", phone="12345"),
              ev("e2", "call.initiated", "c2", "2026-10-01T10:00:00Z", wf="policy_feedback")]
    env.ingest(one_lead_drop(tmp_path, events))
    rep = env.report()
    assert rep["data_quality"]["orphan_events"] == 2 and lead(rep)["total_calls"] == 0


def test_malformed_and_duplicate_events(env, tmp_path):
    good = ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z")
    d = one_lead_drop(tmp_path, [good, good, '{"event_id": "e9", "event_ty', "not json", '{"no_id": 1}',
                                 "[1, 2]", "   ", ev("e1", "call.initiated", "cX", "2026-10-09T10:00:00Z")])
    env.ingest(d)
    q = env.report()["data_quality"]
    assert q["events_total"] == 7           # blank line is not counted
    assert q["malformed_events"] == 4
    assert q["duplicate_events_dropped"] == 2
    assert lead(env.report())["total_calls"] == 1   # first copy wins


def test_duplicate_event_across_drops(env, tmp_path):
    e = ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z")
    env.ingest(one_lead_drop(tmp_path, [e]))
    d2 = tmp_path / "drop_2"
    write(d2 / "events" / "events_1001.jsonl", jsonl(e))
    env.ingest(d2)
    q = env.report()["data_quality"]
    assert q["duplicate_events_dropped"] == 1 and q["events_total"] == 2


def test_lead_rejection_reasons_row_numbers_and_blank_rows(env, tmp_path):
    csv = HDR + "\n".join([
        ",,,,,",                                   # blank: skipped, not counted
        f"Asha,{P},POL1,01/12/2026,Rs 100,hi",      # row 1 ok
        "  ,12345,POL2,,,",                         # row 2: first failure in schema order = name
        "Ben,12345,POL3,,,",                        # row 3: invalid_phone
        f"Cy,{'9876543211'},,,,",                   # row 4: missing policy_number
        ",,,,,",                                    # blank again
        "Dee,,POL5,,,",                             # row 5: missing phone
    ]) + "\n"
    d = tmp_path / "drop_1"
    write(d / "leads" / "motor_renewal" / "batch_001.csv", csv)
    env.ingest(d)
    rep = env.report()
    assert [(r["row_number"], r["reason"]) for r in rep["rejections"]] == [
        (2, "missing_required:customer_name"), (3, "invalid_phone"),
        (4, "missing_required:policy_number"), (5, "missing_required:phone")]
    assert rep["data_quality"]["lead_rows_total"] == 5 and rep["data_quality"]["lead_rows_rejected"] == 4
    assert len(rep["lead_report"]) == 1


def test_warnings_only_for_non_rejected_rows_and_defaults(env, tmp_path):
    csv = HDR + "\n".join([
        f"Asha,{P},POL1,31/02/2026,N/A,Hindi",      # 3 warnings, language falls back to default
        "Ben,12345,POL2,31/02/2026,N/A,Hindi",      # rejected: its warnings must not count
    ]) + "\n"
    d = tmp_path / "drop_1"
    write(d / "leads" / "motor_renewal" / "batch_001.csv", csv)
    env.ingest(d)
    assert env.report()["data_quality"]["field_warnings"] == 3
    import json
    attrs = json.loads(env.conn.execute("SELECT attrs FROM leads").fetchone()[0])
    assert attrs == {"policy_number": "POL1", "preferred_language": "en"}


def test_duplicate_merge_last_non_null_wins_and_batch_id_sticks(env, tmp_path):
    d1 = tmp_path / "drop_1"
    write(d1 / "leads" / "motor_renewal" / "batch_001.csv",
          HDR + f"Asha,{P},POL1,01/12/2026,Rs 100,hi\nAsha R,+91 {P[:5]} {P[5:]},POL1,,Rs 250,\n")
    d2 = tmp_path / "drop_2"   # later batch, renamed/reordered columns
    write(d2 / "leads" / "motor_renewal" / "batch_002.csv",
          "Mobile No,Premium,Insured Name,Policy No,extra\n" f"0{P},,Asha Rao,POL9,x\n")
    env.ingest(d1)
    env.ingest(d2)
    import json
    row = env.conn.execute("SELECT batch_id, external_ref, attrs FROM leads").fetchone()
    assert row[0] == "batch_001" and row[1] == "POL9"
    attrs = json.loads(row[2])
    assert attrs["premium_amount"] == "250.00" and attrs["preferred_language"] == "hi"
    assert attrs["policy_expiry"] == "2026-12-01"
    assert env.report()["data_quality"]["duplicate_lead_rows_merged"] == 2
    assert env.conn.execute("SELECT name FROM sqlite_master WHERE name='leads'").fetchone()
    # PII: name decrypts to the last non-null value
    pii = json.loads(env.conn.execute("SELECT pii FROM leads").fetchone()[0])
    assert env.vault.decrypt(pii["customer_name"]) == "Asha Rao"


def test_no_plaintext_pii_anywhere_in_database_files(env, tmp_path):
    env.ingest(one_lead_drop(tmp_path, [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", phone="+91 98765 43210")]))
    env.conn.execute("PRAGMA wal_checkpoint(FULL)")
    blob = b"".join(p.read_bytes() for p in env.db_path.parent.glob("ledger.db*") if not p.name.endswith(".key"))
    for needle in (b"Asha", b"9876543210", b"98765 43210", b"+919876543210"):
        assert needle not in blob


def test_reingest_changes_nothing(env, tmp_path):
    d = one_lead_drop(tmp_path, [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z")])
    env.ingest(d)
    before, rep_before = env.dump(), env.report()
    s = env.ingest(d)
    assert s == {"ingested": 0, "skipped": 2}
    assert env.dump() == before and env.report() == rep_before


def test_changed_file_is_refused_and_rolled_back(env, tmp_path):
    from solution.pipeline import LedgerError
    d = one_lead_drop(tmp_path, [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z")])
    env.ingest(d)
    before = env.dump()
    write(d / "events" / "events_0001.jsonl", jsonl(ev("e2", "call.initiated", "c1", "2026-10-01T10:00:00Z")))
    write(d / "events" / "events_0002.jsonl", jsonl(ev("e3", "call.initiated", "c1", "2026-10-01T10:00:00Z")))
    with pytest.raises(LedgerError):
        env.ingest(d)
    assert env.dump() == before   # new file in the same drop was not half-applied


def test_ledger_is_append_only(env, tmp_path):
    import sqlite3
    env.ingest(one_lead_drop(tmp_path, [ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z")]))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        env.conn.execute("UPDATE events SET call_id='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        env.conn.execute("DELETE FROM events")
