"""Command line entry point: `ris-kpi <command>` (or `python -m ris_kpi <command>`).

    ris-kpi run                                  # ris_view -> ris_trends (full KPI creation)
    ris-kpi run --keep-going                     # still run ris_trends when ris_view fails
    ris-kpi status-update                        # only defect statuses changed: RIS view (scenario 2 / 1)
    ris-kpi ris-view [--step silver-status intermediate view]   # RIS view; --step: those steps only (UPDATE)
    ris-kpi ris-trends
    ris-kpi sync-db-ids [--dry-run]              # after a run: id / created_at from the DB KPI tables

Silver scope (run, status-update, ris-view, ris-trends):
    default            config.CONSIDER_UPC_LIST True: the surveys of upc_to_run.csv; False: every folder
    --upc-list FILE    the surveys of another CSV (upc, phase, cycle, zone)
    --all-upcs         every Zone / Phase / Cycle / UPC folder
    --zone B --phase 1 --cycle 1 --upc N/01002/01001/GJ    one survey (a level not given = every folder)

Exit code 1 when anything fails.
"""
import argparse
import logging
import sys

from ris_kpi.common.logging import setup_logging

log = logging.getLogger("ris_kpi")


SCOPE_ARGS = {"zone": "Zone", "phase": "Phase", "cycle": "Cycle", "upc": "UPC"}


def _scope_parser():
    """Options choosing the silver folders (default: config.CONSIDER_UPC_LIST / config.UPC_LIST_FILE)."""
    scope = argparse.ArgumentParser(add_help=False)
    group = scope.add_argument_group("silver scope (default: upc_to_run.csv when config.CONSIDER_UPC_LIST, "
                                     "else every folder)")
    group.add_argument("--upc-list", metavar="CSV", help="surveys of this CSV (upc, phase, cycle, zone)")
    group.add_argument("--all-upcs", action="store_true", help="every Zone / Phase / Cycle / UPC folder")
    for arg, key in SCOPE_ARGS.items():
        group.add_argument(f"--{arg}", help=f"one survey: {key}= folder (a level not given = every folder)")
    return scope


def apply_scope(args):
    """Scope options -> config.SILVER_SCOPE (list of surveys), logged. Precedence: --zone/--phase/--cycle/--upc,
    --all-upcs, --upc-list, then config.CONSIDER_UPC_LIST (upc_to_run.csv) / every folder."""
    from ris_kpi import config
    from ris_kpi.common.silver import ALL_SCOPE, describe, make_scope, read_upc_list, run_scopes
    one = {key: getattr(args, arg) for arg, key in SCOPE_ARGS.items() if getattr(args, arg, None) is not None}
    if one:
        config.SILVER_SCOPE, source = [make_scope(one)], "command line"
    elif getattr(args, "all_upcs", False):
        config.SILVER_SCOPE, source = [dict(ALL_SCOPE)], "--all-upcs"
    elif getattr(args, "upc_list", None):
        config.SILVER_SCOPE, source = read_upc_list(args.upc_list), args.upc_list
    else:
        config.SILVER_SCOPE = None
        source = str(config.UPC_LIST_FILE) if config.CONSIDER_UPC_LIST else "CONSIDER_UPC_LIST off: every folder"
    log.info("Settings: %s | RIS_KPI_STORAGE=%s | CONSIDER_UPC_LIST=%s | UPC_LIST_FILE=%s | DROP_ROWS_WITHOUT_IDS=%s"
             " | SURVEY_ID_FALLBACK=%s",
             config.ENV_FILE if config.ENV_FILE_FOUND else f"no {config.ENV_FILE} (defaults)", config.STORAGE,
             config.CONSIDER_UPC_LIST, config.UPC_LIST_FILE, config.DROP_ROWS_WITHOUT_IDS, config.SURVEY_ID_FALLBACK)
    log.info("Paths: DB tables %s | silver %s | silver status -> %s | intermediate -> %s | KPI -> %s (+ /%s)",
             config.DB_TABLES_DIR, config.SILVER_INPUT_DIR, config.SILVER_STATUS_ROOT, config.INTERMEDIATE_ROOT,
             config.KPI_OUTPUT_ROOT, config.LATEST_FOLDER)
    scopes = run_scopes()
    log.info("Silver scope (%s): %s survey folder(s) under %s", source, len(scopes), config.SILVER_INPUT_DIR)
    for scope in scopes:
        log.info("    %s", describe(scope))


def _build_parser():
    from ris_kpi import pipelines, ris_view

    parser = argparse.ArgumentParser(prog="ris-kpi", description="RIS KPI pipelines (ris_view_data, ris_trends_data).")
    sub = parser.add_subparsers(dest="command", required=True)
    scope = [_scope_parser()]

    run = sub.add_parser("run", parents=scope, help="full KPI creation: ris_view -> ris_trends")
    run.add_argument("--only", nargs="+", choices=list(pipelines.STEPS), help="run only these steps")
    run.add_argument("--keep-going", action="store_true", help="continue with independent steps after a failure")

    sub.add_parser("status-update", parents=scope, help="defect statuses changed in the DB: refresh ris_view_data (same as ris-view)")

    rv = sub.add_parser("ris-view", parents=scope, help="run the RIS view (scenario 1 / 2, reads the latest defect statuses from S3)")
    rv.add_argument("--step", nargs="+", choices=list(ris_view.STEPS),
                    help="run only these steps, in UPDATE mode for all surveys (default: the full scenario run)")

    sub.add_parser("ris-trends", parents=scope, help="run the RIS trends KPI")

    sync = sub.add_parser("sync-db-ids", help="copy id / created_at of ris_view_kpi / ris_trends_kpi (DB export) "
                                              "into the saved ris_view_data / ris_trends_data")
    sync.add_argument("--dry-run", action="store_true", help="report only, nothing saved")
    sync.add_argument("--only", nargs="+", choices=["ris_view", "ris_trends"], help="these tables only")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    setup_logging()

    from ris_kpi import pipelines
    from ris_kpi.ris_trends import run_ris_trends
    from ris_kpi.ris_view import run_ris_view

    try:
        if args.command != "sync-db-ids":
            apply_scope(args)
        if args.command == "run":
            results = pipelines.run_full(only=args.only, keep_going=args.keep_going)
            ok = all(status == "OK" for status, _, _ in results.values())
        elif args.command in ("ris-view", "status-update"):
            run_ris_view(getattr(args, "step", None))
            ok = True
        elif args.command == "ris-trends":
            run_ris_trends()
            ok = True
        elif args.command == "sync-db-ids":
            from ris_kpi.tools.db_ids_sync import run_sync
            ok = run_sync(only=args.only, dry_run=args.dry_run)
    except Exception:
        log.exception("%s FAILED", args.command)
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
