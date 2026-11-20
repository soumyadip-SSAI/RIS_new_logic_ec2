"""DB id columns and timestamps for ris_view_data (same lookups as the old pipeline, ris_kpi/ris_view/db_ids.py):

    upc_id            master_upc_project    upc = upc                                                 -> id
    survey_source_id  master_survey_system  source_system ('NSV') = system_code                       -> id
    survey_id         survey_data_map       upc + phase + cycle + defect_detected_on
                                            = upc + phase + cycle + survey_date                       -> survey_schedule_id
    defect_type_id    master_defect_type    source_system + asset_type + defect_name
                                            = survey_system + asset_type + defect_name                -> id
                      asset_type / defect_name from defect_type + pavement_type (defect_type_keys); Rutting: PAVEMENT,
                      else Flexible Pavement; Total -> no defect_type_id
    id                random uuid4 per row. Set when a survey's rows are inserted; an UPDATE run keeps the saved id
                      (incremental.update_rows)
created_at / updated_at are set by ris_view/view.py (run time; UPDATE: updated_at only on rows whose values changed).

Every lookup is de-duplicated on its key (first row kept, logged), so the joins never add rows. Rows without
upc_id / survey_id / survey_source_id are logged and, with DROP_ROWS_WITHOUT_IDS, dropped.
"""
import logging
import uuid

import numpy as np
import pandas as pd

from ris_kpi.common.keys import to_str_id as text_id, upc_slash

log = logging.getLogger(__name__)

ID_DB_TABLES = ["master_upc_project", "master_survey_system", "survey_data_map", "master_defect_type"]
REQUIRED_IDS = ["upc_id", "survey_id", "survey_source_id"]
SURVEY_COLS = ["upc", "phase", "cycle", "defect_detected_on"]
DEFECT_TYPE_KEYS = ["survey_system", "asset_type", "defect_name"]
# (asset_type, defect_name) -> key tried when the first one has no id for that survey system
DEFECT_TYPE_FALLBACK = {("PAVEMENT", "Rutting"): ("Flexible Pavement", "Rutting")}

FINAL_COLUMNS = [
    "id", "survey_source_id", "upc", "upc_id", "phase", "cycle", "defect_detected_on", "survey_id",
    "defect_type", "defect_type_id", "pavement_type", "status", "upc_length_km", "upc_lane_length_km",
    "total_defects_ca", "affected_length_ca_m", "affected_length_ca_km", "created_at", "updated_at",
]


