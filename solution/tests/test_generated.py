"""Cross-check against the provided data generator, on several seeds."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from . import oracle
from .helpers import Env

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "assignment-a-data" / "tools" / "generate_data.py"


def generate(tmp_path, seed):
    out = tmp_path / f"data_{seed}"
    subprocess.run([sys.executable, str(GEN), "--seed", str(seed), "--scale", "small", "--out", str(out)],
                   check=True, capture_output=True)
    return out


@pytest.mark.parametrize("seed", [42, 7, 2024])
def test_pipeline_matches_independent_oracle(tmp_path, seed):
    data = generate(tmp_path, seed)
    env = Env(tmp_path / "db")
    env.ingest(data / "drop_1")
    env.ingest(data / "drop_2")
    got = env.report()
    want = oracle.run([data / "drop_1", data / "drop_2"])
    for name in ("lead_report", "batch_summary", "daily_overview", "rejections"):
        assert got[name] == want[name], name
    q, w = got["data_quality"], want["quality"]
    for key in ("lead_rows_total", "lead_rows_rejected", "duplicate_lead_rows_merged", "field_warnings",
                "events_total", "malformed_events", "duplicate_events_dropped", "orphan_events",
                "unknown_disposition_events"):
        assert q[key] == w[key], key
    assert q["rejections_by_reason"] == {k[7:]: v for k, v in sorted(w.items()) if k.startswith("reason:")}
    # sanity: the messy data really exercises the interesting paths
    assert q["orphan_events"] > 0 and q["unknown_disposition_events"] > 0 and q["malformed_events"] > 0


def test_incremental_equals_all_at_once_and_reingest_is_noop(tmp_path):
    data = generate(tmp_path, 42)
    a, b = Env(tmp_path / "a"), Env(tmp_path / "b")
    a.ingest(data / "drop_1")
    a.ingest(data / "drop_2")
    b.ingest(data / "drop_1")             # b: identical input, ingested the same way in a fresh database
    b.ingest(data / "drop_2")
    snapshot = a.dump()
    a.ingest(data / "drop_1")
    a.ingest(data / "drop_2")
    assert a.dump() == snapshot and a.report() == b.report()


def test_events_first_then_leads_gives_same_report(tmp_path):
    """Event files of both drops loaded before any lead file: same final reports."""
    import shutil
    data = generate(tmp_path, 42)
    normal = Env(tmp_path / "n")
    normal.ingest(data / "drop_1")
    normal.ingest(data / "drop_2")

    ev_first = tmp_path / "ef"
    for d in ("drop_1", "drop_2"):
        shutil.copytree(data / d / "events", ev_first / f"{d}_events" / "events")
        (ev_first / f"{d}_leads").mkdir(parents=True)
        shutil.copytree(data / d / "leads", ev_first / f"{d}_leads" / "leads")
    env = Env(tmp_path / "x")
    for name in ("drop_1_events", "drop_2_events", "drop_1_leads", "drop_2_leads"):
        env.ingest(ev_first / name)
    got, want = env.report(), normal.report()
    for name in ("lead_report", "batch_summary", "daily_overview", "rejections"):
        assert got[name] == want[name], name
    assert got["data_quality"] == want["data_quality"]


def test_cli_end_to_end_writes_exact_contract(tmp_path):
    data = generate(tmp_path, 42)
    db = tmp_path / "s" / "ledger.db"
    base = [sys.executable, "-m", "solution", "--db", str(db)]
    for step in (["ingest", str(data / "drop_1")], ["ingest", str(data / "drop_2")],
                 ["ingest", str(data / "drop_1")], ["report", "--out", str(tmp_path / "reports")]):
        subprocess.run(base + step, check=True, capture_output=True, cwd=ROOT)
    out = tmp_path / "reports"
    assert sorted(p.name for p in out.iterdir()) == [
        "batch_summary.csv", "daily_overview.csv", "data_quality.json", "lead_report.csv", "rejections.csv"]
    assert (out / "daily_overview.csv").read_text().splitlines()[0] == \
        "workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads"
    assert (out / "lead_report.csv").read_text().splitlines()[0] == \
        "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success"
    dq = json.loads((out / "data_quality.json").read_text())
    assert list(dq) == ["lead_rows_total", "lead_rows_rejected", "rejections_by_reason",
                        "duplicate_lead_rows_merged", "field_warnings", "events_total", "malformed_events",
                        "duplicate_events_dropped", "orphan_events", "unknown_disposition_events"]
    assert "Aarav" not in (out / "rejections.csv").read_text()
