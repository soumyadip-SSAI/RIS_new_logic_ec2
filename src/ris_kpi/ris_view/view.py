"""RIS view step 3 — ris_view_data.

Reads the newest saved silver status tables (step 1: total_defects_ca per status) and intermediate_10m_data (step 2:
affected_length_ca_m / _km per status), the DB tables in RisView.DB_TABLES_DIR (master_upc_project: UPC filter and
lengths; ID_DB_TABLES: DB id columns), builds the rows (ris_view/view_table.py), adds the DB id columns
(ris_view/db_ids.py) and saves ris_view_data under RisView.KPI_OUTPUT_ROOT (year=/month=/day=<run date> + latest).
ris_view/pipeline.py runs it in one of two modes (ris_view/incremental.py):

  INSERT (new surveys)  calculated for the new surveys only -> rows added to the newest saved ris_view_data,
                        created_at = updated_at = this run
  UPDATE (all surveys)  calculated for every survey -> only the counts / affected lengths (RIS_VIEW_UPDATE) of the
                        matching rows (RIS_VIEW_KEY) of the newest saved ris_view_data are updated; every other
                        column stays as it is, updated_at = this run on the rows whose values changed. No row is
                        added. No saved table yet -> saved whole.

    ris-kpi ris-view --step view        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi import config
from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.storage import as_text, load_latest, save_run
from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.db_ids import add_db_ids, drop_rows_without_ids, finalize, id_db_tables
from ris_kpi.ris_view.incremental import (INSERT, RIS_VIEW_KEY, RIS_VIEW_UPDATE, SILVER_SURVEY, UPDATE,
                                          insert_surveys, keep_surveys, update_rows)
from ris_kpi.ris_view.loaders import load_intermediate, load_status_table
from ris_kpi.ris_view.project_master import filter_project_upcs, load_project_master
from ris_kpi.ris_view.view_table import build_view

log = logging.getLogger("ris_view")

# defect_type -> silver status table whose breached rows are counted
COUNT_TABLES = {
    "Roughness": config.ROUGHNESS_TABLES["parent"],
    "Rutting": config.RUTTING_TABLES["parent"],
    "Cracking": config.CRACKING_TABLES["ca"],
    "Pothole": config.POTHOLE_TABLE,
    "Ravelling": config.RAVELLING_TABLE,
}


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns the saved ris_view_data."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder, created_at / updated_at
    run_date = run_time.date()

    # --- 1. Sources: newest saved silver status / intermediate_10m_data (only the surveys to calculate) ---
    tables = {defect: keep_surveys(load_status_table(cfg.SILVER_STATUS_ROOT, name), surveys, SILVER_SURVEY)
              for defect, name in COUNT_TABLES.items()}
    intermediate = keep_surveys(load_intermediate(cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME), surveys,
                                SILVER_SURVEY)
    db_tables = load_db_tables(cfg.DB_TABLES_DIR, id_db_tables(cfg.SURVEY_ID_FALLBACK))
    project = load_project_master(db_tables["master_upc_project"])

    # --- 2. Keep only UPCs in master_upc_project ---
    tables = filter_project_upcs(tables, project, cfg.FILTER_BY_PROJECT_MASTER)
    intermediate = filter_project_upcs({"intermediate_10m_data": intermediate}, project,
                                       cfg.FILTER_BY_PROJECT_MASTER)["intermediate_10m_data"]

    # --- 3. Counts (silver status) + affected lengths (intermediate_10m_data) per defect / pavement / status ---
    rows = build_view(tables, intermediate, project)

    # --- 4. DB ids ---
    rows = drop_rows_without_ids(add_db_ids(rows, db_tables, cfg.SURVEY_ID_FALLBACK), drop=cfg.DROP_ROWS_WITHOUT_IDS)
    fresh = as_text(finalize(rows.assign(created_at=run_time, updated_at=run_time)))
    log.info("ris_view_data of this run — %s rows × %s cols", f"{len(fresh):,}", fresh.shape[1])

    # --- 5. Into the newest saved ris_view_data -> run date + latest ---
    stamp = fresh["updated_at"].iloc[0] if len(fresh) else str(run_time)  # as written in the CSV
    base, _ = load_latest(cfg.KPI_OUTPUT_ROOT, cfg.RIS_VIEW_DATA_NAME)
    if mode == INSERT:
        out = insert_surveys(base, fresh, ["upc", "phase", "cycle", "defect_detected_on"], cfg.RIS_VIEW_DATA_NAME)
    else:
        out = fresh if base is None else update_rows(base, fresh, RIS_VIEW_KEY, RIS_VIEW_UPDATE,
                                                     cfg.RIS_VIEW_DATA_NAME, touch=("updated_at", stamp))
    save_run(out, cfg.KPI_OUTPUT_ROOT, cfg.RIS_VIEW_DATA_NAME, run_date)
    return out
