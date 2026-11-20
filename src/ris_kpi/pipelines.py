"""The full KPI run, called in-process (from the CLI or from an orchestrator such as Airflow).

    run_full()   ris_view -> ris_trends

Both read the DB tables straight from S3 (DB_TABLES_DIR, written there by the DB export job); ris_trends needs them
only for the id columns, not the defect statuses. When only defect statuses changed, run the RIS view alone
(ris_kpi.ris_view.run_ris_view / `ris-kpi status-update`).

Outputs are saved under <root>/year=YYYY/month=MM/day=DD/ for the run date and copied to <root>/latest/; one run time
is used for both steps (run date folder, created_at / updated_at).
"""
import logging
import time
from datetime import datetime

from ris_kpi.ris_trends import run_ris_trends
from ris_kpi.ris_view import run_ris_view

log = logging.getLogger(__name__)

# name -> (function, steps it depends on); run in this order
STEPS = {
    # S3 DB tables + silver (all UPC folders) -> silver status -> intermediate_10m_data -> ris_view_data
    # (scenario 1: insert new surveys, then update all | scenario 2: update all)
    "ris_view": (run_ris_view, []),
    # silver (master_roughness_parent / master_rutting_parent) + S3 DB tables (ids) -> ris_trends_data (new surveys)
    "ris_trends": (run_ris_trends, []),
}


def run_full(only=None, keep_going=False, run_time=None):
    """Run the steps in order. Returns {step: (status, seconds, error)} with status OK / FAILED / SKIPPED.

    only:        run only these steps; a dependency that is not selected counts as done (its earlier output is used)
    keep_going:  after a failure, still run the steps that do not depend on the failed one
    run_time:    time of the run (default: now)
    """
    run_time = run_time or datetime.now()
    selected = [name for name in STEPS if not only or name in only]
    results = {}

    for name in selected:
        func, deps = STEPS[name]
        failed_deps = [d for d in deps if results.get(d, ("OK",))[0] != "OK"]
        stopped = any(r[0] == "FAILED" for r in results.values()) and not keep_going
        if stopped or failed_deps:
            reason = f"{', '.join(failed_deps)} did not succeed" if failed_deps else "stopped after failure"
            results[name] = ("SKIPPED", 0.0, reason)
            continue
        log.info("%s %s %s", "=" * 25, name, "=" * 25)
        start = time.time()
        try:
            func(run_time=run_time)
            results[name] = ("OK", time.time() - start, "")
        except Exception as e:
            log.exception("%s FAILED", name)
            results[name] = ("FAILED", time.time() - start, f"{type(e).__name__}: {e}")

    log.info("%s summary %s", "=" * 25, "=" * 25)
    for name, (status, seconds, error) in results.items():
        log.info("%-12s %-8s %6.1fs%s", name, status, seconds, f"  -> {error}" if error else "")
    return results
