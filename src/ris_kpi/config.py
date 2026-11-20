"""All settings for the RIS KPI pipelines (new silver logic), in one place, grouped by pipeline step.

Run settings are read from the .env file in the repository root (committed; run settings only, no credentials).
An environment variable of the same name wins over the .env value; without .env the defaults below apply:
  RIS_KPI_STORAGE     s3 (default): inputs from S3, every output to S3 — the EC2 runs
                      local: input_data/ and output_data/ in the repository — development without AWS access
  CONSIDER_UPC_LIST   True (default): only the surveys of UPC_LIST_FILE; False: every silver folder
  UPC_LIST_FILE       the survey list, relative to the repository root (default upc_to_run.csv) or absolute
  DROP_ROWS_WITHOUT_IDS  True (default): ris_view_data / ris_trends_data rows with an empty upc_id / survey_id /
                      survey_source_id are dropped (logged); False: they are kept with the empty ids (logged)

AWS credentials are not set here: boto3 finds them itself (the EC2 instance role, or `aws configure` locally).
"""
import os
from pathlib import Path

# src/ris_kpi/config.py -> repository root (RIS_KPI_ROOT overrides it, e.g. for a non-editable install)
ROOT_DIR = Path(os.getenv("RIS_KPI_ROOT") or Path(__file__).resolve().parents[2])
ENV_FILE = ROOT_DIR / ".env"


def load_env(path=ENV_FILE):
    """KEY=VALUE lines of the .env file into os.environ (variables already set win; # comments, quotes allowed).
    Returns True when the file exists."""
    path = Path(path)
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)
    return True


