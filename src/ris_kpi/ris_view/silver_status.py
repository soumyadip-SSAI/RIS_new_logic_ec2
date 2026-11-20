"""RIS view step 1 — silver tables with status.

Each status table (RisView.STATUS_TABLES) is read from every UPC folder of RisView.SILVER_INPUT_DIR, stacked, given
defect_reference_id / current_status / final_status (ris_view/status.py) and saved under RisView.SILVER_STATUS_ROOT
(year=/month=/day=<run date> + latest, common/storage.py). ris_view/pipeline.py runs it in one of two modes
(ris_view/incremental.py):

  INSERT (new surveys)  silver rows of the new surveys, final_status 'Open', no DB lookup -> added to the newest saved
                        silver status table
  UPDATE (all surveys)  every silver row, status from udm_defect_reference / dlm_defect_case (RisView.DB_TABLES_DIR)
                        -> rows with a DB case update their status in the newest saved table; the others stay as they
                        are. No saved table yet -> the whole table is saved.

    ris-kpi ris-view --step silver-status        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.silver import load_silver
from ris_kpi.common.storage import load_latest, save_run
from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.incremental import (INSERT, SILVER_SURVEY, UPDATE, insert_surveys, keep_surveys,
                                          update_silver_status)
from ris_kpi.ris_view.status import add_open_status, add_status, case_status

log = logging.getLogger("silver_status")


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns {table: saved table}."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder
    run_date = run_time.date()

    # --- 1. Status tables (DB export), UPDATE only ---
    if mode == UPDATE:
        db = load_db_tables(cfg.DB_TABLES_DIR, cfg.STATUS_DB_TABLES)
        udm_defect_reference, cases = db["udm_defect_reference"], case_status(db["dlm_defect_case"])

    # --- 2. Silver tables (all UPCs) + status columns, into the newest saved table -> run date + latest ---
    outputs = {}
    for table, defect_collection in cfg.STATUS_TABLES.items():
        silver = keep_surveys(load_silver(cfg.SILVER_INPUT_DIR, table, keep_upcs=cfg.KEEP_UPCS), surveys,
                              SILVER_SURVEY)
        base, _ = load_latest(cfg.SILVER_STATUS_ROOT, table, empty_as_null=False)
        if mode == INSERT:
            out = insert_surveys(base, add_open_status(silver, table), SILVER_SURVEY, table)
        else:
            fresh = add_status(silver, defect_collection, udm_defect_reference, cases, silver_key=cfg.STATUS_KEY)
            out = fresh if base is None else update_silver_status(base, fresh, table)
        save_run(out, cfg.SILVER_STATUS_ROOT, table, run_date)
        outputs[table] = out
    return outputs
