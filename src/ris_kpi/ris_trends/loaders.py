"""Silver tables for the RIS trends: master_roughness_parent / master_rutting_parent from every UPC folder
(common/silver.py), filtered to KEEP_UPCS and KEEP_PAVEMENT_TYPES; master_upc_project UPC filter."""
import logging

from ris_kpi.common.silver import load_silver

log = logging.getLogger(__name__)


def load_parent(input_dir, name, keep_upcs, keep_pavement_types):
    df = load_silver(input_dir, name, keep_upcs=keep_upcs)
    kept = df[df["pavement_type"].isin(keep_pavement_types).fillna(False).astype(bool)].reset_index(drop=True)
    log.info("%s: %s of %s rows kept (pavement_type in %s) | threshold_name %s", name, f"{len(kept):,}",
             f"{len(df):,}", keep_pavement_types, kept["threshold_name"].value_counts(dropna=False).to_dict())
    return kept


def filter_project_upcs(tables, master_upc_project, enabled):
    """Rows of UPCs in master_upc_project only (every table), when enabled."""
    if not enabled:
        log.warning("RisTrends.FILTER_BY_PROJECT_MASTER = False (ris_kpi/config.py): UPCs are NOT filtered against "
                    "master_upc_project")
        return tables
    from ris_kpi.common.keys import upc_slash
    valid = set(upc_slash(master_upc_project["upc"].dropna().astype(str).str.strip()))
    out = {}
    for name, df in tables.items():
        keep = df["upc"].isin(valid).fillna(False).astype(bool)
        if (~keep).any():
            log.info("%s: dropped %s rows of UPC(s) not in master_upc_project: %s", name, int((~keep).sum()),
                     sorted(df.loc[~keep, "upc"].dropna().unique()))
        out[name] = df[keep].reset_index(drop=True)
    return out
