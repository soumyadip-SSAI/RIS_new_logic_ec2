"""ris_view_data: per survey, one row per defect type (+ 'Total') / pavement group / status (open / close).

total_defects_ca (count of breach_flag == 1 rows, distinct id, per status = final_status of the row):
    Roughness  master_roughness_parent        Rutting    master_rutting_parent
    Cracking   master_cracking_as_per_ca      Pothole    master_pothole        Ravelling  master_ravelling
    Total      sum over the defect types (same status)
affected_length_ca_m (sum of length_m of the intermediate_10m_data rows, per status):
    Roughness  roughness_final_flag == 1, status roughness_status
    Rutting    rutting_final_flag == 1,   status rutting_status
    Cracking   cracking_final_flag == 1,  status cracking_status
    Pothole / Ravelling  0 (not in intermediate_10m_data)
    Total      final_flag == 1,           status total_status
affected_length_ca_km = affected_length_ca_m / 1000

Pavement group: Cracking / Pothole split into 'Cement Concrete' and 'Asphalt' (any other pavement_type, e.g. Hybrid or
empty -> 'Asphalt'); other defect types and Total: empty. Every grain gets an open and a close row (0 when missing).
"""
import logging

import pandas as pd

from ris_kpi.ris_view.loaders import SURVEY, num

log = logging.getLogger(__name__)

PAVEMENT = "pavement_type"
STATUSES = ["Open", "Closed"]
STATUS_LABELS = {"Open": "open", "Closed": "close"}
DEFECT_ORDER = ["Roughness", "Rutting", "Cracking", "Pothole", "Ravelling", "Total"]
POINT_DEFECTS = {"Cracking", "Pothole"}
CONCRETE_NAMES = {"cement concrete", "cememt concrete"}
KNOWN_PAVEMENTS = CONCRETE_NAMES | {"asphalt", "hybrid"}
CONCRETE, ASPHALT = "Cement Concrete", "Asphalt"

GRAIN = SURVEY + ["defect_type", PAVEMENT]
FINAL_COLUMNS = [
    "source_system", "upc", "zone", "phase", "cycle", "defect_detected_on", "defect_type", "pavement_type", "status",
    "upc_length_km", "upc_lane_length_km", "total_defects_ca", "affected_length_ca_m", "affected_length_ca_km",
]


def pavement_group(defect_type, pavement):
    """'Cement Concrete' / 'Asphalt' for Cracking / Pothole, '' for the other defect types."""
    text = pavement.astype("string").str.strip().str.lower()
    unknown = sorted(set(pavement[~text.isin(KNOWN_PAVEMENTS).fillna(False).astype(bool)].dropna().astype(str)))
    if unknown and defect_type in POINT_DEFECTS:
        log.warning("%s: pavement_type %s counted as %s", defect_type, unknown, ASPHALT)
    if defect_type not in POINT_DEFECTS:
        return pd.Series("", index=pavement.index, dtype="object")
    concrete = text.isin(CONCRETE_NAMES).fillna(False).astype(bool)
    return pd.Series(ASPHALT, index=pavement.index, dtype="object").where(~concrete, CONCRETE)


def defect_counts(count_tables):
    """{defect_type: silver table with status} -> grains (every row, breached or not) and breached counts."""
    grains, counts = [], []
    for defect, df in count_tables.items():
        rows = df[SURVEY].assign(defect_type=defect, **{PAVEMENT: pavement_group(defect, df[PAVEMENT])},
                                 status=df["final_status"].fillna("Open"), _id=df["id"])
        grains.append(rows[GRAIN].drop_duplicates())
        breached = rows[(num(df["breach_flag"]) == 1).to_numpy()]
        counts.append(breached.groupby(GRAIN + ["status"], dropna=False)["_id"].nunique()
                      .rename("total_defects_ca").reset_index())
        log.info("%-10s breached rows: %s | %s", defect, f"{len(breached):,}",
                 ", ".join(f"{k}={v:,}" for k, v in breached["status"].value_counts().items()))
    return pd.concat(grains, ignore_index=True), pd.concat(counts, ignore_index=True)


