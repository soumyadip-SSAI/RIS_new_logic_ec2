"""Push the latest RIS KPI tables from S3 into the Postgres DB (standalone, not part of the ris_kpi pipeline).

    s3://.../DHMS/NSV/Gold/Latest/ris_view_data.csv    -> <schema>.ris_view_kpi
    s3://.../DHMS/NSV/Gold/Latest/ris_trends_data.csv  -> <schema>.ris_trends_kpi   (phase, cycle not pushed)

For every table:
  1. Columns: only the columns that exist both in the CSV and in the DB table are pushed; the others are skipped
     and listed (CSV columns missing from the DB, DB columns missing from the CSV).
  2. Rows are matched on `id`:
       id already in the DB -> UPDATE every pushed column except id and created_at
       id not in the DB     -> INSERT the whole row (pushed columns)
     Every value comes from the CSV as it is (run `ris-kpi sync-db-ids` first, so rows already in the DB carry their
     DB id / created_at).
  3. DRY_RUN (default True): nothing is written. The DB is only read (SELECT), every value is cast to its DB column type
     (so a value the DB would reject is reported now), and the planned changes are saved to dry_run_output/:
       <table>_insert.csv            rows that would be inserted
       <table>_update_changes.csv    one line per changed cell of the updated rows: id, column, db_value, new_value
     With DRY_RUN False, all tables are written in ONE transaction: committed only when every table succeeds,
     otherwise rolled back (the DB is left unchanged).

DB credentials come from environment variables, or from .env in the repository root (keys committed empty;
the values are filled in on the server only, never committed):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SCHEMA

Usage (from the repo root, with the project venv active):
    python db_push/push_to_db.py                      # dry run (DRY_RUN below)
    python db_push/push_to_db.py --dry-run false      # actually insert / update
    python db_push/push_to_db.py --only ris_view      # one table only (ris_view / ris_trends)
    python db_push/push_to_db.py --view-csv a.csv --trends-csv b.csv   # local CSVs instead of S3
"""
import argparse
import io
import logging
import os
import re
import sys
import uuid
from pathlib import Path

import boto3
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------------------------------
DRY_RUN = True                     # True: read and report only. False: insert / update in the DB (or --dry-run false)

# Folder with ris_view_data.csv / ris_trends_data.csv: <S3_GOLD_OUTPUT_DIR>/Latest from .env (read in main),
# else this default
DEFAULT_SOURCE_DIR = "s3://dhms-new-stgae/DHMS/NSV/Gold/Latest"
TABLES = {
    # name: CSV file in the source folder, DB table, CSV columns never pushed
    "ris_view": {"file": "ris_view_data.csv", "table": "ris_view_kpi", "drop": []},
    "ris_trends": {"file": "ris_trends_data.csv", "table": "ris_trends_kpi", "drop": ["phase", "cycle"]},
}
KEY = "id"                                 # rows are matched on this column
NOT_UPDATED = {KEY, "created_at"}          # never changed on an UPDATE
PAGE_SIZE = 1000                           # rows per SQL statement
HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"            # repository root .env (shared with the ris_kpi run settings)
PREVIEW_DIR = HERE / "dry_run_output"
SAMPLE_ROWS = 5                            # rows / changes printed per table

INT_TYPES = {"smallint", "integer", "bigint"}

log = logging.getLogger("push_to_db")


