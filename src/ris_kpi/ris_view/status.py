"""defect_reference_id / current_status / final_status for the silver status tables (RisView.STATUS_TABLES).

  1) defect_reference_id: <table>.<STATUS_KEY> (display_defect_id) = udm_defect_reference.defect_id, for that
                          table's defect_collection -> udm_defect_reference.id   (keys compared as trimmed text,
                          '123.0' = '123')
  2) current_status:      defect_reference_id = dlm_defect_case.defect_reference_id -> current_status
  3) final_status:        'Closed' if current_status is CLOSED / CANCELLED, 'Open' for any other status and when there
                          is no current_status (no DB case = not closed), so it is never null
Both joins are left joins on keys that must be unique on the DB side (else ValueError), so no silver row is
duplicated or lost.

Rows of a new survey (INSERT, ris_view/incremental.py) get final_status 'Open' without a DB lookup (add_open_status).
"""
import logging

import pandas as pd

log = logging.getLogger(__name__)

STATUS_COLUMNS = ["defect_reference_id", "current_status", "final_status"]
CLOSED_STATUSES = {"CLOSED", "CANCELLED"}


def _unique(df, key, label):
    """Keys must be unique on the DB side, or the join would duplicate silver rows (raised, as before)."""
    dups = df[key].duplicated(keep=False)
    if dups.any():
        raise ValueError(f"{label}: {int(dups.sum())} rows share a {key} (duplicate) — the join would duplicate "
                         "silver rows")
    return df


def match_key(series):
    """Join key as trimmed text; a number read as float ('123.0') matches its text form ('123'); empty = null."""
    return (series.astype("string").str.strip().str.replace(r"^(-?\d+)\.0+$", r"\1", regex=True)
            .replace("", pd.NA))


def reference_map(udm_defect_reference, defect_collection):
    """udm_defect_reference.defect_id (as "_key") -> defect_reference_id for one defect_collection."""
    ref = udm_defect_reference.loc[udm_defect_reference["defect_collection"] == defect_collection,
                                   ["defect_id", "id"]]
    ref = ref.rename(columns={"defect_id": "_key", "id": "defect_reference_id"})
    ref["_key"] = match_key(ref["_key"])
    return _unique(ref.dropna(subset=["_key"]), "_key", f"udm_defect_reference[{defect_collection}]")


def case_status(dlm_defect_case):
    """defect_reference_id -> current_status."""
    cases = dlm_defect_case[["defect_reference_id", "current_status"]].dropna(subset=["defect_reference_id"])
    return _unique(cases, "defect_reference_id", "dlm_defect_case")


def final_status(current_status):
    """'Closed' for CLOSED / CANCELLED, else 'Open' (also when current_status is empty)."""
    status = current_status.astype("string").str.strip().str.upper()
    out = pd.Series("Open", index=current_status.index, dtype="string")
    out[status.isin(CLOSED_STATUSES).fillna(False).astype(bool)] = "Closed"
    return out


def _with_status(silver, status):
    """silver with the status columns placed right after id."""
    rest = [c for c in silver.columns if c not in ["id"] + STATUS_COLUMNS]
    return pd.concat([silver[["id"]].reset_index(drop=True), status[STATUS_COLUMNS].reset_index(drop=True),
                      silver[rest].reset_index(drop=True)], axis=1)


def add_status(silver, defect_collection, udm_defect_reference, cases, silver_key="id"):
    """silver + defect_reference_id / current_status / final_status (rows whose silver_key is null get no status).
    silver_key: the silver column matched to udm_defect_reference.defect_id (RisView.STATUS_KEY)."""
    if silver_key not in silver.columns:
        raise KeyError(f"{defect_collection}: the silver table has no {silver_key} column (RisView.STATUS_KEY), "
                       f"needed to match udm_defect_reference.defect_id")
    ref = reference_map(udm_defect_reference, defect_collection)
    keys = pd.DataFrame({"_key": match_key(silver[silver_key])})
    keys = keys.merge(ref, on="_key", how="left", validate="many_to_one")
    keys = keys.merge(cases, on="defect_reference_id", how="left", validate="many_to_one")
    keys["final_status"] = final_status(keys["current_status"])
    if len(keys) != len(silver):
        raise AssertionError("Join changed the row count")

    out = _with_status(silver, keys)
    counts = out["final_status"].value_counts(dropna=False)
    log.info("  %-28s (on %s) defect_reference_id: %s / %s | current_status: %s | final_status: %s",
             defect_collection, silver_key, f"{int(out['defect_reference_id'].notna().sum()):,}", f"{len(out):,}",
             f"{int(out['current_status'].notna().sum()):,}",
             ", ".join(f"{'null' if pd.isna(k) else k}={v:,}" for k, v in counts.items()))
    return out


def add_open_status(silver, label):
    """New survey (not in ris_view_data yet): no DB lookup — defect_reference_id / current_status empty,
    final_status 'Open' on every row. Same columns and order as add_status."""
    status = pd.DataFrame({"defect_reference_id": pd.NA, "current_status": pd.NA, "final_status": "Open"},
                          index=range(len(silver)))
    out = _with_status(silver, status)
    log.info("  %-28s new surveys: %s rows | final_status: Open (no DB lookup)", label, f"{len(out):,}")
    return out
