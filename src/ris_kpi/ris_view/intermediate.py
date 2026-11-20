"""RIS view step 2 — intermediate_10m_data (ris_view/intermediate_10m.py): one row per survey + lane + 10m segment
with the roughness / rutting / cracking flags and statuses.

Reads the silver tables (as_per_ca / child_10m, RisView.SILVER_INPUT_DIR, all UPC folders) and the newest saved
silver status tables (parents, cracking as_per_ca: breach flags + final_status, step 1) and saves intermediate_10m_data
under RisView.INTERMEDIATE_ROOT (year=/month=/day=<run date> + latest). ris_view/pipeline.py runs it in one of two
modes (ris_view/incremental.py):

  INSERT (new surveys)  calculated for the new surveys only -> rows added to the newest saved intermediate_10m_data
  UPDATE (all surveys)  calculated for every survey -> the statuses (INTERMEDIATE_UPDATE) of the matching rows of the
                        newest saved intermediate_10m_data are updated. No saved table yet -> saved whole.

    ris-kpi ris-view --step intermediate        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi import config
from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.silver import load_silver
from ris_kpi.common.storage import as_text, load_latest, save_run
from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.incremental import (INSERT, INTERMEDIATE_KEY, INTERMEDIATE_UPDATE, SILVER_SURVEY, TABLE_SURVEY,
                                          UPDATE, insert_surveys, keep_surveys, update_rows)
from ris_kpi.ris_view.intermediate_10m import build_intermediate_10m, ca_only, cracking_10m, line_defect_10m
from ris_kpi.ris_view.loaders import load_status_table
from ris_kpi.ris_view.project_master import filter_project_upcs, load_project_master

log = logging.getLogger("intermediate_10m")


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns the saved intermediate_10m_data."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder
    run_date = run_time.date()

    # --- 1. Sources: silver status (parents, cracking as_per_ca), silver as_per_ca / child_10m ---
    rough, rut, crack = config.ROUGHNESS_TABLES, config.RUTTING_TABLES, config.CRACKING_TABLES
    tables = {name: load_status_table(cfg.SILVER_STATUS_ROOT, name)
              for name in (rough["parent"], rut["parent"], crack["ca"])}
    for name in (rough["ca"], rough["child_10m"], rut["ca"], rut["child_10m"], crack["child_10m"]):
        tables[name] = load_silver(cfg.SILVER_INPUT_DIR, name, keep_upcs=cfg.KEEP_UPCS)
    tables = {name: keep_surveys(df, surveys, SILVER_SURVEY) for name, df in tables.items()}
    project = load_project_master(load_db_tables(cfg.DB_TABLES_DIR, ["master_upc_project"])["master_upc_project"])
    tables = filter_project_upcs(tables, project, cfg.FILTER_BY_PROJECT_MASTER)

    # --- 2. 10m rows + as_per_ca row + parent (roughness / rutting), as_per_ca row (cracking) ---
    child = lambda name: ca_only(tables[name], config.IRC_ONLY_THRESHOLD, name)
    rough_10m = line_defect_10m(tables[rough["parent"]], tables[rough["ca"]], child(rough["child_10m"]), "roughness")
    rut_10m = line_defect_10m(tables[rut["parent"]], tables[rut["ca"]], child(rut["child_10m"]), "rutting")
    crack_10m = cracking_10m(tables[crack["ca"]], child(crack["child_10m"]))
    fresh = as_text(build_intermediate_10m(rough_10m, rut_10m, crack_10m)
                    .rename(columns={"detected_on": "defect_detected_on"}))

    # --- 3. Into the newest saved intermediate_10m_data -> run date + latest ---
    base, _ = load_latest(cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME)
    if mode == INSERT:
        out = insert_surveys(base, fresh, TABLE_SURVEY, cfg.INTERMEDIATE_NAME)
    else:
        out = fresh if base is None else update_rows(base, fresh, INTERMEDIATE_KEY, INTERMEDIATE_UPDATE,
                                                     cfg.INTERMEDIATE_NAME)
    save_run(out, cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME, run_date)
    return out
