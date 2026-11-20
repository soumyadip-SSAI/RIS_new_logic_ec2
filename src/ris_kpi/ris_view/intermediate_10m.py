"""intermediate_10m_data: one row per survey + lane + 10m segment with the roughness / rutting / cracking flags
(logic of ris_new_logic/ris_kpi_8009.py, per survey) and the status of each defect (final_status of the silver
status tables).

Roughness / rutting (same steps):
  1. as_per_ca rows + breach flag of their parent (parent_id)               -> breach_flag_as_per_parent
  2. 10m rows (no 'As per IRC' rows) + the as_per_ca row they lie in: merge_asof on end_chainage
     (10m end <= CA end, nearest), by survey + parent_id + lane             -> ca_id, breach_flag_as_per_ca,
                                                                               breach_flag_as_per_parent
     status = final_status of the parent row (silver status table)
Cracking (no parent table):
  2. 10m rows + the as_per_ca row they lie in (merge_asof as above, by survey + lane) -> ca_id, breach_flag_as_per_ca
     status = final_status of the as_per_ca row

Flags per 10m row:
  roughness_final_flag = breach_flag_as_per_parent == 1 AND breach_flag_as_per_ca == 1   (same for rutting)
  cracking_final_flag  = breach_flag_as_per_ca == 1
  final_flag           = any of the three
  total_status         = 'Closed' when every flagged defect of the row is Closed, else 'Open' (only on flagged rows)
  length_m             = end_chainage - start_chainage
"""
import logging

import pandas as pd

from ris_kpi.ris_view.loaders import SURVEY, num

log = logging.getLogger(__name__)

KEYS = SURVEY + ["lane", "start_chainage", "end_chainage"]
DEFECTS = ["roughness", "rutting", "cracking"]


def ca_only(child, irc_only_threshold, name):
    """One row per lane + 10m: the 'As per IRC' rows are dropped ('As per CA' / 'As per CA & IRC' kept)."""
    kept = child[child["threshold_name"] != irc_only_threshold]
    log.info("%s: %s of %s rows kept (threshold_name %s)", name, f"{len(kept):,}", f"{len(child):,}",
             kept["threshold_name"].value_counts(dropna=False).to_dict())
    return kept


def _by_key(df, cols):
    return df[cols].astype("string").fillna("").agg("|".join, axis=1)


def assign_ca_rows(child, lookup, by, name):
    """Each 10m row + the as_per_ca row it lies in (merge_asof: 10m end_chainage <= ca_end_chainage, nearest)."""
    left = child.assign(end_chainage=num(child["end_chainage"]), start_chainage=num(child["start_chainage"]),
                        _by=_by_key(child, by)).sort_values("end_chainage")
    right = lookup.assign(ca_end_chainage=num(lookup["ca_end_chainage"]), _by=_by_key(lookup, by))
    right = right.drop(columns=[c for c in by if c in right.columns]).sort_values("ca_end_chainage")
    out = pd.merge_asof(left, right, left_on="end_chainage", right_on="ca_end_chainage", by="_by",
                        direction="forward").drop(columns="_by")
    if len(out) != len(child):
        raise AssertionError(f"{name}: merge_asof changed the row count")
    missing = int(out["ca_id"].isna().sum())
    log.info("%s: %s 10m rows, %s without an as_per_ca row", name, f"{len(out):,}", f"{missing:,}")
    return out


def line_defect_10m(parent, ca, child, name):
    """Roughness / rutting 10m rows with ca_id, both breach flags and the parent status."""
    parent_cols = parent[SURVEY + ["parent_id", "breach_flag", "final_status"]].rename(
        columns={"breach_flag": "breach_flag_as_per_parent", "final_status": "status"})
    if parent_cols.duplicated(SURVEY + ["parent_id"]).any():
        raise ValueError(f"{name}: parent rows are not unique on survey + parent_id")
    merged = ca.merge(parent_cols, on=SURVEY + ["parent_id"], how="left", validate="many_to_one")
    log.info("%s: as_per_ca rows without a parent: %s", name, int(merged["breach_flag_as_per_parent"].isna().sum()))

    lookup = merged[SURVEY + ["parent_id", "lane", "end_chainage", "id", "breach_flag", "breach_flag_as_per_parent",
                              "status"]].rename(columns={"end_chainage": "ca_end_chainage", "id": "ca_id",
                                                         "breach_flag": "breach_flag_as_per_ca"})
    return assign_ca_rows(child, lookup, SURVEY + ["parent_id", "lane"], name)


