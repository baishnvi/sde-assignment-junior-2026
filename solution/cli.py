"""Command line interface.

    python -m solution ingest data/drop_1 [data/drop_2 ...]
    python -m solution report --out reports/

Global options (before the sub-command): --db PATH (default .ledger/ledger.db), --schemas DIR.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config, db, pii, pipeline, reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m solution", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=".ledger/ledger.db", help="SQLite database path")
    ap.add_argument("--schemas", default=str(config.DEFAULT_SCHEMA_DIR), help="schemas directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="ingest one or more drop directories, in the order given")
    p_ing.add_argument("drops", nargs="+")
    p_rep = sub.add_parser("report", help="write the five report files")
    p_rep.add_argument("--out", default="reports")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    workflows = config.load_workflows(Path(args.schemas))
    dispositions = config.load_dispositions(Path(args.schemas))
    conn = db.connect(db_path)
    t0 = time.time()
    try:
        if args.cmd == "ingest":
            vault = pii.PiiVault(db_path.with_name(db_path.name + ".key"))
            for d in args.drops:
                print(f"drop {d}")
                s = pipeline.ingest_drop(conn, vault, workflows, Path(d))
                print(f"  -> {s['ingested']} file(s) ingested, {s['skipped']} skipped")
        else:
            data = reports.write_reports(conn, dispositions, Path(args.out))
            print(f"wrote 5 reports to {args.out}/ "
                  f"({len(data['lead_report'])} leads, {len(data['daily_overview'])} daily rows)")
    except pipeline.LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"done in {time.time() - t0:.1f}s")
    return 0
