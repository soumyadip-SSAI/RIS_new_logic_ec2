"""All settings for the RIS KPI pipelines (new silver logic), in one place, grouped by pipeline step.

Where the data lives is chosen with the RIS_KPI_STORAGE environment variable:
  s3     (default) read the inputs from S3 and write every output to S3 — the EC2 runs
  local  input_data/ and output_data/ in the repository — development / before-after checks without AWS access

AWS credentials are not set here: boto3 finds them itself (the EC2 instance role, or `aws configure` locally).
"""
import os
from pathlib import Path

STORAGE = (os.getenv("RIS_KPI_STORAGE") or "s3").strip().lower()

if STORAGE == "s3":
    BUCKET = "s3://dhms-new-stgae"
    # Inputs
    # DB tables written by the DB export job: dlm_defect_case, udm_defect_reference (statuses), master_upc_project,
    # master_survey_system, survey_data_map, master_defect_type (UPC filter / lengths, DB id columns) — <table>.csv
    DB_TABLES_DIR = f"{BUCKET}/NSV/DHMS/DIC_Staging/Latest"
    # Silver tables (parquet / csv), one folder per survey, read for SILVER_SCOPE and stacked (common/silver.py):
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
    # src/ris_kpi/config.py -> repository root (RIS_KPI_ROOT overrides it, e.g. for a non-editable install)
    ROOT_DIR = Path(os.getenv("RIS_KPI_ROOT") or Path(__file__).resolve().parents[2])
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


# Which silver folders a run reads: <SILVER_INPUT_DIR>/Zone=<Zone>/Phase=<Phase>/Cycle=<Cycle>/UPC=<UPC>/...
# An empty value ("") = every folder of that level, e.g. {"Zone": "", "Phase": "", "Cycle": "", "UPC": ""} = all UPCs.
# UPC in '/' or '_' form. Overridden on the command line: ris-kpi run --zone B --phase 1 --cycle 1 --upc N/01002/01001/GJ
# (--all-upcs = every folder). Rows of other surveys already in the gold tables are never changed by a scoped run.
SILVER_SCOPE = {"Zone": "B", "Phase": "1", "Cycle": "1", "UPC": "N/01002/01001/GJ"}


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
    # True = rows with an empty upc_id / survey_id / survey_source_id are dropped (logged)
    DROP_ROWS_WITHOUT_IDS = True

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

    # --- Output ---
    # ris_trends_data, saved as OUTPUT_ROOT/year=YYYY/month=MM/day=DD/ris_trends_data.csv (run date) + latest/.
    # Also an input: only silver surveys (upc + phase + cycle + detected_on) not in the newest saved copy are
    # calculated and appended (ris_trends/append.py). Delete the saved copies (dated folders + latest) to rebuild it.
    OUTPUT_ROOT = KPI_OUTPUT_ROOT
    RIS_TRENDS_OUTPUT_NAME = "ris_trends_data"