def cracking_10m(ca, child):
    """Cracking 10m rows with ca_id, CA breach flag and the as_per_ca row status."""
    lookup = ca[SURVEY + ["lane", "end_chainage", "id", "breach_flag", "final_status"]].rename(
        columns={"end_chainage": "ca_end_chainage", "id": "ca_id", "breach_flag": "breach_flag_as_per_ca",
                 "final_status": "status"})
    return assign_ca_rows(child, lookup, SURVEY + ["lane"], "cracking")


def _prep(df, prefix, cols):
    out = df[KEYS + ["pavement_type"] + cols].rename(columns={
        "pavement_type": f"{prefix}_pavement_type",
        "parent_id": f"{prefix}_parent_id",
        "ca_id": f"{prefix}_ca_id",
        "breach_flag": f"{prefix}_breach_flag_10m",
        "breach_flag_as_per_ca": f"{prefix}_breach_flag_as_per_ca",
        "breach_flag_as_per_parent": f"{prefix}_breach_flag_as_per_parent",
        "status": f"{prefix}_status",
    })
    dups = out.duplicated(KEYS)
    if dups.any():
        log.warning("%s: %s duplicate lane + chainage rows — first one kept", prefix, int(dups.sum()))
    return out.drop_duplicates(KEYS)


def build_intermediate_10m(rough, rut, crack):
    """rough / rut / crack: 10m rows from line_defect_10m / cracking_10m."""
    line_cols = ["parent_id", "ca_id", "breach_flag", "breach_flag_as_per_ca", "breach_flag_as_per_parent", "status"]
    m = (_prep(rough, "roughness", line_cols)
         .merge(_prep(rut, "rutting", line_cols), on=KEYS, how="outer")
         .merge(_prep(crack, "cracking", ["ca_id", "breach_flag", "breach_flag_as_per_ca", "status"]), on=KEYS,
                how="outer"))
    # one pavement_type per row: cracking's, else roughness', else rutting's
    m.insert(len(KEYS), "pavement_type", m["cracking_pavement_type"].fillna(m["roughness_pavement_type"])
             .fillna(m["rutting_pavement_type"]))
    m = m.drop(columns=[f"{d}_pavement_type" for d in DEFECTS])

    flag = lambda col: num(m[col]).fillna(0)
    for suffix in ["breach_flag_10m", "breach_flag_as_per_ca", "breach_flag_as_per_parent"]:
        cols = [c for c in m.columns if c.endswith(suffix)]
        m[f"any_{suffix}"] = pd.concat([flag(c) for c in cols], axis=1).max(axis=1).astype(int)

    for d in ("roughness", "rutting"):
        m[f"{d}_final_flag"] = ((flag(f"{d}_breach_flag_as_per_parent") == 1)
                                & (flag(f"{d}_breach_flag_as_per_ca") == 1)).astype(int)
    m["cracking_final_flag"] = (flag("cracking_breach_flag_as_per_ca") == 1).astype(int)
    m["final_flag"] = m[[f"{d}_final_flag" for d in DEFECTS]].max(axis=1)

    # Total status: Closed only when every flagged defect of the row is Closed
    all_closed = pd.Series(True, index=m.index)
    for d in DEFECTS:
        all_closed &= (m[f"{d}_final_flag"] == 0) | (m[f"{d}_status"].fillna("Open") == "Closed")
    m["total_status"] = all_closed.map({True: "Closed", False: "Open"}).where(m["final_flag"] == 1)

    m["length_m"] = (m["end_chainage"] - m["start_chainage"]).round(3)
    m = m.sort_values(SURVEY + ["lane", "start_chainage"]).reset_index(drop=True)
    for col in [f"{d}_final_flag" for d in DEFECTS] + ["final_flag"]:
        log.info("intermediate_10m_data: %-20s %10.2f km", col, m.loc[m[col] == 1, "length_m"].sum() / 1000)
    log.info("intermediate_10m_data — %s rows x %s cols", f"{len(m):,}", m.shape[1])
    return m
