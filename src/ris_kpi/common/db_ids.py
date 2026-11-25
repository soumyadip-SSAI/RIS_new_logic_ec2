"""Shared by the DB id columns of both pipelines (ris_view/db_ids.py, ris_trends/db_ids.py) and by the new / existing
survey logic (ris_view/incremental.py, ris_trends/append.py): key normalisation, DB lookups, id-preserving left joins
and the survey key.

Every lookup is de-duplicated on its key (keep first), so the joins never add rows; a row whose id is not found is
kept with an empty id.
"""
import logging

import pandas as pd

from ris_kpi.common.keys import upc_slash

log = logging.getLogger(__name__)

SURVEY_KEYS = ["upc", "phase", "cycle"]


# --- key normalisation -----------------------------------------------------------------------------------------

def key_text(series):
    """Phase / cycle keys as text, '2.0' -> '2'."""
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def date_text(series):
    """Date keys as 'YYYY-MM-DD'."""
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")


def survey_key(df, columns):
    """'upc|phase|cycle|YYYY-MM-DD' per row; columns = [upc, phase, cycle, date] column names of df.
    upc in '/' form, phase / cycle as '2' (not '2.0'), date as its first 10 characters (no datetime parse:
    silver dates can be out of the pandas range)."""
    upc, phase, cycle, day = (df[c].astype("string").str.strip() for c in columns)
    parts = [upc_slash(upc), phase.str.replace(r"\.0$", "", regex=True),
             cycle.str.replace(r"\.0$", "", regex=True), day.str[:10]]
    return parts[0].str.cat(parts[1:], sep="|", na_rep="")


# --- lookups ---------------------------------------------------------------------------------------------------

def dedup(lookup, keys, name):
    dups = lookup.duplicated(keys, keep="first")
    if dups.any():
        log.warning("%s: %s duplicate key row(s) on %s — keeping the first", name, int(dups.sum()), keys)
    return lookup[~dups]


def upc_lookup(master_upc_project, id_col):
    """upc -> master_upc_project.id (as id_col)."""
    lk = master_upc_project[["upc", "id"]].rename(columns={"id": id_col})
    return dedup(lk.dropna(subset=["upc"]), ["upc"], "master_upc_project")


def survey_source_lookup(master_survey_system, id_col):
    """system_code -> master_survey_system.id (as id_col)."""
    lk = master_survey_system[["system_code", "id"]].rename(columns={"id": id_col})
    return dedup(lk.dropna(subset=["system_code"]), ["system_code"], "master_survey_system")


def survey_lookup(survey_data_map, id_col):
    """upc + phase + cycle -> survey_data_map.survey_schedule_id (as id_col); survey_date not used."""
    lk = pd.DataFrame({
        "upc": survey_data_map["upc"].astype("string"),
        "phase": key_text(survey_data_map["phase"]),
        "cycle": key_text(survey_data_map["cycle"]),
        id_col: survey_data_map["survey_schedule_id"],
    })
    return dedup(lk.dropna(subset=SURVEY_KEYS + [id_col]).drop_duplicates(), SURVEY_KEYS, "survey_data_map")


def left_join_ids(df, lookup, left_on, right_on, id_col):
    """Left join that keeps every row of df exactly once (many_to_one), adding only id_col."""
    keys = pd.DataFrame({r: df[l] for l, r in zip(left_on, right_on)}, index=df.index)
    merged = keys.merge(lookup, on=right_on, how="left", validate="many_to_one")
    if len(merged) != len(df):
        raise AssertionError(f"{id_col} join changed the row count")
    out = df.copy()
    out[id_col] = merged[id_col].to_numpy()
    return out


def log_match(df, id_col, key_cols):
    matched = int(df[id_col].notna().sum())
    log.info("%-17s matched: %s / %s rows", id_col, f"{matched:,}", f"{len(df):,}")
    missing = df.loc[df[id_col].isna(), key_cols].drop_duplicates()
    if len(missing):
        log.warning("%s not found for %s key(s) (rows kept with empty %s):\n%s",
                    id_col, len(missing), id_col, missing.to_string(index=False))