# ---------------------------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------------------------
def load_env(path=ENV_FILE):
    """KEY=VALUE lines of path into os.environ (variables already set win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def db_settings():
    missing = [k for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing DB settings {missing}: set them in {ENV_FILE} or as environment variables")
    return {"host": os.environ["DB_HOST"], "port": int(os.environ.get("DB_PORT") or "5432"),
            "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"], "password": os.environ["DB_PASSWORD"]}


def source_dir():
    """Folder of the CSVs: <S3_GOLD_OUTPUT_DIR>/Latest (same .env key as the ris_kpi run), else DEFAULT_SOURCE_DIR."""
    gold = (os.environ.get("S3_GOLD_OUTPUT_DIR") or "").strip().rstrip("/")
    return f"{gold}/Latest" if gold else DEFAULT_SOURCE_DIR


def read_csv(source):
    """CSV (S3 URI or local path) with every value as text; empty cells -> None."""
    if str(source).startswith("s3://"):
        bucket, key = str(source)[5:].split("/", 1)
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        df = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    else:
        df = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return df.astype(object).map(_text)


# ---------------------------------------------------------------------------------------------------------------------
# DB metadata
# ---------------------------------------------------------------------------------------------------------------------
def db_columns(cur, schema, table):
    """{column: {"type", "not_null", "has_default", "writable"}} in table order; {} when the table does not exist."""
    cur.execute("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.atthasdef,
               a.attgenerated = '' AND a.attidentity <> 'a'
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum""", (schema, table))
    return {name: {"type": typ, "not_null": not_null, "has_default": has_default, "writable": writable}
            for name, typ, not_null, has_default, writable in cur.fetchall()}


def existing_ids(cur, schema, table):
    cur.execute(sql.SQL("SELECT {}::text FROM {}.{}").format(sql.Identifier(KEY), sql.Identifier(schema),
                                                           sql.Identifier(table)))
    return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------------------------------------------------
def _text(v):
    """Text value, or None for an empty cell (pandas may hold an empty cell as None or NaN)."""
    return v if isinstance(v, str) and v.strip() != "" else None


def _int_text(v):
    """'12.0' -> '12' for integer columns (pandas writes whole numbers of float columns with .0)."""
    if _text(v) is None:
        return None
    m = re.fullmatch(r"\s*(-?\d+)\.0*\s*", v)
    return m.group(1) if m else v


def _uuid_text(v):
    """Canonical uuid text (lower case, with dashes), so CSV ids compare with the DB's id::text; invalid left as is."""
    if _text(v) is None:
        return None
    try:
        return str(uuid.UUID(v.strip()))
    except ValueError:
        return v


def normalise(df, columns):
    df = df.copy()
    for col in df.columns:
        typ = columns[col]["type"]
        if typ in INT_TYPES:
            df[col] = df[col].map(_int_text)
        elif typ == "uuid":
            df[col] = df[col].map(_uuid_text)
    return df


def rows_of(df, cols):
    return [tuple(_text(x) for x in row) for row in df[cols].itertuples(index=False, name=None)]


