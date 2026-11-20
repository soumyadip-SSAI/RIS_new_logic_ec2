# db_push: RIS KPI tables -> DB

A standalone script, not part of the `ris-kpi` pipeline. It pushes the latest gold tables into Postgres:

| Source (S3) | DB table | Not pushed |
|---|---|---|
| `s3://dhms-new-stgae/DHMS/NSV/Gold/Latest/ris_view_data.csv` | `ris_view_kpi` | — |
| `s3://dhms-new-stgae/DHMS/NSV/Gold/Latest/ris_trends_data.csv` | `ris_trends_kpi` | `phase`, `cycle` |

Rows are matched on `id`:
- **`id` already in the DB:** UPDATE every pushed column except `id` and `created_at`.
- **`id` not in the DB:** INSERT the whole row.

Every value is pushed as it is in the CSV, including `created_at` / `updated_at`. Run `ris-kpi sync-db-ids` after
`ris-kpi run` and before this push, so rows already in the DB carry their DB `id` / `created_at` and are UPDATEd
rather than inserted a second time.

Only columns that exist in both the CSV and the DB table are pushed. The others are skipped and listed in the output.

## Setup (once)

```bash
source .venv/bin/activate            # the project venv
pip install -r db_push/requirements.txt
cp db_push/.env.example db_push/.env # then fill in DB_HOST, DB_NAME, DB_USER, DB_PASSWORD (.env is not committed)
```

## Run

```bash
python db_push/push_to_db.py                     # DRY RUN (default): nothing is written
python db_push/push_to_db.py --dry-run false     # insert / update in the DB
python db_push/push_to_db.py --only ris_view     # one table (ris_view / ris_trends)
python db_push/push_to_db.py --view-csv a.csv --trends-csv b.csv   # local CSVs instead of S3
```

You can also change the default with `DRY_RUN = True` at the top of `push_to_db.py`.

**Dry run.** It only reads (SELECT) and checks everything the DB would check on the real write:
- every value cast to its column type;
- NOT NULL columns;
- foreign keys (`survey_id`, `upc_id`, ...).

Then it prints the plan and saves it to `db_push/dry_run_output/`:
- **`<table>_insert.csv`:** the rows that would be inserted.
- **`<table>_update_changes.csv`:** one line per changed cell (`id, column, db_value, new_value`). Values are compared in the DB after the cast, so `1.0` and `1` count as the same.

If a check fails (lines starting with `!!`), the run exits with code 1. A live run then writes nothing.

**Live run (`--dry-run false`).** It runs the same checks, then writes both tables in **one transaction**. If any statement fails, everything is rolled back and the DB is left unchanged.

The EC2 role needs `s3:GetObject` on `DHMS/NSV/Gold/Latest/`, and the instance needs network access to the RDS host on port 5432.
