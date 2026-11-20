"""Copy `id` / `created_at` from the DB KPI tables into the saved outputs (run manually after `ris-kpi run`):

    run_data  = KPI_OUTPUT_ROOT/Latest/ris_view_data.csv, ris_trends_data.csv and the newest dated copy
                year=/month=/day=/<same name>.csv (each overwritten in place, same name)
    kpi_data  = DB export ris_view_kpi / ris_trends_kpi in DB_TABLES_DIR (DHMS/DIC_Staging/Latest)

A run_data row whose KEYS match a kpi_data row takes that row's id and created_at, so the DB push later UPDATEs the
existing DB row instead of inserting a second one. Rows without a match keep their own id / created_at (new to the
DB). Keys (compared as text: trimmed, case-insensitive, dates as yyyy-mm-dd, empty = empty):

    ris_view   survey_source_id, upc_id, defect_detected_on, survey_id, defect_type_id + status
    ris_trends upc_id, survey_id, survey_source_id, defect_detected_on, pavement_type, roughness_ca_category,
               roughness_irc_category, rutting_ca_category, rutting_irc_category

status is needed to make a ris_view row unique (an open and a close row per defect type); the category columns
separate the Good / Fair / Poor rows of ris_trends (an empty category matches an empty category). A key that is still shared by several rows (in run_data or in
kpi_data) is ambiguous and is left unchanged (logged).

Both the Latest/ file and the newest dated copy (the one the next `ris-kpi run` reads) are synced, each on its own
rows, so the DB ids are carried into the next run. Older dated copies are history and are not touched.

    ris-kpi sync-db-ids --dry-run      # report only, nothing saved
    ris-kpi sync-db-ids                # copy and save
    ris-kpi sync-db-ids --only ris_view
"""
import logging
import re
import tempfile
from pathlib import Path

import pandas as pd

from ris_kpi import config
from ris_kpi.common import s3, storage
from ris_kpi.common.io import exists
from ris_kpi.common.db_tables import find_db_table, read_db_table
from ris_kpi.config import RisTrends, RisView

log = logging.getLogger(__name__)

COPIED = ["id", "created_at"]
DATE_KEYS = {"defect_detected_on"}
TABLES = {
    "ris_view": {
        "run_root": RisView.KPI_OUTPUT_ROOT, "run_name": RisView.RIS_VIEW_DATA_NAME, "db_table": "ris_view_kpi",
        "key": ["survey_source_id", "upc_id", "defect_detected_on", "survey_id", "defect_type_id", "status"],
    },
    "ris_trends": {
        "run_root": RisTrends.OUTPUT_ROOT, "run_name": RisTrends.RIS_TRENDS_OUTPUT_NAME, "db_table": "ris_trends_kpi",
        "key": ["upc_id", "survey_id", "survey_source_id", "defect_detected_on", "pavement_type",
                "roughness_ca_category", "roughness_irc_category", "rutting_ca_category", "rutting_irc_category"],
    },
}
SAMPLE_ROWS = 10
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _date_text(value):
    """yyyy-mm-dd of a date / timestamp text ('2025-01-25 00:00:00+00' -> '2025-01-25'; '25-01-2025' day first)."""
    if not value:
        return ""
    if _ISO_DATE.match(value):
        return value[:10]
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def key_text(df, key):
    """One text key per row: columns trimmed + lower case, dates as yyyy-mm-dd, joined with '|'."""
    parts = []
    for col in key:
        values = df[col].astype("string").fillna("").str.strip()
        parts.append(values.map(_date_text) if col in DATE_KEYS else values.str.lower())
    return pd.concat(parts, axis=1).agg("|".join, axis=1)


def copy_db_ids(run_data, kpi_data, key, label):
    """run_data with id / created_at of the kpi_data row with the same key. Returns (table, report dict)."""
    for name, df, needed in (("run_data", run_data, key + COPIED), ("kpi_data", kpi_data, key + COPIED)):
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise KeyError(f"{label}: {name} has no column(s) {missing}")

    run_key, kpi_key = key_text(run_data, key), key_text(kpi_data, key)
    run_dup = set(run_key[run_key.duplicated(keep=False)])
    kpi_dup = set(kpi_key[kpi_key.duplicated(keep=False)])
    for name, dup, df, k in (("run_data", run_dup, run_data, run_key), ("kpi_data", kpi_dup, kpi_data, kpi_key)):
        if dup:
            log.warning("%s: %s key(s) shared by several %s rows — those rows are left unchanged:\n%s", label,
                        len(dup), name, df.loc[k.isin(dup), key].head(SAMPLE_ROWS).to_string(index=False))

    unique_db = ~kpi_key.isin(kpi_dup)
    lookup = kpi_data.loc[unique_db, COPIED].set_axis(kpi_key[unique_db])        # key -> DB id, created_at
    usable = run_key.isin(lookup.index) & ~run_key.isin(run_dup)
    out = run_data.copy()
    changed = pd.Series(False, index=run_data.index)
    for col in COPIED:
        new = run_key.map(lookup[col]).astype("string")
        old = run_data[col].astype("string")
        changed |= usable & (old.fillna("") != new.fillna(""))
        out.loc[usable, col] = new[usable]

    ids = out["id"].astype("string")
    if ids.duplicated().any():
        raise AssertionError(f"{label}: ids are not unique after the copy:\n"
                             f"{out.loc[ids.duplicated(keep=False), ['id'] + key].to_string(index=False)}")

    report = {
        "run_rows": len(run_data), "kpi_rows": len(kpi_data), "matched": int(usable.sum()),
        "changed": int(changed.sum()), "not_in_db": int((~run_key.isin(lookup.index) & ~run_key.isin(run_dup)).sum()),
        "only_in_db": int((~kpi_key.isin(run_key)).sum()),
        "ambiguous_run": int(run_key.isin(run_dup).sum()), "ambiguous_db": int(kpi_key.isin(kpi_dup).sum()),
        "changes": pd.DataFrame({"run_id": run_data.loc[changed, "id"], "db_id": out.loc[changed, "id"],
                                 "db_created_at": out.loc[changed, "created_at"]}).join(run_data.loc[changed, key]),
        "not_in_db_rows": run_data.loc[~run_key.isin(lookup.index) & ~run_key.isin(run_dup), key],
    }
    return out, report