def chunks(items, size=PAGE_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def values_template(cols, columns):
    """(%s::type, ...) so every CSV text value is cast to its DB column type."""
    return "(" + ", ".join(f"%s::{columns[c]['type']}" for c in cols) + ")"


def ident_list(cols):
    return sql.SQL(", ").join(sql.Identifier(c) for c in cols)


# ---------------------------------------------------------------------------------------------------------------------
# Plan (read only)
# ---------------------------------------------------------------------------------------------------------------------
def plan_table(cur, name, cfg, schema, source):
    """Everything needed to push one table; None when it cannot be pushed (reason logged)."""
    table = cfg["table"]
    print(f"\n{'=' * 100}\n{name}: {source}  ->  {schema}.{table}\n{'=' * 100}")

    df = read_csv(source)
    dropped = [c for c in cfg["drop"] if c in df.columns]
    df = df.drop(columns=dropped)
    print(f"CSV: {len(df):,} rows, {len(df.columns)} columns" + (f" (not pushed: {dropped})" if dropped else ""))

    columns = db_columns(cur, schema, table)
    if not columns:
        print(f"!! Table {schema}.{table} not found - skipped")
        return None

    pushed = [c for c in df.columns if c in columns and columns[c]["writable"]]
    csv_only = [c for c in df.columns if c not in columns]
    not_writable = [c for c in df.columns if c in columns and not columns[c]["writable"]]
    db_only = [c for c in columns if c not in df.columns]
    print(f"Columns pushed ({len(pushed)}): {pushed}")
    if csv_only:
        print(f"!! CSV columns not in the DB table - skipped: {csv_only}")
    if not_writable:
        print(f"!! DB columns that cannot be written (generated / identity) - skipped: {not_writable}")
    if db_only:
        print(f"DB columns not in the CSV - left as they are (DB default on insert): {db_only}")
    if KEY not in pushed:
        print(f"!! '{KEY}' is not in both the CSV and the DB table - table skipped")
        return None
    df = normalise(df[pushed], columns)
    update_cols = [c for c in pushed if c not in NOT_UPDATED]

    no_id = df[KEY].isna()
    if no_id.any():
        print(f"!! {int(no_id.sum())} rows with an empty {KEY} - skipped")
        df = df[~no_id]
    dup = df[KEY].duplicated(keep=False)
    if dup.any():
        print(f"!! {int(dup.sum())} rows share an {KEY} in the CSV - table skipped:\n"
              f"{df.loc[dup].head(10).to_string(index=False)}")
        return None

    in_db = existing_ids(cur, schema, table)
    is_update = df[KEY].isin(in_db)
    inserts, updates = df[~is_update], df[is_update]
    print(f"DB table: {len(in_db):,} rows. Plan: INSERT {len(inserts):,} rows (new {KEY}), "
          f"UPDATE {len(updates):,} rows (matching {KEY}; columns {update_cols})")

    plan = {"name": name, "table": table, "schema": schema, "columns": columns, "pushed": pushed,
            "update_cols": update_cols, "update_values": [KEY] + update_cols,
            "inserts": inserts, "updates": updates, "ok": True}

    # NOT NULL checks (the DB would reject the statement)
    for col in pushed:
        if columns[col]["not_null"]:
            n_ins = int(inserts[col].isna().sum())
            n_upd = int(updates[col].isna().sum()) if col in update_cols else 0
            if n_ins or n_upd:
                plan["ok"] = False
                print(f"!! {col} is NOT NULL in the DB but empty in {n_ins} insert / {n_upd} update rows")
    required = [c for c in db_only if columns[c]["not_null"] and not columns[c]["has_default"]]
    if required and len(inserts):
        plan["ok"] = False
        print(f"!! DB columns NOT NULL without a default and not in the CSV - inserts will fail: {required}")

    check_foreign_keys(cur, plan)
    check_inserts(cur, plan)
    diff_updates(cur, plan)
    return plan


def foreign_keys(cur, schema, table):
    """[(column, referenced schema, referenced table, referenced column)] of the single-column foreign keys."""
    cur.execute("""
        SELECT a.attname, rn.nspname, rc.relname, ra.attname
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class rc ON rc.oid = k.confrelid JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
        JOIN pg_attribute ra ON ra.attrelid = k.confrelid AND ra.attnum = k.confkey[1]
        WHERE k.contype = 'f' AND n.nspname = %s AND c.relname = %s AND cardinality(k.conkey) = 1""",
                (schema, table))
    return cur.fetchall()


def check_foreign_keys(cur, plan):
    """Every pushed value of a foreign-key column must exist in the referenced table (else the DB rejects the row)."""
    rows = pd.concat([plan["inserts"], plan["updates"][plan["update_values"]]])
    for col, ref_schema, ref_table, ref_col in foreign_keys(cur, plan["schema"], plan["table"]):
        if col not in plan["pushed"]:
            continue
        values = sorted({v for v in rows[col] if isinstance(v, str)})
        if not values:
            continue
        cur.execute(sql.SQL("SELECT x FROM unnest(%s::text[]) AS x WHERE NOT EXISTS "
                            "(SELECT 1 FROM {}.{} r WHERE r.{}::text = x)").format(
            sql.Identifier(ref_schema), sql.Identifier(ref_table), sql.Identifier(ref_col)), (values,))
        missing = [r[0] for r in cur.fetchall()]
        if missing:
            plan["ok"] = False
            n_rows = int(rows[col].isin(missing).sum())
            print(f"!! {col}: {len(missing)} value(s) not in {ref_schema}.{ref_table}.{ref_col} "
                  f"({n_rows} rows would be rejected): {missing[:10]}")
        else:
            print(f"Foreign key {col} -> {ref_table}.{ref_col}: all {len(values)} value(s) found")


def check_inserts(cur, plan):
    """Cast every insert value to its DB type (SELECT only): a value the DB would reject is reported here."""
    inserts, pushed = plan["inserts"], plan["pushed"]
    if inserts.empty:
        return
    query = sql.SQL("SELECT count(*) FROM (VALUES %s) AS v({})").format(ident_list(pushed)).as_string(cur)
    try:
        for part in chunks(rows_of(inserts, pushed)):
            execute_values(cur, query, part, template=values_template(pushed, plan["columns"]), page_size=len(part))
        print(f"Insert values: all {len(inserts):,} rows cast to the DB column types")
    except psycopg2.Error as e:
        cur.connection.rollback()
        plan["ok"] = False
        print(f"!! Insert values rejected by the DB: {str(e).strip()}")


def diff_updates(cur, plan):
    """Per updated row and column: DB value vs new value (compared in the DB after the cast, so 1.0 = 1)."""
    updates, cols = plan["updates"], plan["update_values"]
    compared = plan["update_cols"]
    plan["changes"] = pd.DataFrame(columns=[KEY, "column", "db_value", "new_value"])
    if updates.empty or not plan["update_cols"]:
        return
    t, v = sql.Identifier("t"), sql.Identifier("v")
    selects = sql.SQL(", ").join(
        sql.SQL("{t}.{c}::text, {v}.{c}::text, {t}.{c} IS DISTINCT FROM {v}.{c}").format(t=t, v=v, c=sql.Identifier(c))
        for c in compared) if compared else sql.SQL("NULL")
    query = sql.SQL("SELECT {v}.{key}::text, {selects} FROM (VALUES %s) AS v({cols}) "
                    "JOIN {schema}.{table} AS t ON {t}.{key} = {v}.{key}").format(
        t=t, v=v, key=sql.Identifier(KEY), selects=selects, cols=ident_list(cols),
        schema=sql.Identifier(plan["schema"]), table=sql.Identifier(plan["table"])).as_string(cur)
    changes = []
    try:
        for part in chunks(rows_of(updates, cols)):
            for row in execute_values(cur, query, part, template=values_template(cols, plan["columns"]),
                                      page_size=len(part), fetch=True):
                for i, col in enumerate(compared):
                    db_value, new_value, changed = row[1 + 3 * i: 4 + 3 * i]
                    if changed:
                        changes.append((row[0], col, db_value, new_value))
    except psycopg2.Error as e:
        cur.connection.rollback()
        plan["ok"] = False
        print(f"!! Update values rejected by the DB: {str(e).strip()}")
        return
    changes = pd.DataFrame(changes, columns=[KEY, "column", "db_value", "new_value"])
    plan["changes"] = changes
    n_changed = changes[KEY].nunique()
    print(f"Update values: all {len(updates):,} rows cast to the DB column types; {n_changed:,} rows have a "
          f"different value, {len(updates) - n_changed:,} are already the same")
    if not changes.empty:
        print("Changed cells per column: " + ", ".join(f"{c}={n:,}" for c, n in
                                                         changes["column"].value_counts().items()))


def report(plan):
    """Samples on screen + the full planned changes in dry_run_output/."""
    PREVIEW_DIR.mkdir(exist_ok=True)
    inserts, changes, table = plan["inserts"], plan["changes"], plan["table"]
    ins_path, chg_path = PREVIEW_DIR / f"{table}_insert.csv", PREVIEW_DIR / f"{table}_update_changes.csv"
    inserts.to_csv(ins_path, index=False)
    changes.to_csv(chg_path, index=False)
    if not inserts.empty:
        print(f"\nFirst {min(SAMPLE_ROWS, len(inserts))} rows to INSERT:\n"
              f"{inserts.head(SAMPLE_ROWS).to_string(index=False)}")
    if not changes.empty:
        print(f"\nFirst {min(SAMPLE_ROWS * 2, len(changes))} changed cells to UPDATE:\n"
              f"{changes.head(SAMPLE_ROWS * 2).to_string(index=False)}")
    print(f"\nFull plan: {ins_path}\n           {chg_path}")


# ---------------------------------------------------------------------------------------------------------------------
# Write (DRY_RUN False)
# ---------------------------------------------------------------------------------------------------------------------
def write_table(cur, plan):
    """UPDATE matching ids, INSERT new ids. Returns (updated, inserted) row counts."""
    schema, table, pushed, update_cols = plan["schema"], plan["table"], plan["pushed"], plan["update_cols"]
    update_values = plan["update_values"]           # id + updated columns (created_at not sent on an UPDATE)
    target = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    updated = inserted = 0

    if not plan["updates"].empty and update_cols:
        query = sql.SQL("UPDATE {target} AS t SET {sets} FROM (VALUES %s) AS v({cols}) "
                        "WHERE t.{key} = v.{key}").format(
            target=target, cols=ident_list(update_values), key=sql.Identifier(KEY),
            sets=sql.SQL(", ").join(sql.SQL("{c} = v.{c}").format(c=sql.Identifier(c)) for c in update_cols)
        ).as_string(cur)
        for part in chunks(rows_of(plan["updates"], update_values)):
            execute_values(cur, query, part, template=values_template(update_values, plan["columns"]),
                           page_size=len(part))
            updated += cur.rowcount

    if not plan["inserts"].empty:
        query = sql.SQL("INSERT INTO {target} ({cols}) VALUES %s").format(target=target, cols=ident_list(pushed)) \
            .as_string(cur)
        for part in chunks(rows_of(plan["inserts"], pushed)):
            execute_values(cur, query, part, template=values_template(pushed, plan["columns"]),
                           page_size=len(part))
            inserted += cur.rowcount
    return updated, inserted


# ---------------------------------------------------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Push ris_view_data / ris_trends_data (S3 Latest) to the DB")
    p.add_argument("--dry-run", choices=["true", "false"], default=None,
                   help=f"true: report only, false: write to the DB (default: DRY_RUN = {DRY_RUN})")
    p.add_argument("--only", choices=list(TABLES), action="append", help="push only this table (repeatable)")
    p.add_argument("--view-csv", help="local ris_view_data.csv instead of S3")
    p.add_argument("--trends-csv", help="local ris_trends_data.csv instead of S3")
    return p.parse_args()


def main():
    args = parse_args()
    dry_run = DRY_RUN if args.dry_run is None else args.dry_run == "true"
    sources = {"ris_view": args.view_csv, "ris_trends": args.trends_csv}
    load_env()
    folder = source_dir()
    settings = db_settings()
    schema = os.environ.get("DB_SCHEMA") or "public"
    print(f"{'DRY RUN - nothing is written to the DB' if dry_run else 'LIVE RUN - the DB will be changed'}\n"
          f"DB: {settings['user']}@{settings['host']}:{settings['port']}/{settings['dbname']} schema {schema}")

    conn = psycopg2.connect(connect_timeout=15, application_name="ris_kpi_db_push", **settings)
    try:
        with conn.cursor() as cur:
            plans = []
            for name, cfg in TABLES.items():
                if args.only and name not in args.only:
                    continue
                plan = plan_table(cur, name, cfg, schema, sources[name] or f"{folder}/{cfg['file']}")
                if plan is not None:
                    report(plan)
                    plans.append(plan)
            conn.rollback()                       # the plan only read; end that transaction

            bad = [p["name"] for p in plans if not p["ok"]]
            print(f"\n{'=' * 100}")
            if dry_run:
                for p in plans:
                    print(f"{p['table']}: would INSERT {len(p['inserts']):,}, UPDATE {len(p['updates']):,} rows "
                          f"({p['changes'][KEY].nunique() if 'changes' in p else 0:,} with changed values)")
                print("DRY RUN done - nothing written." + (f" Fix first: {bad}" if bad else
                                                             " Run with --dry-run false to write."))
                return 1 if bad else 0
            if bad:
                print(f"Not written: values rejected for {bad} (see !! above). The DB is unchanged.")
                return 1

            for p in plans:                       # one transaction for all tables
                updated, inserted = write_table(cur, p)
                print(f"{p['table']}: UPDATED {updated:,}, INSERTED {inserted:,} rows")
            conn.commit()
            print("Committed.")
            return 0
    except Exception:
        conn.rollback()
        print("!! Error - rolled back, the DB is unchanged.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
