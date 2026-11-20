"""Silver tables of the new layout: one file per survey folder, read for the run scope and stacked.

    <SILVER_INPUT_DIR>/Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/dlm_kpi_new/master_cracking_10m.parquet

Run scope = a list of surveys {"Zone", "Phase", "Cycle", "UPC"} (an empty value = any folder of that level):
  config.CONSIDER_UPC_LIST True   the rows of config.UPC_LIST_FILE (upc_to_run.csv: upc, phase, cycle, zone)
  config.CONSIDER_UPC_LIST False  [ALL_SCOPE]: every Zone / Phase / Cycle / UPC folder
resolved once (run_scopes) into config.SILVER_SCOPE; the command line can set it instead (cli.apply_scope).

Every file named <table>.csv / .parquet / .xlsx under the root (any depth) whose path has one scope's
Zone= / Phase= / Cycle= / UPC= folders belongs to <table>. For each scope only the folder fixed by its leading values
is listed (e.g. Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/), so a listed run does not walk the whole silver folder;
on S3 that needs s3:ListBucket on the silver prefix. Every column is read as text; the key columns are normalised
(normalise): upc '/' form, phase / cycle '2' (not '2.0'), detected_on yyyy-mm-dd, text keys trimmed, pavement type
spellings unified.
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
ALL_SCOPE = {key: "" for key in PARTITIONS}
# upc_to_run.csv column -> scope key
LIST_COLUMNS = {"zone": "Zone", "phase": "Phase", "cycle": "Cycle", "upc": "UPC"}


# --- Run scope ---

def _clean(key, value):
    """One scope value: trimmed, phase / cycle '2' (not '2.0'), UPC in '/' form, a 'Zone=B' style prefix dropped."""
    value = "" if value is None or pd.isna(value) else str(value).strip()
    if value.lower().startswith(f"{key.lower()}="):
        value = value[len(key) + 1:].strip()
    if key in ("Phase", "Cycle") and value.endswith(".0"):
        value = value[:-2]
    if key == "UPC":
        value = value.replace("_", "/")
    return value


def make_scope(values):
    """{"Zone": "B", "UPC": "N_1"} -> {"Zone": "B", "Phase": "", "Cycle": "", "UPC": "N/1"} (missing = any)."""
    return {key: _clean(key, (values or {}).get(key)) for key in PARTITIONS}


def as_scopes(scope):
    """A scope dict or a list of them -> list of normalised scopes."""
    return [make_scope(one) for one in ([scope] if isinstance(scope, dict) else scope)]


def describe(scope):
    """{"Zone": "B", "Phase": "", ...} -> 'Zone=B/Phase=*/Cycle=1/UPC=N/1'."""
    return "/".join(f"{key}={scope.get(key) or '*'}" for key in PARTITIONS)


def read_upc_list(path):
    """upc_to_run.csv -> list of scopes, one per row (columns upc, phase, cycle, zone in any order / case; an empty
    cell = any folder of that level; empty rows and duplicates dropped)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"UPC list {path} not found (CONSIDER_UPC_LIST is on). Create it with the columns "
                                "upc, phase, cycle, zone, or turn CONSIDER_UPC_LIST off to run every folder")
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False, skipinitialspace=True)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in LIST_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing column(s) {missing} (needed: {', '.join(LIST_COLUMNS)})")
    scopes = []
    for row in df[list(LIST_COLUMNS)].itertuples(index=False):
        scope = make_scope({LIST_COLUMNS[col]: value for col, value in zip(LIST_COLUMNS, row)})
        if any(scope.values()) and scope not in scopes:
            scopes.append(scope)
    if not scopes:
        raise ValueError(f"{path} lists no survey (turn CONSIDER_UPC_LIST off to run every folder)")
    for scope in scopes:
        if not scope["UPC"]:
            log.warning("%s: a row without upc reads every UPC folder of %s", path.name, describe(scope))
    return scopes


def run_scopes():
    """The run scope: config.SILVER_SCOPE when already set (command line / earlier call), else from
    config.CONSIDER_UPC_LIST / config.UPC_LIST_FILE (then stored in config.SILVER_SCOPE)."""
    if config.SILVER_SCOPE is None:
        config.SILVER_SCOPE = read_upc_list(config.UPC_LIST_FILE) if config.CONSIDER_UPC_LIST else [dict(ALL_SCOPE)]
    return as_scopes(config.SILVER_SCOPE)


# --- Finding / reading the silver files ---

def scope_folders(scope):
    """{"Zone": "B", "UPC": "N/01002/01001/GJ", "Cycle": ""} -> ["Zone=B", "UPC=N_01002_01001_GJ"] (empty = any)."""
    folders = []
    for key in PARTITIONS:
        value = _clean(key, (scope or {}).get(key))
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


def _list(root, prefix, name, listed):
    """Files under <root>/<prefix> (S3 listings cached per prefix in `listed`; local: files named <name>.*)."""
    if s3.is_s3_uri(root):
        base = f"{str(root).rstrip('/')}/{prefix}" if prefix else str(root)
        if base not in listed:
            listed[base] = s3.list_uris(base)
        return listed[base]
    base = Path(root) / prefix if prefix else Path(root)
    return [p for p in base.rglob(f"{name}.*") if p.is_file()] if base.exists() else []


def find_silver_files(root, name, scope=None, missing=None):
    """Every <root>/**/<name>.<csv|parquet|xlsx> (exact file name) inside the folders of any scope, sorted.
    scope: a dict, a list of dicts, or None = the run scope (run_scopes). missing: a list that gets the scopes
    without any file."""
    wanted = {f"{name}{suffix}" for suffix in SUFFIXES}
    found, listed = set(), {}
    for one in (run_scopes() if scope is None else as_scopes(scope)):
        folders = scope_folders(one)
        prefix = "/".join(_scope_prefix(folders))
        hits = [loc for loc in _list(root, prefix, name, listed)
                if _parts(loc)[-1] in wanted and set(folders) <= set(_parts(loc))]
        if not hits and missing is not None:
            missing.append(one)
        found.update(hits)
    return sorted(found, key=str)


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
    """<name> from every survey folder of the scope (default: the run scope) under root, stacked and normalised.
    A survey of the scope without the file is logged (warning); no file at all is an error. Columns missing in some
    files stay empty (logged). columns: read only these (every file must have them)."""
    missing_scopes = []
    files = find_silver_files(root, name, scope, missing=missing_scopes)
    if not files:
        raise FileNotFoundError(f"No {name}.csv / .parquet / .xlsx under {root} in "
                                + ", ".join(describe(one) for one in missing_scopes))
    for one in missing_scopes:
        log.warning("No %s under %s for %s (that survey has no rows of this table)", name, root, describe(one))
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
    """Rows of a saved (gold) table that belong to any scope (default: the run scope), compared on the normalised
    zone / phase / cycle / upc columns; empty scope values match everything."""
    keep = pd.Series(False, index=df.index)
    for one in (run_scopes() if scope is None else as_scopes(scope)):
        match = pd.Series(True, index=df.index)
        for key, col in SCOPE_COLUMNS.items():
            value = one[key]
            if not value or col not in df.columns:
                continue
            if key == "UPC":
                match &= upc_slash(df[col].astype("string").str.strip()) == value
            elif key in ("Phase", "Cycle"):
                match &= to_str_id(df[col].astype("string").str.strip()) == value
            else:
                match &= df[col].astype("string").str.strip() == value
        keep |= match.fillna(False).astype(bool)
    return df[keep.to_numpy()].reset_index(drop=True)
