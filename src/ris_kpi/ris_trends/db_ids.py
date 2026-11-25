"""DB id columns and timestamps (same lookups as the main pipeline, ris_kpi/ris_trends/db_ids.py):

    upc_id            master_upc_project    upc = upc                                                 -> id
    survey_source_id  master_survey_system  source_system ('NSV') = system_code                       -> id
    survey_id         survey_data_map       upc + phase + cycle = upc + phase + cycle                 -> survey_schedule_id
                                            (survey_date / defect_detected_on not used). Several ids for one
                                            upc + phase + cycle: SURVEY_ID_FALLBACK (.env) -> wfm_survey_schedule
                                            (common/db_ids.survey_id_lookup), else no survey_id
    id                random uuid4 per row (set when the survey's rows are appended)
created_at / updated_at are set when the rows are appended (ris_trends/append.py).

Every lookup is de-duplicated on its key (first row kept, logged), so the joins never add rows. Rows whose id is not
found are logged and, with DROP_ROWS_WITHOUT_IDS (.env), dropped.
"""
import logging
import uuid

import pandas as pd

from ris_kpi.common.db_ids import SURVEY_ID_KEYS, survey_id_lookup
from ris_kpi.common.keys import to_str_id as text_id, upc_slash

log = logging.getLogger(__name__)

ID_DB_TABLES = ["master_upc_project", "master_survey_system", "survey_data_map"]
REQUIRED_IDS = ["upc_id", "survey_id", "survey_source_id"]
SURVEY_KEYS = ["upc", "phase", "cycle", "defect_detected_on"]


def _dedup(lookup, keys, table):
    dups = lookup.duplicated(keys, keep=False)
    if dups.any():
        log.warning("%s: %s rows share %s, first one kept:\n%s", table, int(dups.sum()), keys,
                    lookup[dups].head(10).to_string(index=False))
    return lookup.drop_duplicates(keys)


def _join(df, lookup, keys, id_col, label_cols):
    """Left join adding id_col only; never changes the row count."""
    out = df.merge(lookup, on=keys, how="left", validate="many_to_one")
    if len(out) != len(df):
        raise AssertionError(f"{id_col} join changed the row count")
    missing = out[id_col].isna()
    log.info("%s: %s / %s rows matched", id_col, int((~missing).sum()), len(out))
    if missing.any():
        log.warning("%s not found for:\n%s", id_col, out.loc[missing, label_cols].drop_duplicates().to_string(index=False))
    return out


def id_db_tables(survey_id_fallback):
    """DB tables add_db_ids needs (wfm_survey_schedule only with SURVEY_ID_FALLBACK)."""
    return ID_DB_TABLES + (["wfm_survey_schedule"] if survey_id_fallback else [])


def add_db_ids(df, db_tables, survey_id_fallback=False):
    """df + upc_id / survey_source_id / survey_id / id (uuid4 per row).
    survey_id_fallback: SURVEY_ID_FALLBACK (.env), see common/db_ids.survey_id_lookup."""
    upc_table, survey_system_table = db_tables["master_upc_project"], db_tables["master_survey_system"]
    survey_map_table = db_tables["survey_data_map"]
    df = df.copy()
    # Normalised join keys (temporary)
    df["_upc"] = upc_slash(df["upc"])
    df["_src"] = df["source_system"].astype("string").str.strip()
    df["_phase"] = text_id(df["phase"])
    df["_cycle"] = text_id(df["cycle"])

    upc = _dedup(pd.DataFrame({"_upc": upc_slash(upc_table["upc"]), "upc_id": upc_table["id"]})
                 .dropna(subset=["_upc"]), ["_upc"], "master_upc_project")
    df = _join(df, upc, ["_upc"], "upc_id", ["upc"])

    src = _dedup(pd.DataFrame({"_src": survey_system_table["system_code"].astype("string").str.strip(),
                               "survey_source_id": survey_system_table["id"]})
                 .dropna(subset=["_src"]), ["_src"], "master_survey_system")
    df = _join(df, src, ["_src"], "survey_source_id", ["source_system"])

    # survey_id on upc + phase + cycle (no date); several ids -> SURVEY_ID_FALLBACK (common/db_ids.py)
    survey = survey_id_lookup(survey_map_table, db_tables.get("wfm_survey_schedule"), survey_id_fallback)
    df = _join(df, survey, SURVEY_ID_KEYS, "survey_id", SURVEY_KEYS)

    df["id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    return df.drop(columns=["_upc", "_src", "_phase", "_cycle"])


def drop_rows_without_ids(df, drop=True):
    """Rows with an empty upc_id / survey_id / survey_source_id: dropped (drop=True) or kept (drop=False); logged
    either way."""
    missing = df[REQUIRED_IDS].isna().any(axis=1)
    if missing.any():
        log.warning("%s %s / %s rows with an empty %s:\n%s", "Dropped" if drop else "Kept (DROP_ROWS_WITHOUT_IDS=False)",
                    int(missing.sum()), len(df), " / ".join(REQUIRED_IDS),
                    df.loc[missing, SURVEY_KEYS + REQUIRED_IDS].drop_duplicates(SURVEY_KEYS).to_string(index=False))
    return df[~missing].reset_index(drop=True) if drop else df.reset_index(drop=True)
