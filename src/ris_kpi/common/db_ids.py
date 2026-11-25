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


# survey_id for one upc + phase + cycle with several survey_schedule_ids (SURVEY_ID_FALLBACK=True): the
# wfm_survey_schedule row (id = survey_schedule_id, same upc) with the first current_status of this list
SURVEY_STATUS_PRIORITY = ["Survey Final Approved", "Validation in Progress"]
SURVEY_ID_KEYS = ["_upc", "_phase", "_cycle"]


def survey_id_lookup(survey_data_map, wfm_survey_schedule=None, fallback=False):
    """_upc / _phase / _cycle -> survey_id (survey_data_map.survey_schedule_id); survey_date is not used.

    One survey_schedule_id for the upc + phase + cycle (repeated rows count once) -> that id.
    Several different ids:
      fallback=False -> no survey_id (logged)
      fallback=True  -> the ids are looked up in wfm_survey_schedule (id = survey_schedule_id) and kept only when
                        wfm_survey_schedule.upc is the same UPC; of those, the one whose current_status is
                        'Survey Final Approved', else the one with 'Validation in Progress'. No such id, or several
                        ids with the same chosen status -> no survey_id (logged).
    """
    keys = SURVEY_ID_KEYS
    lk = pd.DataFrame({
        "_upc": upc_slash(survey_data_map["upc"]),
        "_phase": key_text(survey_data_map["phase"]),
        "_cycle": key_text(survey_data_map["cycle"]),
        "survey_id": survey_data_map["survey_schedule_id"].astype("string").str.strip().replace("", pd.NA),
    }).dropna(subset=keys + ["survey_id"]).drop_duplicates()
    n_ids = lk.groupby(keys)["survey_id"].transform("nunique")
    single, multi = lk[n_ids == 1], lk[n_ids > 1]
    if multi.empty:
        return single.reset_index(drop=True)

    n_surveys = len(multi[keys].drop_duplicates())
    if not fallback:
        log.warning("survey_data_map: %s upc + phase + cycle with more than one survey_schedule_id -> survey_id left "
                    "empty (SURVEY_ID_FALLBACK=False):\n%s", n_surveys,
                    multi.rename(columns=_SURVEY_LABELS).to_string(index=False))
        return single.reset_index(drop=True)

    picked = _pick_by_schedule_status(multi, wfm_survey_schedule)
    log.info("survey_data_map: %s upc + phase + cycle with more than one survey_schedule_id -> %s resolved from "
             "wfm_survey_schedule (SURVEY_ID_FALLBACK=True)", n_surveys, len(picked))
    return pd.concat([single, picked], ignore_index=True)


_SURVEY_LABELS = {"_upc": "upc", "_phase": "phase", "_cycle": "cycle", "survey_id": "survey_schedule_id"}


def _pick_by_schedule_status(multi, wfm_survey_schedule):
    """One survey_id per upc + phase + cycle of `multi` (several candidate ids each), by wfm_survey_schedule."""
    keys = SURVEY_ID_KEYS
    if wfm_survey_schedule is None:
        wfm_survey_schedule = pd.DataFrame(columns=["id", "upc", "current_status"])
    wfm = pd.DataFrame({
        "survey_id": wfm_survey_schedule["id"].astype("string").str.strip(),
        "wfm_upc": upc_slash(wfm_survey_schedule["upc"].astype("string").str.strip()),
        "current_status": wfm_survey_schedule["current_status"].astype("string").str.strip(),
    }).dropna(subset=["survey_id"])
    wfm = dedup(wfm, ["survey_id"], "wfm_survey_schedule")

    cand = multi.merge(wfm, on="survey_id", how="left", validate="many_to_one")
    same_upc = (cand["wfm_upc"] == cand["_upc"]).fillna(False).astype(bool)
    rank = {s.lower(): i for i, s in enumerate(SURVEY_STATUS_PRIORITY)}
    cand["_rank"] = cand["current_status"].str.lower().map(rank).where(same_upc)
    best = cand.groupby(keys)["_rank"].transform("min")
    top = cand[cand["_rank"].notna() & (cand["_rank"] == best)]
    n_top = top.groupby(keys)["survey_id"].transform("nunique")
    picked = top.loc[n_top == 1, keys + ["survey_id"]].drop_duplicates()

    report = cand.merge(picked.assign(chosen="yes"), on=keys + ["survey_id"], how="left")
    report["chosen"] = report["chosen"].fillna("")
    report = report.rename(columns=_SURVEY_LABELS)[["upc", "phase", "cycle", "survey_schedule_id", "wfm_upc",
                                                    "current_status", "chosen"]]
    log.info("survey_id from wfm_survey_schedule (status order: %s):\n%s", " > ".join(SURVEY_STATUS_PRIORITY),
             report.to_string(index=False))
    unresolved = multi[keys].drop_duplicates().merge(picked[keys], on=keys, how="left", indicator=True)
    unresolved = unresolved[unresolved["_merge"] == "left_only"]
    if len(unresolved):
        log.warning("survey_id left empty for %s upc + phase + cycle: no id with the same UPC and status %s in "
                    "wfm_survey_schedule, or several ids with the same status:\n%s", len(unresolved),
                    " / ".join(SURVEY_STATUS_PRIORITY),
                    unresolved[keys].rename(columns=_SURVEY_LABELS).to_string(index=False))
    return picked.reset_index(drop=True)


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
