"""RIS Trends KPI (new silver logic). All paths and settings are in config.RisTrends.

    ris-kpi ris-trends

Only new surveys (ris_trends/append.py): reads the newest saved ris_trends_data (RisTrends.OUTPUT_ROOT,
year=/month=/day= folders, else latest/), keeps the silver rows (master_roughness_parent / master_rutting_parent, every
UPC folder of RisTrends.SILVER_INPUT_DIR) of the surveys (upc + phase + cycle + detected_on) not in it yet, builds
their rows, adds the DB id columns (upc_id, survey_id, survey_source_id, id; ris_trends/db_ids.py), appends them
(created_at = updated_at = this run) and saves the table under the run date + latest/ (common/storage.py). Existing
rows are never changed. Nothing saved yet -> every survey; no new survey -> nothing is calculated or written.

Per grain (upc, source_system, detected_on, phase, cycle, zone, pavement_type), 3 rows:
  roughness_average_mm_km / average_rutting_depth_mm  mean metric_value of the 'As per CA' rows (kpi_metrics.py)
  pci_value                                           mean pci of PCI_TABLE per upc / phase / cycle / pavement / date
  <defect>_ca_category / _ca_threshold / lane_length  Good (metric_value <= threshold_value) / Poor (>) / empty,
                                                      threshold text from threshold_value, lengths in m (categories.py)
  <defect>_irc_category                               Good / Poor / Fair; IRC threshold and lane length empty
"""
import logging
import time
from datetime import datetime

from ris_kpi import config
from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.storage import latest_location, save_run
from ris_kpi.config import RisTrends as cfg
from ris_kpi.ris_trends.append import append_ris_trends_data, keep_new_surveys, load_existing
from ris_kpi.ris_trends.categories import build_categories
from ris_kpi.ris_trends.db_ids import ID_DB_TABLES, add_db_ids, drop_rows_without_ids
from ris_kpi.ris_trends.kpi_metrics import GRAIN, build_kpis, check_pci_sources
from ris_kpi.ris_trends.loaders import filter_project_upcs, load_parent

log = logging.getLogger(__name__)

FINAL_COLUMNS = [
    "id", "upc", "upc_id", "phase", "cycle", "pavement_type", "survey_id", "survey_source_id", "defect_detected_on",
    "pci_value", "roughness_average_mm_km", "average_rutting_depth_mm",
    "roughness_ca_category", "roughness_ca_threshold", "lane_length_roughness_ca",
    "roughness_irc_category", "roughness_irc_threshold", "lane_length_roughness_irc",
    "rutting_ca_category", "rutting_ca_threshold", "lane_length_rutting_ca",
    "rutting_irc_category", "rutting_irc_threshold", "lane_length_rutting_irc",
]


def combine(kpis, categories):
    """KPIs repeated on the 3 category rows of their grain (outer join: nothing is lost). row_rank only sorts."""
    df = kpis.merge(categories, on=GRAIN, how="outer")
    df = df.sort_values(GRAIN + ["row_rank"]).rename(columns={"detected_on": "defect_detected_on"})
    return df.drop(columns="row_rank").reset_index(drop=True)


def run_ris_trends(run_time=None):
    """Returns the location of the saved ris_trends_data (its latest/ copy when nothing was written)."""
    run_time = run_time or datetime.now()
    start = time.time()
    output_path = latest_location(cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME)

    # --- 1. Silver tables (all UPCs), only the surveys not in ris_trends_data yet ---
    tables = {name: load_parent(cfg.SILVER_INPUT_DIR, name, cfg.KEEP_UPCS, cfg.KEEP_PAVEMENT_TYPES)
              for name in dict.fromkeys([cfg.ROUGHNESS_TABLE, cfg.RUTTING_TABLE, cfg.PCI_TABLE])}
    existing = load_existing(cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME)
    tables, n_new = keep_new_surveys(tables, existing)
    if not n_new:
        log.info("No new survey in the silver tables — nothing written (%s unchanged)", output_path)
        return output_path

    # --- 2. DB tables: UPC filter, DB ids ---
    db_tables = load_db_tables(cfg.DB_TABLES_DIR, ID_DB_TABLES)
    tables = filter_project_upcs(tables, db_tables["master_upc_project"], cfg.FILTER_BY_PROJECT_MASTER)
    roughness, rutting, pci_source = (tables[cfg.ROUGHNESS_TABLE], tables[cfg.RUTTING_TABLE], tables[cfg.PCI_TABLE])

    # --- 3. Averages + PCI, 4. categories ---
    check_pci_sources(roughness, rutting)
    kpis = build_kpis(roughness, rutting, pci_source, config.CA_THRESHOLD_NAME)
    categories = build_categories(roughness, rutting, config.CA_THRESHOLD_NAME)
    rows = combine(kpis, categories)

    # --- 5. DB ids -> rows of the new surveys ---
    created = drop_rows_without_ids(add_db_ids(rows, db_tables))[FINAL_COLUMNS]
    log.info("ris_trends_data of the new surveys — %s rows × %s cols", f"{len(created):,}", created.shape[1])

    # --- 6. Appended to the saved ris_trends_data (created_at = updated_at = this run) -> run date + latest ---
    if created.empty and existing is not None:
        log.info("No row left for the new surveys — nothing written (%s unchanged)", output_path)
        return output_path
    ris_trends_data = append_ris_trends_data(existing, created, run_time)
    path = save_run(ris_trends_data, cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME, run_time.date())
    log.info("RIS trends done in %.1fs — %s", time.time() - start, path)
    return path
