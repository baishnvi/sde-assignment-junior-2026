# Assignment A solution: Interaction Ledger & Reporting Pipeline

See `../SUBMISSION.md` for design, decisions and results.

```bash
pip install -r requirements.txt
python -m solution ingest data/drop_1 data/drop_2
python -m solution report --out reports/
python -m pytest solution
```

| file | role |
|---|---|
| `normalize.py` | phone / date / decimal / enum / header / timestamp rules (pure functions) |
| `config.py` | loads workflow schemas and `dispositions.csv` |
| `pii.py` | HMAC phone hashing + Fernet field encryption |
| `db.py` | SQLite DDL, append-only triggers |
| `leads.py` | lead CSV → validate → reject / merge |
| `events.py` | event JSONL → malformed / duplicate → ledger |
| `pipeline.py` | per-drop transaction + file registry (idempotency) |
| `reports.py` | derives the five report files from the final state |
| `cli.py` | `ingest` and `report` commands |
| `tests/` | unit tests, scenarios, generator cross-check (`oracle.py`) |
