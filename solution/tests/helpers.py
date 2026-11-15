"""Tiny builders for hand-written fixtures."""
import json
from pathlib import Path

from solution import config, db, pii, pipeline, reports

HDR = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ev(eid, etype, call, ts, phone="9876543210", wf="motor_renewal", **payload):
    d = {"event_id": eid, "event_type": etype, "workflow_id": wf, "call_id": call, "occurred_at": ts,
         "payload": payload}
    if phone is not None:
        d["phone"] = phone
    return json.dumps(d)


def jsonl(*lines):
    return "\n".join(lines) + "\n"


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.db_path = tmp_path / "state" / "ledger.db"
        self.workflows = config.load_workflows()
        self.dispositions = config.load_dispositions()
        self.conn = db.connect(self.db_path)
        self.vault = pii.PiiVault(self.db_path.with_name("ledger.db.key"))

    def ingest(self, drop):
        return pipeline.ingest_drop(self.conn, self.vault, self.workflows, drop, log=lambda *_: None)

    def report(self):
        return reports.compute(self.conn, self.dispositions)

    def dump(self):
        out = {}
        for t in ("ingest_files", "leads", "rejections", "events"):
            out[t] = self.conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
        return out
