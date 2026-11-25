"""DB tables exported from the DHMS database by a separate job, read on every run from DB_TABLES_DIR (config.py).
Nothing is kept locally.

    S3     <DB_TABLES_DIR>/<table>.csv|.parquet  (DHMS/DIC_Staging/Latest: always the latest export)
    local  <DB_TABLES_DIR>/<table>.csv|.parquet, or else the newest dated snapshot
           <DB_TABLES_DIR>/<table>/<YYYY>/<MM>/<DD>/<table>.csv|.parquet (raw_db_tables layout of the DB pull)

Used tables: dlm_defect_case / udm_defect_reference (defect statuses, RIS view step 1), master_upc_project (UPC
filter, UPC lengths, upc_id), master_survey_system / survey_data_map / master_defect_type (DB id columns).
"""
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ris_kpi.common import s3
from ris_kpi.common.io import find_table_file, local_file, read_text_csv, suffix_of

log = logging.getLogger(__name__)

# Columns the pipelines read from each table; a source without them fails before any work is done
REQUIRED_COLUMNS = {
    "dlm_defect_case": ["defect_reference_id", "current_status", "defect_type", "record_level"],
    "udm_defect_reference": ["id", "defect_collection", "defect_id"],
    "master_upc_project": ["id", "upc", "length_km", "lane_length_km"],
    "master_survey_system": ["id", "system_code"],
    "survey_data_map": ["survey_schedule_id", "upc", "phase", "cycle"],
    "master_defect_type": ["id", "survey_system", "asset_type", "defect_name"],
}
SNAPSHOT_SUFFIXES = (".csv", ".parquet")


def latest_snapshot(db_dir, table):
    """Newest local <db_dir>/<table>/<YYYY>/<MM>/<DD>/<table>.csv|.parquet, or None."""
    snapshots = []
    for path in (Path(db_dir) / table).glob(f"*/*/*/{table}.*"):
        if path.suffix not in SNAPSHOT_SUFFIXES:
            continue
        try:
            snapshots.append((date(*map(int, path.parent.parts[-3:])), path))
        except ValueError:
            continue  # not a date folder
    return max(snapshots)[1] if snapshots else None


def find_db_table(db_dir, table):
    """<db_dir>/<table>.* ; locally also the newest dated snapshot when there is no such file."""
    try:
        return find_table_file(db_dir, table)
    except FileNotFoundError:
        snapshot = None if s3.is_s3_uri(db_dir) else latest_snapshot(db_dir, table)
        if snapshot is None:
            raise FileNotFoundError(f"No {table}.(csv|parquet) in {db_dir}"
                                    + ("" if s3.is_s3_uri(db_dir) else f" (nor a {table}/YYYY/MM/DD/ snapshot)")) from None
        return snapshot


def read_db_table(location):
    """All columns as text, empty cells as null (as the original DB fetch returned them)."""
    if suffix_of(location) == ".parquet":
        with local_file(location) as path:
            df = pd.read_parquet(path)
        return df.astype("string").astype(object).where(df.notna(), None)
    return read_text_csv(location, empty_as_null=True)


def load_db_tables(db_dir, tables):
    """{table: DataFrame} for each table in `tables`, read from db_dir."""
    out = {}
    for table in tables:
        location = find_db_table(db_dir, table)
        df = read_db_table(location)
        missing = [c for c in REQUIRED_COLUMNS.get(table, []) if c not in df.columns]
        if missing:
            raise ValueError(f"{table} ({location}) has no column(s) {', '.join(missing)}")
        meta = s3.head(location) if s3.is_s3_uri(location) else None
        written = f" (last written {meta['LastModified']:%Y-%m-%d %H:%M %Z})" if meta else ""
        log.info("Loaded %-22s %8s rows from %s%s", table, f"{len(df):,}", location, written)
        out[table] = df
    return out