def _date_text(series):
    """Dates as yyyy-mm-dd ('2026-04-21 00:00:00+00' -> '2026-04-21')."""
    s = series.astype("string").str.strip()
    iso = s.str.match(r"^\d{4}-\d{2}-\d{2}").fillna(False).astype(bool)
    parsed = pd.to_datetime(s.where(~iso), dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
    return s.str[:10].where(iso, parsed.astype("string"))


def _dedup(lookup, keys, table):
    dups = lookup.duplicated(keys, keep=False)
    if dups.any():
        log.warning("%s: %s rows share %s, first one kept", table, int(dups.sum()), keys)
    return lookup.drop_duplicates(keys)


def _join(df, lookup, keys, id_col, label_cols):
    """Left join adding id_col only; never changes the row count."""
    out = df.merge(lookup, on=keys, how="left", validate="many_to_one")
    if len(out) != len(df):
        raise AssertionError(f"{id_col} join changed the row count")
    missing = out[id_col].isna()
    log.info("%s: %s / %s rows matched", id_col, int((~missing).sum()), len(out))
    if missing.any():
        log.warning("%s not found for:\n%s", id_col,
                    out.loc[missing, label_cols].drop_duplicates().to_string(index=False))
    return out


def defect_type_keys(defect_type, pavement_type):
    """defect_type + pavement_type -> (asset_type, defect_name) of master_defect_type (old rules):
    Pothole / Cracking: Cement Concrete -> 'Rigid Pavements', else 'Flexible Pavement';
                        Cracking on Cement Concrete -> 'Crack on Rigid Pavement', else 'Cracking'
    Roughness / Ravelling -> 'Pavement (NSV)'; Rutting -> 'PAVEMENT'; Total -> none."""
    pavement = pavement_type.astype("string").replace("", pd.NA).fillna("Asphalt")
    is_point = defect_type.isin(["Pothole", "Cracking"])
    is_concrete = pavement.str.strip().str.lower().isin(["cement concrete", "cememt concrete"]).fillna(False)
    asset_type = np.select(
        [is_point & is_concrete, is_point & ~is_concrete, defect_type.isin(["Roughness", "Ravelling"]),
         defect_type == "Rutting"],
        ["Rigid Pavements", "Flexible Pavement", "Pavement (NSV)", "PAVEMENT"], default=None)
    defect_name = np.select(
        [(defect_type == "Cracking") & is_concrete, (defect_type == "Cracking") & ~is_concrete,
         defect_type == "Pothole", defect_type == "Roughness", defect_type == "Ravelling", defect_type == "Rutting"],
        ["Crack on Rigid Pavement", "Cracking", "Pothole", "Roughness", "Ravelling", "Rutting"], default=None)
    return (pd.Series(asset_type, index=defect_type.index, dtype="string"),
            pd.Series(defect_name, index=defect_type.index, dtype="string"))


def _add_defect_type_id(df, master_defect_type):
    if master_defect_type is None:
        log.warning("No master_defect_type — defect_type_id left empty")
        df["defect_type_id"] = pd.NA
        return df
    lk = _dedup(master_defect_type[DEFECT_TYPE_KEYS + ["id"]].rename(columns={"id": "defect_type_id"})
                .dropna(subset=DEFECT_TYPE_KEYS), DEFECT_TYPE_KEYS, "master_defect_type")
    df["_asset_type"], df["_defect_name"] = defect_type_keys(df["defect_type"], df["pavement_type"])
    keys = pd.DataFrame({"survey_system": df["_src"], "asset_type": df["_asset_type"],
                         "defect_name": df["_defect_name"]})
    df["defect_type_id"] = keys.merge(lk, on=DEFECT_TYPE_KEYS, how="left",
                                      validate="many_to_one")["defect_type_id"].to_numpy()
    for (asset, name), (fb_asset, fb_name) in DEFECT_TYPE_FALLBACK.items():
        rows = df["defect_type_id"].isna() & (df["_asset_type"] == asset) & (df["_defect_name"] == name)
        if rows.any():
            fb = pd.DataFrame({"survey_system": df.loc[rows, "_src"], "asset_type": fb_asset, "defect_name": fb_name})
            df.loc[rows, "defect_type_id"] = fb.merge(lk, on=DEFECT_TYPE_KEYS, how="left")["defect_type_id"].to_numpy()
    real = df["defect_type"] != "Total"
    missing = real & df["defect_type_id"].isna()
    log.info("defect_type_id: %s / %s defect rows matched (Total rows have none)", int((real & ~missing).sum()),
             int(real.sum()))
    if missing.any():
        log.warning("defect_type_id not found for:\n%s", df.loc[missing, ["source_system", "defect_type",
                    "pavement_type", "_asset_type", "_defect_name"]].drop_duplicates().to_string(index=False))
    return df.drop(columns=["_asset_type", "_defect_name"])


def add_db_ids(view, db_tables):
    """view + upc_id / survey_source_id / survey_id / defect_type_id / id (uuid4 per row)."""
    master_upc_project, master_survey_system = db_tables["master_upc_project"], db_tables["master_survey_system"]
    survey_data_map, master_defect_type = db_tables["survey_data_map"], db_tables["master_defect_type"]
    df = view.copy()
    df["_upc"] = upc_slash(df["upc"])
    df["_src"] = df["source_system"].astype("string").str.strip()
    df["_phase"] = text_id(df["phase"])
    df["_cycle"] = text_id(df["cycle"])
    df["_date"] = _date_text(df["defect_detected_on"])

    upc = _dedup(pd.DataFrame({"_upc": upc_slash(master_upc_project["upc"]), "upc_id": master_upc_project["id"]})
                 .dropna(subset=["_upc"]), ["_upc"], "master_upc_project")
    df = _join(df, upc, ["_upc"], "upc_id", ["upc"])

    src = _dedup(pd.DataFrame({"_src": master_survey_system["system_code"].astype("string").str.strip(),
                               "survey_source_id": master_survey_system["id"]})
                 .dropna(subset=["_src"]), ["_src"], "master_survey_system")
    df = _join(df, src, ["_src"], "survey_source_id", ["source_system"])

    keys = ["_upc", "_phase", "_cycle", "_date"]
    survey = _dedup(pd.DataFrame({
        "_upc": upc_slash(survey_data_map["upc"]),
        "_phase": text_id(survey_data_map["phase"]),
        "_cycle": text_id(survey_data_map["cycle"]),
        "_date": _date_text(survey_data_map["survey_date"]),
        "survey_id": survey_data_map["survey_schedule_id"],
    }).dropna(subset=keys), keys, "survey_data_map")
    df = _join(df, survey, keys, "survey_id", SURVEY_COLS)

    df = _add_defect_type_id(df, master_defect_type)

    df["id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    return df.drop(columns=["_upc", "_src", "_phase", "_cycle", "_date"])


def drop_rows_without_ids(df):
    missing = df[REQUIRED_IDS].isna().any(axis=1)
    if missing.any():
        log.warning("Dropped %s / %s rows with an empty %s:\n%s", int(missing.sum()), len(df), " / ".join(REQUIRED_IDS),
                    df.loc[missing, SURVEY_COLS + REQUIRED_IDS].drop_duplicates(SURVEY_COLS).to_string(index=False))
    return df[~missing].reset_index(drop=True)


def finalize(df):
    """Output columns (source_system / zone used only for the joins, not written); created_at / updated_at last."""
    return df[FINAL_COLUMNS].reset_index(drop=True)