def log_report(label, r, survey_cols):
    log.info("%s: %s run_data rows, %s kpi_data rows -> %s matched (%s get a different id / created_at), "
             "%s not in the DB (keep their own id), %s DB rows not in run_data, ambiguous: %s run / %s DB",
             label, f"{r['run_rows']:,}", f"{r['kpi_rows']:,}", f"{r['matched']:,}", f"{r['changed']:,}",
             f"{r['not_in_db']:,}", f"{r['only_in_db']:,}", f"{r['ambiguous_run']:,}", f"{r['ambiguous_db']:,}")
    if not r["changes"].empty:
        log.info("%s: first changes (run id -> DB id):\n%s", label,
                 r["changes"].head(SAMPLE_ROWS).to_string(index=False))
    if not r["not_in_db_rows"].empty:
        cols = [c for c in survey_cols if c in r["not_in_db_rows"].columns]
        log.info("%s: rows not in the DB, per survey (new rows, keep their own id):\n%s", label,
                 r["not_in_db_rows"].groupby(cols, dropna=False).size().rename("rows").reset_index()
                 .to_string(index=False))


def run_data_locations(root, name):
    """<root>/Latest/<name>.csv and the newest dated <root>/year=/month=/day=/<name>.csv (those that exist)."""
    locations = [storage.latest_location(root, name)]
    newest = storage.find_latest(root, name)                                       # newest dated copy (or Latest)
    if newest is not None and str(newest) != str(locations[0]):
        locations.append(newest)
    return [loc for loc in locations if exists(loc)]


def sync_location(name, location, kpi_data, key, dry_run):
    """Copy the DB ids into one saved file (overwritten in place when something changed); its report or None."""
    run_data = storage.read_text(location, empty_as_null=False)
    if run_data.empty:
        log.warning("%s: no rows in %s — nothing to do", name, location)
        return None
    log.info("%s: run_data %s (%s rows)", name, location, f"{len(run_data):,}")
    out, report = copy_db_ids(run_data, kpi_data, key, name)
    log_report(name, report, ["upc_id", "survey_id", "defect_detected_on"])
    if dry_run:
        log.info("%s: DRY RUN — %s not saved", name, location)
    elif report["changed"] == 0:
        log.info("%s: ids already match the DB — %s not saved", name, location)
    else:
        write_text(out, location)
        log.info("%s: saved %s — %s rows", name, location, f"{len(out):,}")
    return report


def sync_table(name, dry_run=False, db_dir=None):
    """Copy the DB ids into the Latest/ copy and the newest dated copy of one table; returns the report of the first
    synced copy (Latest/ when it exists), None when there is nothing to do."""
    cfg = TABLES[name]
    db_dir = db_dir or config.DB_TABLES_DIR
    locations = run_data_locations(cfg["run_root"], cfg["run_name"])
    if not locations:
        log.warning("%s: no saved %s.csv in %s — nothing to do", name, cfg["run_name"], cfg["run_root"])
        return None
    db_location = find_db_table(db_dir, cfg["db_table"])
    kpi_data = read_db_table(db_location)
    log.info("%s: kpi_data %s (%s rows); syncing %s", name, db_location, f"{len(kpi_data):,}",
             " and ".join(str(loc) for loc in locations))
    reports = [sync_location(name, loc, kpi_data, cfg["key"], dry_run) for loc in locations]
    return next((r for r in reports if r is not None), None)


def write_text(df, location):
    """df as CSV at location (S3 URI or local path), replacing the file."""
    if s3.is_s3_uri(location):
        with tempfile.TemporaryDirectory(prefix="ris_kpi_") as tmp:
            path = Path(tmp) / Path(str(location)).name
            df.to_csv(path, index=False)
            s3.upload(path, location)
    else:
        df.to_csv(location, index=False)


def run_sync(only=None, dry_run=False):
    """Every table in TABLES (or `only`); True when all succeeded."""
    ok = True
    for name in TABLES:
        if only and name not in only:
            continue
        try:
            sync_table(name, dry_run=dry_run)
        except Exception:
            log.exception("%s: sync-db-ids FAILED", name)
            ok = False
    return ok
