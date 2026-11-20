"""Silver tables of the new layout: one file per survey folder, read for config.SILVER_SCOPE and stacked.

    <SILVER_INPUT_DIR>/Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/dlm_kpi_new/master_cracking_10m.parquet

Every file named <table>.csv / .parquet / .xlsx under the root (any depth) whose path has the scope's
Zone= / Phase= / Cycle= / UPC= folders belongs to <table> (empty scope value = any folder of that level). Only the
folder fixed by the leading scope values is listed (e.g. Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/), so a one-UPC
run does not walk the whole silver folder; on S3 that needs s3:ListBucket on the silver prefix. Every column is read
as text; the key columns are normalised (normalise): upc '/' form, phase / cycle '2' (not '2.0'), detected_on
yyyy-mm-dd, text keys trimmed, pavement type spellings unified.
"""
import logging
from pathlib import Path, PurePosixPath

import pandas as pd

from ris_kpi import config
from ris_kpi.common import s3
from ris_kpi.common.io import local_file
from ris_kpi.common.keys import filter_upcs, to_str_id, upc_slash

log = logging.getLogger(__name__)

SUFFIXES = (".csv", ".parquet", ".xlsx")
TEXT_KEYS = ("zone", "source_system", "lane", "threshold_name", "pavement_type", "defect_type")


PARTITIONS = ["Zone", "Phase", "Cycle", "UPC"]


def scope_folders(scope):
    """{"Zone": "B", "UPC": "N/01002/01001/GJ", "Cycle": ""} -> ["Zone=B", "UPC=N_01002_01001_GJ"] (empty = any)."""
    folders = []
    for key in PARTITIONS:
        value = str((scope or {}).get(key) or "").strip()
        if value:
            folders.append(f"{key}={value.replace('/', '_') if key == 'UPC' else value}")
    return folders


def _scope_prefix(folders):
    """Leading folders that fix the path (Zone=B/Phase=1/... until the first level left open)."""
    prefix = []
    for key, folder in zip(PARTITIONS, folders):
        if not folder.startswith(f"{key}="):
            break
        prefix.append(folder)
    return prefix


def _parts(location):
    return PurePosixPath(str(location).replace("\\", "/")).parts


def find_silver_files(root, name, scope=None):
    """Every <root>/**/<name>.<csv|parquet|xlsx> (exact file name) inside the scope folders, sorted."""
    wanted = {f"{name}{suffix}" for suffix in SUFFIXES}
    folders = scope_folders(config.SILVER_SCOPE if scope is None else scope)
    prefix = "/".join(_scope_prefix(folders))
    if s3.is_s3_uri(root):
        base = f"{str(root).rstrip('/')}/{prefix}" if prefix else str(root)
        found = [uri for uri in s3.list_uris(base) if PurePosixPath(uri).name in wanted]
    else:
        base = Path(root) / prefix if prefix else Path(root)
        found = [p for p in base.rglob(f"{name}.*") if p.is_file() and p.name in wanted] if base.exists() else []
    return sorted(loc for loc in found if set(folders) <= set(_parts(loc)))


def _read(location, columns=None):
    """One silver file, every column as text (columns: only these, when given)."""
    with local_file(location) as path:
        suffix = Path(path).suffix.lower()
        if suffix == ".parquet":
            df = pd.read_parquet(path, columns=columns)
            return df.astype("string")
        if suffix == ".xlsx":
            return pd.read_excel(path, dtype=str, usecols=columns)
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig", low_memory=False, usecols=columns)


def normalise(df):
    """Key columns in one form across CSV / parquet sources (the other columns are left as read)."""
    df = df.copy()
    if "upc" in df.columns:
        df["upc"] = upc_slash(df["upc"].astype("string").str.strip())
    for col in ("phase", "cycle"):
        if col in df.columns:
            df[col] = to_str_id(df[col].astype("string").str.strip())
    for col in TEXT_KEYS:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
    if "pavement_type" in df.columns:
        df["pavement_type"] = df["pavement_type"].replace(config.PAVEMENT_TYPE_ALIASES)
    if "detected_on" in df.columns:
        df["detected_on"] = df["detected_on"].astype("string").str.strip().str[:10]
    return df


def load_silver(root, name, columns=None, keep_upcs=(), scope=None):
    """<name> from every survey folder of the scope (default config.SILVER_SCOPE) under root, stacked and normalised.
    Columns missing in some files stay empty (logged). columns: read only these (every file must have them)."""
    files = find_silver_files(root, name, scope)
    if not files:
        folders = scope_folders(config.SILVER_SCOPE if scope is None else scope)
        raise FileNotFoundError(f"No {name}.csv / .parquet / .xlsx under {root}"
                                + (f" in {'/'.join(folders)}" if folders else ""))
    frames = []
    for location in files:
        df = _read(location, columns)
        log.info("Loaded %-30s rows: %9s  (%s)", name, f"{len(df):,}", str(location)[len(str(root)):].lstrip("/\\"))
        frames.append((location, df))
    all_cols = set().union(*(df.columns for _, df in frames))
    for location, df in frames:
        missing = sorted(all_cols - set(df.columns))
        if missing:
            log.warning("%s: %s has no %s (left empty)", name, location, missing)
    out = normalise(pd.concat([df for _, df in frames], ignore_index=True))
    return filter_upcs(out, keep_upcs)


# scope key -> column of the silver / gold tables
SCOPE_COLUMNS = {"Zone": "zone", "Phase": "phase", "Cycle": "cycle", "UPC": "upc"}


def keep_scope(df, scope=None):
    """Rows of a saved (gold) table that belong to the scope (default config.SILVER_SCOPE), compared on the
    normalised zone / phase / cycle / upc columns; empty scope values keep everything."""
    scope = config.SILVER_SCOPE if scope is None else scope
    keep = pd.Series(True, index=df.index)
    for key, col in SCOPE_COLUMNS.items():
        value = str((scope or {}).get(key) or "").strip()
        if not value or col not in df.columns:
            continue
        if key == "UPC":
            keep &= upc_slash(df[col].astype("string").str.strip()) == value.replace("_", "/")
        elif key in ("Phase", "Cycle"):
            keep &= to_str_id(df[col].astype("string").str.strip()) == value
        else:
            keep &= df[col].astype("string").str.strip() == value
    return df[keep.fillna(False).astype(bool).to_numpy()].reset_index(drop=True)
