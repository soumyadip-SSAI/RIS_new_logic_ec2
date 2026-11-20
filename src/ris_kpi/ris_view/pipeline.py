"""RIS view driver: picks the scenario, then runs the three steps (silver status -> intermediate_10m_data ->
ris_view_data). All paths and settings are in config.RisView.

A survey is one upc + phase + cycle + detected_on. The surveys of the silver tables (RisView.SILVER_INPUT_DIR) are
compared with the surveys in <RisView.KPI_OUTPUT_ROOT>/latest/ris_view_data.csv:

  Scenario 1 — some silver surveys are not in ris_view_data (new; all of them when there is no ris_view_data):
      a) INSERT for the new surveys only, each step reading the newest saved table and saving it under the run date:
         1. silver status 'Open' (no DB lookup)   -> rows added to the silver status tables
         2. intermediate_10m_data of the new surveys -> rows added
         3. ris_view_data of the new surveys      -> rows added (created_at = updated_at = this run)
      b) then UPDATE for all surveys (as scenario 2; the newest saved tables are now the ones just written)
  Scenario 2 — every silver survey is already in ris_view_data:
      UPDATE for all surveys:
         1. silver status from the DB tables      -> status of the rows with a DB case updated
         2. intermediate_10m_data                 -> statuses updated
         3. ris_view_data                         -> counts / affected lengths updated (other columns unchanged)

Scenario 2 is also the run after a defect status change (cases closed / assigned in the DB): `ris-kpi ris-view`.
Outputs: <root>/year=YYYY/month=MM/day=DD/<table>.csv for the run date, copied to <root>/latest/ (common/storage.py).
A failing step raises; the steps after it are not run.
"""
import logging
import time
from datetime import datetime

from ris_kpi.common.io import exists
from ris_kpi.common.silver import load_silver
from ris_kpi.common.storage import latest_location, read_text
from ris_kpi.config import RisView
from ris_kpi.ris_view import intermediate, silver_status, view
from ris_kpi.ris_view.incremental import INSERT, SILVER_SURVEY, TABLE_SURVEY, UPDATE, surveys_of

log = logging.getLogger(__name__)

# CLI name -> step function, in run order
STEPS = {
    "silver-status": silver_status.run,
    "intermediate": intermediate.run,
    "view": view.run,
}


def ris_view_data_location():
    """<RisView.KPI_OUTPUT_ROOT>/latest/ris_view_data.csv: its surveys are existing, any other silver survey is new."""
    return latest_location(RisView.KPI_OUTPUT_ROOT, RisView.RIS_VIEW_DATA_NAME)


def silver_surveys():
    """Surveys in the silver defect tables (after KEEP_UPCS)."""
    surveys = set()
    for table in RisView.STATUS_TABLES:
        keys = load_silver(RisView.SILVER_INPUT_DIR, table, columns=SILVER_SURVEY, keep_upcs=RisView.KEEP_UPCS)
        surveys |= surveys_of(keys, SILVER_SURVEY)
    return surveys


def saved_surveys():
    """Surveys in latest/ris_view_data.csv (empty when there is none)."""
    location = ris_view_data_location()
    if not exists(location):
        log.info("No %s — every silver survey is new", location)
        return set()
    return surveys_of(read_text(location), TABLE_SURVEY)


def run_steps(mode, surveys, run_time, label, steps=None):
    selected = [name for name in STEPS if not steps or name in steps]
    for i, name in enumerate(selected, 1):
        step_start = time.time()
        log.info("%s — step %d/%d: %s (%s)", label, i, len(selected), name, mode)
        STEPS[name](mode=mode, surveys=surveys, run_time=run_time)
        log.info("%s — step %d/%d: %s done in %.1fs", label, i, len(selected), name, time.time() - step_start)


def run_ris_view(steps=None, run_time=None):
    """Full RIS view (scenario 1 or 2); returns the scenario number.

    steps: run only these steps, in UPDATE mode for all surveys (no scenario check; like running one step alone).
    run_time: one time for the whole run — run date folder, created_at of inserted rows, updated_at of changed rows
    (so rows inserted in 1a and updated in 1b keep created_at = updated_at)."""
    run_time = run_time or datetime.now()
    start = time.time()
    if steps:
        run_steps(UPDATE, None, run_time, "RIS view (selected steps)", steps)
        scenario = None
    else:
        silver, saved = silver_surveys(), saved_surveys()
        new = silver - saved
        log.info("Surveys: %s in silver, %s in ris_view_data, %s new", len(silver), len(saved), len(new))
        if new:
            log.info("Scenario 1 — new surveys:\n  %s", "\n  ".join(sorted(new)))
            run_steps(INSERT, new, run_time, "Scenario 1a (insert new surveys)")
            run_steps(UPDATE, None, run_time, "Scenario 1b (update all surveys)")
            scenario = 1
        else:
            log.info("Scenario 2 — no new survey: update all surveys")
            run_steps(UPDATE, None, run_time, "Scenario 2 (update all surveys)")
            scenario = 2
    log.info("RIS view done (scenario %s) in %.1fs — %s", scenario or "-", time.time() - start,
             ris_view_data_location())
    return scenario
