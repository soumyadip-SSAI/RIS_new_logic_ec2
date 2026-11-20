"""UPC lengths and the UPC filter from the DB table master_upc_project (RisView.DB_TABLES_DIR, common/db_tables.py).

    ris_view_data column     master_upc_project
    upc_length_km            length_km
    upc_lane_length_km       lane_length_km
"""
import logging

import pandas as pd

from ris_kpi.common.keys import upc_slash

log = logging.getLogger(__name__)

# RIS view column -> master_upc_project column (read as numbers, invalid -> NaN)
PROJECT_SOURCE = {
    "upc_length_km": "length_km",
    "upc_lane_length_km": "lane_length_km",
}
PROJECT_COLUMNS = list(PROJECT_SOURCE)


def load_project_master(master_upc_project):
    """One row per UPC (column 'upc') with the PROJECT_COLUMNS lengths, from the master_upc_project table; rows
    without a UPC dropped, first row kept per UPC."""
    info = pd.DataFrame({"upc": upc_slash(master_upc_project["upc"].astype("string").str.strip()).replace("", pd.NA)})
    for out_col, src in PROJECT_SOURCE.items():
        info[out_col] = pd.to_numeric(master_upc_project[src], errors="coerce")

    n_raw = len(info)
    info = info.dropna(subset=["upc"]).drop_duplicates("upc").reset_index(drop=True)
    log.info("master_upc_project — %s rows -> %s unique UPCs (UPC lengths)", f"{n_raw:,}", f"{len(info):,}")
    return info


def filter_project_upcs(tables, project_info, enabled):
    """Keep only UPCs present in master_upc_project (all tables), when enabled."""
    if not enabled:
        log.warning("RisView.FILTER_BY_PROJECT_MASTER = False (ris_kpi/config.py): UPCs are NOT filtered against "
                    "master_upc_project")
        return tables
    valid_upcs = set(project_info["upc"].dropna())
    filtered = {}
    for name, df in tables.items():
        keep = df["upc"].isin(valid_upcs).fillna(False)
        log.info("  %-28s — %8s → %8s  (dropped %s rows with unknown UPC)",
                 name, f"{len(df):,}", f"{int(keep.sum()):,}", f"{int((~keep).sum()):,}")
        filtered[name] = df[keep.astype(bool)].reset_index(drop=True)
    return filtered
