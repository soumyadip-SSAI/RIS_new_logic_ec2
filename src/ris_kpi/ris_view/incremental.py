"""New / existing surveys and the two ways a step changes its saved table (ris_view/pipeline.py picks the scenario).

A survey is one upc + phase + cycle + detected_on combination. The surveys in latest/ris_view_data.csv
(RisView.KPI_OUTPUT_ROOT) are existing; every other survey in the silver tables is new.

    INSERT (new surveys)  the step is calculated for the new surveys only; their rows are added to the newest saved
                          table (insert_surveys; rows of those surveys already in it are replaced, never doubled)
    UPDATE (all surveys)  the step is calculated for every survey; matching rows of the newest saved table get the
                          new values of some columns only (update_rows); no row is added or removed

    table              UPDATE: row key                              columns updated
    silver status      SILVER_ROW_KEY                               defect_reference_id / current_status /
                                                                    final_status, only rows with a DB case
                                                                    (current_status found); others stay as they are
    intermediate_10m   INTERMEDIATE_KEY                             INTERMEDIATE_UPDATE (statuses)
    ris_view_data      RIS_VIEW_KEY                                 RIS_VIEW_UPDATE (counts, affected lengths);
                                                                    updated_at = this run on rows whose values changed
"""
import logging

import pandas as pd

from ris_kpi.common.db_ids import survey_key

log = logging.getLogger(__name__)

INSERT, UPDATE = "insert", "update"

# survey columns: upc, phase, cycle, date (the date column name differs per table)
SILVER_SURVEY = ["upc", "phase", "cycle", "detected_on"]
TABLE_SURVEY = ["upc", "phase", "cycle", "defect_detected_on"]  # intermediate_10m_data, ris_view_data

SILVER_ROW_KEY = ["id", "upc", "phase", "cycle", "detected_on"]  # silver ids restart per survey
STATUS_UPDATE = ["defect_reference_id", "current_status", "final_status"]
INTERMEDIATE_KEY = ["upc", "zone", "phase", "cycle", "source_system", "defect_detected_on", "lane",
                    "start_chainage", "end_chainage"]
INTERMEDIATE_UPDATE = ["roughness_status", "rutting_status", "cracking_status", "total_status"]
RIS_VIEW_KEY = ["survey_id", "survey_source_id", "upc_id", "defect_type", "pavement_type", "status"]
RIS_VIEW_UPDATE = ["total_defects_ca", "affected_length_ca_m", "affected_length_ca_km"]


# --- surveys ---------------------------------------------------------------------------------------------------

def surveys_of(df, columns):
    return set() if df is None else set(survey_key(df, columns).dropna())


def keep_surveys(df, surveys, columns):
    """Rows of the given surveys (all rows when surveys is None)."""
    if surveys is None:
        return df
    return df[survey_key(df, columns).isin(surveys).fillna(False).astype(bool).to_numpy()].reset_index(drop=True)


# --- INSERT ----------------------------------------------------------------------------------------------------

def insert_surveys(base, rows, columns, label):
    """base (newest saved table, None = none) + rows of new surveys. Rows of those surveys already in base are
    replaced (logged), so a survey is never in the table twice."""
    if base is None:
        log.info("%s: %s rows inserted (no saved table yet)", label, f"{len(rows):,}")
        return rows.reset_index(drop=True)
    surveys = surveys_of(rows, columns)
    replaced = survey_key(base, columns).isin(surveys).fillna(False).astype(bool)
    if replaced.any():
        log.warning("%s: %s rows of the inserted surveys were already saved — replaced", label,
                    f"{int(replaced.sum()):,}")
    order = list(base.columns) + [c for c in rows.columns if c not in base.columns]
    log.info("%s: %s rows inserted (%s surveys) into %s saved rows", label, f"{len(rows):,}", len(surveys),
             f"{int((~replaced).sum()):,}")
    return pd.concat([base[~replaced.to_numpy()], rows], ignore_index=True).reindex(columns=order)


# --- UPDATE ----------------------------------------------------------------------------------------------------

def _row_key(df, key):
    cols = [df[c].astype("string").fillna("") for c in key]
    return cols[0].str.cat(cols[1:], sep="|")


def update_rows(base, fresh, key, columns, label, touch=None):
    """base rows whose `key` is in fresh get fresh's `columns`; no row is added or removed. Both tables as text
    (common/storage.as_text). touch = (column, value): set on the rows whose values changed (e.g. updated_at)."""
    base_key, fresh_key = _row_key(base, key), _row_key(fresh, key)
    if fresh_key.duplicated().any():
        raise AssertionError(f"{label}: {int(fresh_key.duplicated().sum())} new rows share the key {key}")
    if base_key.duplicated().any():
        log.warning("%s: %s saved rows share the key %s — each of them is updated", label,
                    f"{int(base_key.duplicated().sum()):,}", key)

    hit = base_key.isin(set(fresh_key)).to_numpy()
    new_values = fresh.set_index(fresh_key.to_numpy()).loc[base_key[hit].to_numpy(), columns].to_numpy(dtype=object)
    old_values = base.loc[hit, columns].to_numpy(dtype=object)
    same = (pd.DataFrame(old_values).fillna("").astype(str).to_numpy()
            == pd.DataFrame(new_values).fillna("").astype(str).to_numpy())
    changed = ~same.all(axis=1)

    out = base.copy()
    out.loc[hit, columns] = new_values
    if touch is not None:
        out.loc[out.index[hit][changed], touch[0]] = touch[1]

    missing = int((~fresh_key.isin(set(base_key))).sum())
    log.info("%s: %s / %s saved rows matched, %s with changed values, %s not matched (unchanged)", label,
             f"{int(hit.sum()):,}", f"{len(base):,}", f"{int(changed.sum()):,}", f"{int((~hit).sum()):,}")
    if missing:
        log.warning("%s: %s new rows have no saved row on %s — not inserted (update only)", label,
                    f"{missing:,}", key)
    return out


def update_silver_status(base, fresh, label):
    """Silver status UPDATE: only rows with a DB case (current_status found) change; the others keep their saved
    defect_reference_id / current_status / final_status."""
    with_case = fresh[fresh["current_status"].notna() & (fresh["current_status"].astype("string") != "")]
    log.info("%s: %s / %s rows have a DB case", label, f"{len(with_case):,}", f"{len(fresh):,}")
    return update_rows(base, with_case, SILVER_ROW_KEY, STATUS_UPDATE, label)
