# Submission

## Candidate
- **Name:** Baishnvi Goswami
- **Assignment chosen:** A (Data): Interaction Ledger & Reporting Pipeline
- **Time spent (honest estimate):** _EDIT: fill in your real number_

## How to run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2          # or: ingest data/drop_1 data/drop_2
python -m solution report --out reports/

python -m pytest solution                       # all tests, ~5 s
```
Defaults: database `.ledger/ledger.db` (SQLite) and its PII key `.ledger/ledger.db.key`; the
`.ledger/` folder ignores itself in git. Override with `--db PATH` (before the sub-command).
Use a fresh `--db` (or delete `.ledger/`) when switching to data from a different seed.

## Design
```
CSV leads ──► header map ► validate/normalise ► reject | merge ──► leads (PII hashed/encrypted)
JSONL events ─► malformed? ► duplicate? ► normalise ts + phone ───► events (append-only ledger)
                                   ingest_files = registry of what was ingested + per-file counters
reports = pure function of (leads, events, rejections, ingest_files)   -- computed on every run
```
**Tables** (`solution/db.py`):
- `ingest_files`: one row per file, `UNIQUE(drop_name, rel_path)` + sha256 + the per-file counters
  (rows, rejected, merged, warnings, malformed, duplicates). This is the idempotency registry.
- `leads`: `UNIQUE(workflow_id, phone_hash)` = the lead identity. `phone_hash` is an HMAC-SHA256 of
  the canonical phone, `phone_last4` is kept only to produce `phone_masked`, `attrs` is JSON of
  non-PII fields, `pii` is JSON of Fernet-encrypted PII fields (name). `batch_id` is set once.
- `rejections`: `(file_id, workflow_id, batch_id, row_number, reason)`: no values, so no PII.
- `events`: the ledger. `event_id UNIQUE` (cross-drop dedupe), UTC microseconds for ordering, the raw
  timestamp string for audit, `phone_hash` (NULL for overrides / invalid phones). SQLite triggers
  `RAISE(ABORT)` on any `UPDATE` or `DELETE`, so an override can only ever be a new row.

**Why linking is not stored.** An event is joined to its lead at report time on
`(workflow_id, phone_hash)`, and an override through its `call_id`. Because nothing about linking
is persisted, "event arrives before its lead" needs no special case or reprocessing: the next
`report` simply sees the lead. The same property makes reports independent of how the data was split
into drops (tested: events-first-then-leads gives byte-identical reports).

**PII.** Phones are matched via a *keyed* hash (HMAC), so the ~10^9 possible numbers cannot be
brute-forced from a stolen DB without the key. Names are Fernet-encrypted (reversible by support
staff holding the key). Raw phones from events are hashed before the insert and never stored. A test
greps the SQLite/WAL files for the name and every phone format. The key comes from `LEDGER_PII_KEY`
or, for local dev only, a 0600 file next to the DB.

**Idempotency / atomicity.** Each drop is one transaction (`BEGIN IMMEDIATE`). A file already in
`ingest_files` is skipped; if its content hash changed we abort and roll back instead of silently
double counting. A crash mid-drop leaves the DB unchanged.

**Reports** (`solution/reports.py`) stream the events once through a `LEFT JOIN` on leads, keep a small
state object per call, then derive buckets, daily rows and the lead report.

## Decisions & assumptions
| Decision | Why | Alternative considered |
|---|---|---|
| Orphans, unknown dispositions, linking are computed at report time from final state | Spec says counts reflect final state; avoids a relink step | Store `lead_id` on events and relink on each lead ingest (more writes, easy to get stale) |
| Order of event checks: malformed → duplicate → orphan → unknown, so an orphan unknown-disposition event counts only as orphan | Matches the numbered order in the spec | Count in both |
| Extra malformed cases: event without `event_type`, `workflow_id`, `call_id`, or with unparseable `occurred_at` | We can't derive anything from them; never occurs in generated data | Store them and fail later |
| Unknown `event_type`s are stored in the ledger and ignored by reports | Ledger keeps everything that is well-formed | Reject |
| Override with no linked call (or call with no linked lead) = orphan | Spec: override links only through its call | |
| Ties on `occurred_at` between dispositions broken by larger `event_id` | Deterministic output regardless of file order | Arrival order (not stable across drop splits) |
| If a call has several `call.initiated`, the earliest one defines the call | Defensive; doesn't occur in generated data | Count as two calls |
| `batch_summary` only has rows for batches that own at least one lead | It is derived from leads; a file whose rows were all rejected/merged elsewhere has no leads | Emit zero rows |
| Header matching: if two columns map to one field, the first wins | Deterministic | Last wins / error |
| A non-phone required field that is present but invalid → `invalid_value:<field>` | The spec defines only two reasons and the shipped schemas never need this | Treat as missing |
| Decimal rounding is half-up (`12.345 → 12.35`) | Money convention | Python's banker's rounding |
| Day/month may be 1 or 2 digits (`1/2/2026`) | Lenient, still day-first | Require 2 digits |
| Disposition codes compared exactly (case-sensitive) | Spec: "not in dispositions.csv" | Case-insensitive |
| `rejections_by_reason` lists only reasons that occurred, keys sorted | Example in the spec is a shape illustration | Pre-populate every possible reason with 0 |
| Defaults are applied when the lead is created; later non-null values still overwrite | Equivalent to "after merging, to fields still null" | Apply at read time |
| Same file path ingested again with different bytes → error + rollback | Silent re-ingest would double count | Treat as new file |

## Failure modes considered
- **Dirty leads:** six phone formats, bad phones, missing required fields, mixed dates/currencies,
  blank rows, renamed/re-ordered/extra columns, BOM, short rows: all covered by unit tests and the oracle.
- **Dirty events:** truncated JSON, non-object JSON, duplicates inside a file and across drops,
  out-of-order timestamps, three timestamp forms, overrides without phone, unknown codes.
- **Re-runs:** re-ingesting a drop is a no-op (tested by dumping every table before/after).
- **Crash mid-ingest:** one transaction per drop, rolled back on any exception.
- **Not handled:** concurrent ingests (SQLite write lock serialises them but nothing coordinates
  drop order); a file edited in place is refused rather than diffed; schema changes after leads exist
  (old leads keep their old `attrs`); key rotation for the PII key; `phone_last4` is stored in clear.

## Testing
- `test_normalize.py`: phone (valid/invalid formats), dates (day-first, leap days, impossible dates),
  decimals, headers, the three timestamp forms and the IST date boundary.
- `test_pipeline.py`: hand-written scenarios for each bucket, effective disposition (override beats set,
  latest wins by `occurred_at` not file order, bogus override ignored), orphans resolving when the lead
  arrives in a later drop, malformed/duplicate events, rejection reasons and row numbers, merge rules,
  warnings only on non-rejected rows, no plaintext PII in DB files, idempotency, rollback, append-only triggers.
- `test_generated.py`: runs the provided generator on seeds 42, 7 and 2024 and compares **every report
  table** with `oracle.py`, a deliberately naive database-free re-implementation of the spec; plus
  incremental == all-at-once, events-before-leads, and a CLI end-to-end check of the output contract.
- Not tested: PostgreSQL (SQLite only), concurrent writers, huge files beyond the large dataset.
- Honest limitation: the oracle and the pipeline were written by the same author from the same reading
  of the spec, so a misreading of the spec would be in both. That is what the decision table is for.

## Results
`--scale large --seed 42` (≈144k lead rows, ≈633k event lines), on a sandbox VM:
| step | time |
|---|---|
| `ingest drop_1 drop_2` | ~32 s |
| `report` | ~5.5 s |
| re-ingest (all skipped) | 0.2 s |

Seed 42 / small: 2,057 lead rows → 108 rejected (42 `invalid_phone`), 57 merged duplicates, 8,745 event
lines → 8 malformed, 237 duplicates, 462 orphans, 18 unknown dispositions. The README's examples
(`0.5625` connectivity on 2026-10-01, `last_call_at 2026-10-14T17:55:24+05:30`) appear in our output.
Surprising: ~460 orphans are not only "numbers never uploaded": leads that were *rejected* (e.g. missing
name) still get calls, and so do leads with an invalid phone.

## If I had another day
1. Truly incremental reports (maintain per-call/lead aggregates on ingest; trade-off: need re-derivation when an
   old call gets a late override, so keep the full recompute as a verification job).
2. Move aggregation into SQL (window functions / CTEs) and run on Postgres via docker-compose.
3. Production notes for 100k leads/day: object-store drops + a queue, one worker per workflow with the
   per-file registry as the lock, KMS-managed keys with rotation (store a key id per row), metrics on
   reject/orphan rates with alerts, backfills by replaying files into a fresh DB and diffing reports.
4. Property-based tests (hypothesis) for the normalisers.