def defect_lengths(intermediate_10m):
    """Roughness / rutting / cracking affected lengths per grain + status from intermediate_10m_data."""
    parts = []
    for defect in ("Roughness", "Rutting", "Cracking"):
        d = defect.lower()
        rows = intermediate_10m[intermediate_10m[f"{d}_final_flag"] == 1]
        part = rows[SURVEY].assign(defect_type=defect, **{PAVEMENT: pavement_group(defect, rows[PAVEMENT])},
                                   status=rows[f"{d}_status"].fillna("Open"), length=rows["length_m"])
        parts.append(part.groupby(GRAIN + ["status"], dropna=False)["length"].sum()
                     .rename("affected_length_ca_m").reset_index())
    return pd.concat(parts, ignore_index=True)


def total_lengths(intermediate_10m):
    rows = intermediate_10m[intermediate_10m["final_flag"] == 1]
    return (rows.groupby(SURVEY + ["total_status"], dropna=False)["length_m"].sum()
            .rename("affected_length_ca_m").reset_index().rename(columns={"total_status": "status"}))


def _with_statuses(grains):
    return grains.drop_duplicates().merge(pd.DataFrame({"status": STATUSES}), how="cross")


def build_view(count_tables, intermediate_10m, upc_lengths):
    """Every grain x open / close with total_defects_ca / affected_length_ca_m / _km and the UPC lengths; also the
    survey columns (source_system, zone) used by the DB id joins."""
    grains, counts = defect_counts(count_tables)
    lengths = defect_lengths(intermediate_10m)
    grains = pd.concat([grains, lengths[GRAIN]], ignore_index=True)

    # defect rows: every grain x open / close, counts and lengths (0 when missing)
    rows = (_with_statuses(grains)
            .merge(counts, on=GRAIN + ["status"], how="left", validate="one_to_one")
            .merge(lengths, on=GRAIN + ["status"], how="left", validate="one_to_one"))
    rows["total_defects_ca"] = rows["total_defects_ca"].fillna(0).astype("int64")
    rows["affected_length_ca_m"] = rows["affected_length_ca_m"].fillna(0.0)

    # Total rows: counts summed over the defect types, length of final_flag rows by total_status
    totals = (_with_statuses(grains[SURVEY])
              .merge(rows.groupby(SURVEY + ["status"], dropna=False)["total_defects_ca"].sum().reset_index(),
                     on=SURVEY + ["status"], how="left", validate="one_to_one")
              .merge(total_lengths(intermediate_10m), on=SURVEY + ["status"], how="left", validate="one_to_one")
              .assign(defect_type="Total", **{PAVEMENT: ""}))
    totals["total_defects_ca"] = totals["total_defects_ca"].fillna(0).astype("int64")
    totals["affected_length_ca_m"] = totals["affected_length_ca_m"].fillna(0.0)

    view = pd.concat([rows, totals], ignore_index=True)
    view["affected_length_ca_m"] = view["affected_length_ca_m"].round(3)
    view["affected_length_ca_km"] = (view["affected_length_ca_m"] / 1000).round(6)
    view = view.merge(upc_lengths, on="upc", how="left", validate="many_to_one")

    order = {d: i for i, d in enumerate(DEFECT_ORDER)}
    view = (view.assign(_d=view["defect_type"].map(order), _s=view["status"].map({"Open": 0, "Closed": 1}))
            .sort_values(SURVEY + ["_d", PAVEMENT, "_s"]).drop(columns=["_d", "_s"]))
    view["status"] = view["status"].replace(STATUS_LABELS)
    view[PAVEMENT] = view[PAVEMENT].replace("", pd.NA)
    log.info("ris_view_data — %s rows (%s surveys)", f"{len(view):,}", len(view[SURVEY].drop_duplicates()))
    view = view.rename(columns={"detected_on": "defect_detected_on"})
    return view[FINAL_COLUMNS].reset_index(drop=True)