def env_bool(name, default):
    """True / False setting from the environment (.env): true/false, yes/no, 1/0 (any case)."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "y"):
        return True
    if value in ("0", "false", "no", "n"):
        return False
    raise ValueError(f"{name} must be True or False (in {ENV_FILE} or the environment), not {value!r}")


ENV_FILE_FOUND = load_env()

STORAGE = (os.getenv("RIS_KPI_STORAGE") or "s3").strip().lower()

if STORAGE == "s3":
    BUCKET = "s3://dhms-new-stgae"
    # Inputs
    # DB tables written by the DB export job: dlm_defect_case, udm_defect_reference (statuses), master_upc_project,
    # master_survey_system, survey_data_map, master_defect_type (UPC filter / lengths, DB id columns) — <table>.csv
    DB_TABLES_DIR = f"{BUCKET}/NSV/DHMS/DIC_Staging/Latest"
    # Silver tables (parquet / csv), one folder per survey, read for the run scope (upc_to_run.csv / every folder) and stacked (common/silver.py):
    #   <SILVER_INPUT_DIR>/Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/dlm_kpi_new/master_cracking_10m.parquet
    SILVER_INPUT_DIR = f"{BUCKET}/DHMS/NSV/Silver"
    # Outputs — all in the gold folder, each table saved as
    #   <GOLD_ROOT>/year=YYYY/month=MM/day=DD/<table>.csv  (run date)  +  <GOLD_ROOT>/Latest/<table>.csv
    # (common/storage.py); the newest saved copy is read back on the next run
    GOLD_ROOT = f"{BUCKET}/DHMS/NSV/Gold"
    SILVER_STATUS_ROOT = GOLD_ROOT       # silver tables (all UPCs stacked) + status columns
    INTERMEDIATE_ROOT = GOLD_ROOT        # intermediate_10m_data
    KPI_OUTPUT_ROOT = GOLD_ROOT          # ris_view_data, ris_trends_data
    LATEST_FOLDER = "Latest"
elif STORAGE == "local":
    INPUT_DATA_DIR = ROOT_DIR / "input_data"
    OUTPUT_DATA_DIR = ROOT_DIR / "output_data"
    # Inputs
    # <table>.csv, or the raw_db_tables layout <table>/<YYYY>/<MM>/<DD>/<table>.csv (newest date used)
    DB_TABLES_DIR = INPUT_DATA_DIR / "raw_db_tables"
    # Silver tables in any sub-folder (Zone=/Phase=/Cycle=/UPC=/...)
    SILVER_INPUT_DIR = INPUT_DATA_DIR / "silver_data"
    # Outputs (same layout as on S3: <root>/year=YYYY/month=MM/day=DD/ + <root>/latest/)
    SILVER_STATUS_ROOT = OUTPUT_DATA_DIR / "silver_data_with_status"
    INTERMEDIATE_ROOT = OUTPUT_DATA_DIR / "output_kpi_data"
    KPI_OUTPUT_ROOT = OUTPUT_DATA_DIR / "output_kpi_data"
    LATEST_FOLDER = "latest"
else:
    raise ValueError(f"RIS_KPI_STORAGE must be 's3' or 'local', not {STORAGE!r}")


# Which silver folders a run reads: <SILVER_INPUT_DIR>/Zone=<zone>/Phase=<phase>/Cycle=<cycle>/UPC=<upc>/...
# Set in .env (see the top of this file):
#   CONSIDER_UPC_LIST=True   only the surveys listed in UPC_LIST_FILE (one row per survey folder)
#   CONSIDER_UPC_LIST=False  every Zone / Phase / Cycle / UPC folder under SILVER_INPUT_DIR
# UPC_LIST_FILE: CSV with the columns upc, phase, cycle, zone (any order; upc in '/' or '_' form; an empty cell =
# every folder of that level). Rows of surveys not in the run are never changed in the gold tables.
# Command line overrides (common/silver.py, cli.py): --upc-list FILE, --all-upcs, --zone/--phase/--cycle/--upc
# (one survey).
CONSIDER_UPC_LIST = env_bool("CONSIDER_UPC_LIST", True)
UPC_LIST_FILE = ROOT_DIR / (os.getenv("UPC_LIST_FILE") or "upc_to_run.csv").strip()   # an absolute path stays as is
# ris_view_data and ris_trends_data: True = rows with an empty upc_id / survey_id / survey_source_id are dropped,
# False = kept with the empty ids (logged either way). Set in .env.
DROP_ROWS_WITHOUT_IDS = env_bool("DROP_ROWS_WITHOUT_IDS", True)
# Set at run time from the settings above (list of {"Zone", "Phase", "Cycle", "UPC"}); None = not resolved yet
SILVER_SCOPE = None


# Silver tables (file names without extension; the same name in every UPC folder)
ROUGHNESS_TABLES = {"parent": "master_roughness_parent", "ca": "master_roughness_as_per_ca",
                    "child_10m": "master_roughness_child_10m"}
RUTTING_TABLES = {"parent": "master_rutting_parent", "ca": "master_rutting_as_per_ca",
                  "child_10m": "master_rutting_child_10m"}
CRACKING_TABLES = {"ca": "master_cracking_as_per_ca", "child_10m": "master_cracking_10m"}
POTHOLE_TABLE = "master_pothole"
RAVELLING_TABLE = "master_ravelling"
# Rows used for the CA values ('As per CA'); 10m rows with this threshold_name only are dropped ('As per IRC')
CA_THRESHOLD_NAME = "As per CA"
IRC_ONLY_THRESHOLD = "As per IRC"
# Pavement type spellings unified when the silver tables are read
PAVEMENT_TYPE_ALIASES = {"Cement concrete": "Cement Concrete"}


class RisView:
    """RIS view (ris_kpi.ris_view): silver status -> intermediate_10m_data -> ris_view_data (+ DB ids).
    ris_view/pipeline.py picks the scenario from the surveys in latest/ris_view_data.csv:
    scenario 1 (some silver surveys are new): INSERT for the new surveys, then UPDATE for all;
    scenario 2 (no new survey): UPDATE for all (ris_view/incremental.py)."""
    # UPCs in '/' form (e.g. ["N/08009/04001/GJ"]). Empty = all UPCs.
    KEEP_UPCS = []

    # --- Input ---
    SILVER_INPUT_DIR = SILVER_INPUT_DIR
    # DB tables, read from DB_TABLES_DIR on every run (kept up to date there by the DB export job):
    # STATUS_DB_TABLES (silver-status step, UPDATE only) and master_upc_project / master_survey_system /
    # survey_data_map / master_defect_type (view step: UPC filter, UPC lengths, DB id columns)
    DB_TABLES_DIR = DB_TABLES_DIR
    STATUS_DB_TABLES = ["dlm_defect_case", "udm_defect_reference"]
    # True = keep only UPCs present in master_upc_project (intermediate + view steps)
    FILTER_BY_PROJECT_MASTER = True

    # --- Status ---
    # Silver tables that get defect_reference_id / current_status / final_status, and their
    # udm_defect_reference.defect_collection (ris_view/status.py)
    STATUS_TABLES = {
        ROUGHNESS_TABLES["parent"]: "roughness_parent",
        RUTTING_TABLES["parent"]: "rutting_parent",
        RAVELLING_TABLE: "ravelling_parent",
        POTHOLE_TABLE: "pothole_child_ca",
        CRACKING_TABLES["ca"]: "cracking_child_ca",
    }
    # Silver column matched to udm_defect_reference.defect_id (every status table)
    STATUS_KEY = "id"

    # --- ris_view_data ---
    # True = rows with an empty upc_id / survey_id / survey_source_id are dropped (logged); from .env
    DROP_ROWS_WITHOUT_IDS = DROP_ROWS_WITHOUT_IDS

    # --- Output (file names without .csv) ---
    # Every output table is saved twice (common/storage.py):
    #   <root>/year=YYYY/month=MM/day=DD/<name>.csv  one folder per run date (a second run on the same day overwrites)
    #   <root>/latest/<name>.csv                     the newest run, overwritten by every run
    # Each step reads the newest dated copy of its table, inserts / updates rows (ris_view/incremental.py) and saves
    # it under the run date.
    SILVER_STATUS_ROOT = SILVER_STATUS_ROOT          # <status table>.csv: all UPCs stacked + status columns
    INTERMEDIATE_ROOT = INTERMEDIATE_ROOT
    INTERMEDIATE_NAME = "intermediate_10m_data"
    KPI_OUTPUT_ROOT = KPI_OUTPUT_ROOT
    RIS_VIEW_DATA_NAME = "ris_view_data"


class RisTrends:
    """RIS trends (ris_kpi.ris_trends): ris_trends_data (+ DB ids), new surveys only (append). Does not use the status
    tables."""
    # UPCs in '/' form. Empty = all UPCs.
    KEEP_UPCS = []
    KEEP_PAVEMENT_TYPES = ["Asphalt", "Cement Concrete", "Hybrid"]

    # --- Input ---
    SILVER_INPUT_DIR = SILVER_INPUT_DIR
    # Roughness / rutting: one row per carriageway (LHS / RHS) per km, 'As per CA'; the threshold is the row's
    # threshold_value (no dlm_threshold_mapping)
    ROUGHNESS_TABLE = ROUGHNESS_TABLES["parent"]
    RUTTING_TABLE = RUTTING_TABLES["parent"]
    # pci_value = mean pci of this table (master_roughness_parent and master_rutting_parent carry the same pci)
    PCI_TABLE = ROUGHNESS_TABLES["parent"]
    # True = keep only UPCs present in master_upc_project
    FILTER_BY_PROJECT_MASTER = True
    # DB tables: master_upc_project (UPC filter + upc_id), master_survey_system / survey_data_map (DB id columns)
    DB_TABLES_DIR = DB_TABLES_DIR
    # True = rows with an empty upc_id / survey_id / survey_source_id are dropped (logged); from .env
    DROP_ROWS_WITHOUT_IDS = DROP_ROWS_WITHOUT_IDS

    # --- Output ---
    # ris_trends_data, saved as OUTPUT_ROOT/year=YYYY/month=MM/day=DD/ris_trends_data.csv (run date) + latest/.
    # Also an input: only silver surveys (upc + phase + cycle + detected_on) not in the newest saved copy are
    # calculated and appended (ris_trends/append.py). Delete the saved copies (dated folders + latest) to rebuild it.
    OUTPUT_ROOT = KPI_OUTPUT_ROOT
    RIS_TRENDS_OUTPUT_NAME = "ris_trends_data"
