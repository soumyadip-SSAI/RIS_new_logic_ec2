"""roughness_average_mm_km, average_rutting_depth_mm and pci_value per grain."""
import logging

import pandas as pd

from ris_kpi.ris_trends.spark import spark_round

log = logging.getLogger(__name__)

# One output grain (x 3 category rows)
GRAIN = ["upc", "source_system", "detected_on", "phase", "cycle", "zone", "pavement_type"]
# PCI_Value is averaged on these columns only
PCI_KEYS = ["upc", "phase", "cycle", "pavement_type", "detected_on"]


def average_metric(master, out_col, ca_threshold_name):
    """Mean metric_value of the 'As per CA' rows per grain, rounded half-up to 4 decimals."""
    src = master[master["threshold_name"] == ca_threshold_name]
    src = src.assign(metric_value=pd.to_numeric(src["metric_value"], errors="coerce"))
    agg = src.groupby(GRAIN, dropna=False)["metric_value"].mean()
    return spark_round(agg, 4).rename(out_col).reset_index()


def average_pci(master):
    """Mean pci per upc + phase + cycle + pavement_type + detected_on (all rows of the table)."""
    pci = pd.to_numeric(master["pci"], errors="coerce")
    agg = master.assign(pci=pci).groupby(PCI_KEYS, dropna=False)["pci"].mean()
    return spark_round(agg, 4).rename("pci_value").reset_index()


def check_pci_sources(roughness, rutting):
    """master_roughness and master_rutting should give the same PCI; differences are logged."""
    both = average_pci(roughness).merge(average_pci(rutting), on=PCI_KEYS, how="outer",
                                        suffixes=("_roughness", "_rutting"))
    diff = both[~((both["pci_value_roughness"] - both["pci_value_rutting"]).abs() < 1e-9)]
    if diff.empty:
        log.info("PCI: master_roughness and master_rutting give the same averages (%s surveys)", len(both))
    else:
        log.warning("PCI differs between master_roughness and master_rutting for %s row(s):\n%s",
                    len(diff), diff.to_string(index=False))


def build_kpis(roughness, rutting, pci_source, ca_threshold_name):
    """One row per grain with the two averages (outer join) and pci_value."""
    rough = average_metric(roughness, "roughness_average_mm_km", ca_threshold_name)
    rut = average_metric(rutting, "average_rutting_depth_mm", ca_threshold_name)
    kpis = rough.merge(rut, on=GRAIN, how="outer")
    kpis = kpis.merge(average_pci(pci_source), on=PCI_KEYS, how="left")
    log.info("KPIs: %s grain(s), pci_value filled on %s", len(kpis), int(kpis["pci_value"].notna().sum()))
    return kpis
