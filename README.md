# RIS KPI (new silver logic)

Builds the RIS KPI tables `ris_view_data` and `ris_trends_data` from the **new silver tables** and the DHMS database
tables (defect statuses, UPC / survey / defect type masters). Every input is read from S3 and every output is written
to S3; nothing is kept on the machine that runs it.

It has the same structure, output folders and incremental runs as `ris_kpi_golden_modular_dic`. The KPI logic comes
from `RIS_View_new_logic_modular` / `RIS_Trends_new_logic_modular`:

- **Silver input:** one folder per UPC (`Zone=/Phase=/Cycle=/UPC=/`), read and stacked.
- **Intermediate table:** the old `final_intermediate` is replaced by `intermediate_10m_data`, which needs no
  `master_chainage` and no reporting intervals.
- **No IRC values:**
  - `ris_view_data` has no IRC columns;
  - `ris_trends_data` keeps the IRC columns, with only the category filled (Good / Poor / Fair).
- **Trends thresholds:** taken from each silver row's `threshold_value`, so no `dlm_threshold_mapping` is needed.

| Command | Use it when | Writes |
|---|---|---|
| `ris-kpi run` | Full KPI creation (new survey data / silver tables) | everything below |
| `ris-kpi status-update` | Only defect statuses changed in the DB (cases closed, assigned, ...) | silver with status, `intermediate_10m_data`, `ris_view_data` |
| `ris-kpi sync-db-ids` | Manually, after every run: take `id` / `created_at` of rows already in the DB | `ris_view_data.csv`, `ris_trends_data.csv` in `Latest/` and in the newest `year=/month=/day=` folder |

The tables are **updated, not rebuilt**: each run reads the newest saved copy of every table first and inserts /
updates rows in it (see [New and existing surveys](#new-and-existing-surveys-scenario-1--2)).

## Where the data lives (`s3://dhms-new-stgae/...`)

| | S3 location | Contents |
|---|---|---|
| in | `NSV/DHMS/DIC_Staging/Latest/` | DB tables from the DB export job: `dlm_defect_case`, `udm_defect_reference` (statuses), `master_upc_project`, `master_survey_system`, `survey_data_map`, `master_defect_type` (UPC filter, UPC lengths, DB id columns) |
| in | `DHMS/NSV/Silver/` | silver tables (parquet), one folder per survey: `Zone=/Phase=/Cycle=/UPC=/dlm_kpi_new/` (see below) |
| in + out | `DHMS/NSV/Gold/` (`Latest/` and `year=YYYY/month=MM/day=DD/`) | every output table: the 5 silver status tables (all UPCs stacked + `defect_reference_id`, `current_status`, `final_status`), `intermediate_10m_data.csv`, `ris_view_data.csv`, `ris_trends_data.csv` |

Silver layout: every file named `<table>.csv`, `.parquet` or `.xlsx` in the scope's folders belongs to that table, and all of them are stacked (`common/silver.py`):

```
DHMS/NSV/Silver/Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ/dlm_kpi_new/master_cracking_10m.parquet
```

**Run scope.** Which `Zone=` / `Phase=` / `Cycle=` / `UPC=` folders a run reads:
Set in **`.env`** in the repository root, as shown in the settings section below.
- **`CONSIDER_UPC_LIST=True`** (default): only the surveys listed in **`upc_to_run.csv`** in the repository root, or in the file named by `UPC_LIST_FILE`.
  - Columns: `upc`, `phase`, `cycle`, `zone`, in any order, one row per survey folder.
  - UPCs can be in `/` or `_` form.
  - An empty cell means every folder of that level.
- **`CONSIDER_UPC_LIST=False`:** every Zone / Phase / Cycle / UPC folder under `Silver/`.
- **Command line overrides:**
  - `--upc-list other.csv` reads another list.
  - `--all-upcs` reads every folder.
  - `--zone/--phase/--cycle/--upc` picks one survey; a level that isn't given means every folder.
- **Log:** the first lines list the survey folders of the run.
- **Missing files:** a listed survey with no file for a table is a warning in the log. A table with no file at all is an error.
- **Listing:** only the folders fixed by the listed surveys are listed on S3.
- **Other surveys:** a run reads and changes only the listed surveys' rows in the gold tables. Rows of other surveys saved there stay as they are.

Tables used:
- **Line defects:** `master_roughness_{parent,as_per_ca,child_10m}`, `master_rutting_{parent,as_per_ca,child_10m}`.
- **Cracking:** `master_cracking_{as_per_ca,10m}`.
- **Point defects:** `master_pothole`, `master_ravelling`.

**Reading the silver data:**
- **Text values:** every column is read as text.
- **Key columns:** upc is converted to the `/` form, phase and cycle to `2` (not `2.0`), and `detected_on` to yyyy-mm-dd. `Cement concrete` becomes `Cement Concrete`.
- **Columns missing in some UPC files:** they stay empty, and the log names the file.

**Permissions.** AWS access comes from boto3's usual chain: the EC2 instance role, or `aws configure` locally. No credentials are stored in the repo. The role needs:
- **`DHMS/NSV/Silver/`:** `s3:GetObject` and **`s3:ListBucket`**, to find the UPC folders.
- **`DHMS/NSV/Gold/`:** `s3:GetObject`, `s3:PutObject` and `s3:ListBucket`.
  - Without ListBucket here, the `Latest/` copy is read instead of the newest dated copy. That still works, because every run writes both.
- **`NSV/DHMS/DIC_Staging/Latest/`:** `s3:GetObject`.

> If the role cannot read the gold folder, S3 answers as if nothing was saved, so the run treats every survey as new.
> After the first run on a new machine or role, check the log line `Surveys: ... in silver, ... in ris_view_data, ... new`.

### Output folders

```
s3://dhms-new-stgae/DHMS/NSV/Gold/
├── year=2026/month=11/day=20/<table>.csv   # one folder per run date (a second run on the same day overwrites it)
└── Latest/<table>.csv                       # copy of the newest run, overwritten by every run
```

- **Reading:** a step reads the **newest saved** copy of its table. That is the latest `year=/month=/day=` folder that has the file, or `Latest/` when there is no dated copy.
- **Saving:** the step saves the changed table under the run date and in `Latest/`.
- **Run time:** one run time is used for the whole run: the run-date folder, `created_at` and `updated_at`.

## Running on the EC2 instance (SSM console)

Needs Python 3.10 or newer.

```bash
sudo su
cd ~
git clone <repo-url> ris_kpi_new_logic          # first time only
cd ~/ris_kpi_new_logic
git pull
python3 -m venv .venv                           # first time only
source .venv/bin/activate
pip install -e .
nano .env                                       # settings: CONSIDER_UPC_LIST, UPC_LIST_FILE, ...
nohup ris-kpi run > run_$(date +%F_%H%M).log 2>&1 &   # surveys of upc_to_run.csv (CONSIDER_UPC_LIST)
tail -f run_*.log                               # Ctrl+C stops watching, not the run
```

The run ends with a summary (`OK` / `FAILED` / `SKIPPED` per step); exit code 1 means something failed.

## Settings (`.env`)

Run settings live in `.env` in the repository root.
- **Committed:** it holds the run settings and the `db_push` DB keys (`DB_HOST`, `DB_PASSWORD`, …). The DB keys are committed **empty**: fill them in on the server only and run `git update-index --skip-worktree .env` so they are never committed (see `db_push/README.md`).
- **Editing:** use `nano .env` on the server. No reinstall is needed.
- **Server edits and `git pull`:** if the repository's `.env` changed too, pull stops. Run `git checkout -- .env` to drop the local edit, or `git stash` then `git pull` then `git stash pop` to keep it.
- **Defaults:** without `.env`, the defaults below apply.
- **Environment variables:** one with the same name, such as `CONSIDER_UPC_LIST=False ris-kpi run`, wins over the `.env` value for that run.

| Setting | Default | Meaning |
|---|---|---|
| `RIS_KPI_STORAGE` | `s3` | `s3`: read and write S3 (EC2). `local`: `input_data/` and `output_data/` in the repository |
| `CONSIDER_UPC_LIST` | `True` | `True`: only the surveys in `UPC_LIST_FILE`. `False`: every silver folder |
| `UPC_LIST_FILE` | `upc_to_run.csv` | survey list (`upc`, `phase`, `cycle`, `zone`), relative to the repository root or absolute |
| `DROP_ROWS_WITHOUT_IDS` | `True` | `ris_view_data` and `ris_trends_data` rows with an empty `upc_id`, `survey_id` or `survey_source_id`. `True`: dropped. `False`: kept with the empty ids. Logged either way |
| `S3_DB_TABLES_DIR` | the current DB export folder | input: DB tables (`dlm_defect_case`, `udm_defect_reference`, `master_upc_project`, ...) |
| `S3_SILVER_INPUT_DIR` | `s3://dhms-new-stgae/DHMS/NSV/Silver` | input: silver tables, `Zone=/Phase=/Cycle=/UPC=` folders |
| `S3_GOLD_OUTPUT_DIR` | the current gold folder | output: every table in `year=/month=/day=/` and `Latest/`; the newest copy is read back on the next run, so a new folder means a fresh start. `db_push` reads `<S3_GOLD_OUTPUT_DIR>/Latest/` |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SCHEMA` | empty, `5432`, `public` | `db_push` credentials, filled in on the server only (see `db_push/README.md`) |

The first log line shows the settings used, for example `Settings: .../.env | RIS_KPI_STORAGE=s3 | CONSIDER_UPC_LIST=True | ...`.

## Commands

```bash
ris-kpi run                      # ris_view -> ris_trends, for upc_to_run.csv (or every folder: CONSIDER_UPC_LIST=False in .env)
ris-kpi run --upc-list other.csv # the surveys of another CSV (upc, phase, cycle, zone)
ris-kpi run --all-upcs           # every Zone / Phase / Cycle / UPC folder
ris-kpi run --zone B --phase 1 --cycle 1 --upc N/01002/01001/GJ   # one survey folder
ris-kpi run --keep-going         # still run ris_trends when ris_view fails
ris-kpi status-update            # only defect statuses changed: the RIS view (scenario 2, or 1 when a survey is new)
ris-kpi ris-view --step view     # single steps (silver-status, intermediate, view), UPDATE mode for all surveys
ris-kpi ris-trends
ris-kpi sync-db-ids --dry-run    # after a run: report which rows get the DB id / created_at
ris-kpi sync-db-ids              # copy them and save
```

## Steps

| Step | Reads | Writes (gold folder) |
|---|---|---|
| `ris_view` 1. `silver-status` | the 5 status tables (silver), newest saved silver status, `dlm_defect_case` / `udm_defect_reference` (UPDATE only) | `master_roughness_parent`, `master_rutting_parent`, `master_ravelling`, `master_pothole`, `master_cracking_as_per_ca` (+ status columns) |
| `ris_view` 2. `intermediate` | silver `*_as_per_ca` / `*_child_10m` / `master_cracking_10m`, newest saved silver status (parent breach flags, statuses), `master_upc_project` | `intermediate_10m_data.csv` |
| `ris_view` 3. `view` | newest saved silver status (counts), newest saved `intermediate_10m_data` (lengths), `master_upc_project`, `master_survey_system`, `survey_data_map`, `master_defect_type`, newest saved `ris_view_data` | `ris_view_data.csv` |
| `ris_trends` | silver `master_roughness_parent` / `master_rutting_parent`, `master_upc_project`, `master_survey_system`, `survey_data_map`, newest saved `ris_trends_data` | `ris_trends_data.csv` (rows of new surveys appended) |

### 1. Silver status (`ris_view/status.py`)
- **Lookup:**
  1. Filter `udm_defect_reference` to the table's `defect_collection` (`RisView.STATUS_TABLES`).
  2. Match the silver **`display_defect_id`** (`RisView.STATUS_KEY`) to `udm_defect_reference.defect_id`, within the table's `defect_collection`. Keys are compared as trimmed text (`123.0` = `123`). The matched row's `id` becomes `defect_reference_id`. The silver `id` is kept as it is and still links the 10m, CA and parent rows.
  3. Match `defect_reference_id` to `dlm_defect_case.defect_reference_id`, which gives `current_status`.
- **`final_status`:** `Closed` for CLOSED / CANCELLED, else `Open`, including when there is no case.
- **Duplicate keys** on the DB side stop the run (`ValueError`).

### 2. `intermediate_10m_data` (`ris_view/intermediate_10m.py`)
- **Grain:** one row per survey + lane + 10 m segment.
- **Which 10 m rows:** `As per IRC` rows are dropped; `As per CA` and `As per CA & IRC` rows are kept.
- **Roughness / rutting:**
  - Each as_per_ca row gets its parent's breach flag (`parent_id`).
  - Each 10 m row gets the as_per_ca row it lies in (`merge_asof`, 10 m end ≤ CA end, by survey + parent_id + lane).
  - Its status is the parent's `final_status`.
- **Cracking:** each 10 m row gets its as_per_ca row (by survey + lane), and that row's status.
- **Flags:**
  - `<defect>_final_flag` = parent breach AND CA breach (roughness / rutting), or CA breach (cracking);
  - `final_flag` = any of the three.
- **`total_status`:** `Closed` when every flagged defect on the row is Closed, else `Open`.
- **`length_m`:** `end_chainage - start_chainage`.

### 3. `ris_view_data` (`ris_view/view_table.py`, `db_ids.py`)
One row per survey, defect type (plus `Total`), pavement group and status (`open` / `close`); every grain has both statuses.

| defect_type | `total_defects_ca`: breached rows (`breach_flag = 1`) of | `affected_length_ca_m`: sum of `length_m` where |
|---|---|---|
| Roughness | `master_roughness_parent` | `roughness_final_flag = 1`, by `roughness_status` |
| Rutting | `master_rutting_parent` | `rutting_final_flag = 1`, by `rutting_status` |
| Cracking | `master_cracking_as_per_ca` | `cracking_final_flag = 1`, by `cracking_status` |
| Pothole | `master_pothole` | 0 |
| Ravelling | `master_ravelling` | 0 |
| Total | sum of the above (same status) | `final_flag = 1`, by `total_status` |

- **Pavement type:** Cracking and Pothole are split into `Cement Concrete` / `Asphalt` (anything else counts as Asphalt). Other defect types and Total have an empty `pavement_type`.
  - For the counts, it comes from the counted silver row.
  - For cracking lengths, it comes from `intermediate_10m_data.pavement_type`.
- **`affected_length_ca_km`** = `affected_length_ca_m / 1000`.
- **UPC lengths:** `upc_length_km` / `upc_lane_length_km` come from `master_upc_project.length_km` / `lane_length_km`.
- **DB ids:**

  | Column | DB table | Join |
  |---|---|---|
  | `upc_id` | `master_upc_project` | upc → `id` |
  | `survey_source_id` | `master_survey_system` | source_system (`NSV`) = `system_code` → `id` |
  | `survey_id` | `survey_data_map` | upc + phase + cycle + defect_detected_on = `survey_date` → `survey_schedule_id` |
  | `defect_type_id` | `master_defect_type` | survey_system + asset_type + defect_name (as before; Rutting `PAVEMENT`, else `Flexible Pavement`; `Total`: none) |
  | `id` | — | a random uuid4 per row, set on INSERT and kept by UPDATE runs |

  Rows with an empty `upc_id`, `survey_id` or `survey_source_id` are **dropped** and logged (`DROP_ROWS_WITHOUT_IDS=True` in `.env`; `False` keeps them with the empty ids, for ris_view_data and ris_trends_data).
- **Columns:**
  `id, survey_source_id, upc, upc_id, phase, cycle, defect_detected_on, survey_id, defect_type, defect_type_id, pavement_type, status, upc_length_km, upc_lane_length_km, total_defects_ca, affected_length_ca_m, affected_length_ca_km, created_at, updated_at`.

### `ris_trends_data` (`ris_trends/`)
- **Grain:** upc, source_system, detected_on, phase, cycle, zone, pavement_type. Each grain gives 3 rows.
- **Averages:** `roughness_average_mm_km` / `average_rutting_depth_mm` are the mean `metric_value` of the `As per CA` rows, rounded half-up to 4 decimals.
- **`pci_value`:** the mean `pci` (`RisTrends.PCI_TABLE`) per upc, phase, cycle, pavement_type and detected_on.
- **CA columns, one row each:**

  | Row | `<defect>_ca_category` | `<defect>_ca_threshold` | `lane_length_<defect>_ca` (m) |
  |---|---|---|---|
  | 1 | Good | `(<= <threshold_value> mm/km)`, or `mm` for rutting | sum of segment lengths with `metric_value <= threshold_value` |
  | 2 | Poor | `(> <threshold_value> mm/km)` / `mm` | sum with `metric_value > threshold_value` |
  | 3 | empty | empty | empty |

- **IRC columns:** `<defect>_irc_category` is Good / Poor / Fair. `<defect>_irc_threshold` and `lane_length_<defect>_irc` are empty.
- **DB ids:** `upc_id`, `survey_source_id`, `survey_id` as above, and `id` = uuid4. Rows without one of the three DB ids are dropped.
- **Columns:** the same as the old `ris_trends_data` (`zone` / `source_system` are not written).

## New and existing surveys (scenario 1 / 2)

A survey is one `upc` + `phase` + `cycle` + `defect_detected_on`. The RIS view compares the surveys in the silver status tables with the surveys in `Latest/ris_view_data.csv`.

**Scenario 1: some surveys are new.**
1. **INSERT, for the new surveys only:**
   - silver status: every row `Open`, with no DB lookup;
   - `intermediate_10m_data` rows of those surveys;
   - `ris_view_data` rows, with `created_at` = `updated_at` = run time.
2. **Then UPDATE for all surveys**, as in scenario 2.

**Scenario 2: no new survey.** Every step is calculated for every survey and only updates rows of the newest saved table. No row is added or removed.

| Table | Row key | Updated columns |
|---|---|---|
| silver status | `id` + `upc` + `phase` + `cycle` + `detected_on` | `defect_reference_id`, `current_status`, `final_status`, only on rows with a DB case |
| `intermediate_10m_data` | `upc` + `zone` + `phase` + `cycle` + `source_system` + `defect_detected_on` + `lane` + `start_chainage` + `end_chainage` | `roughness_status`, `rutting_status`, `cracking_status`, `total_status` |
| `ris_view_data` | `survey_id` + `survey_source_id` + `upc_id` + `defect_type` + `pavement_type` + `status` | `total_defects_ca`, `affected_length_ca_m`, `affected_length_ca_km`; `updated_at` = run time on rows whose values changed |

- **Missing `survey_id`:** a survey whose rows are dropped (e.g. no `survey_data_map` row yet) never reaches `ris_view_data`. It counts as new on every run until the id is found, and its silver status / intermediate rows are replaced each time, never doubled.
- **`ris_trends_data`** is append-only:
  - only surveys that aren't in it yet are calculated and appended;
  - rows already saved are never changed.
- **Rebuild from scratch:** delete the saved copies (the dated folders and `Latest/`).

## DB ids of existing rows (`ris-kpi sync-db-ids`)

Run it by hand after every `ris-kpi run`. It compares two sets of tables:
- **run_data:** the newest `ris_view_data` / `ris_trends_data`;
- **kpi_data:** the DB exports `ris_view_kpi` / `ris_trends_kpi` in `NSV/DHMS/DIC_Staging/Latest/`.

A run_data row whose key matches a kpi_data row takes that row's `id` and `created_at`.

Two copies of each table are synced, each on its own rows, and overwritten in place with the same names:
- **`Latest/`:** the copy that `db_push` reads.
- **The newest `year=/month=/day=` copy:** the one the next `ris-kpi run` reads, so the DB ids carry into the next run.

Older dated copies are history and are not touched.

| Table | Key |
|---|---|
| `ris_view_data` | `survey_source_id`, `upc_id`, `defect_detected_on`, `survey_id`, `defect_type_id`, `status` |
| `ris_trends_data` | `upc_id`, `survey_id`, `survey_source_id`, `defect_detected_on`, `pavement_type`, `roughness_ca_category`, `roughness_irc_category`, `rutting_ca_category`, `rutting_irc_category` |

The DB push (`db_push/`, see its README) reads `DHMS/NSV/Gold/Latest/`. Columns the CSV doesn't have, such as the IRC columns of `ris_view_kpi`, are skipped.

## Local runs

Set `RIS_KPI_STORAGE=local` to use folders in the repository instead of S3:
- **Inputs:**
  - `input_data/silver_data/`: the same UPC-folder layout as on S3;
  - `input_data/raw_db_tables/`: flat `<table>.csv`, or `<table>/<YYYY>/<MM>/<DD>/<table>.csv` snapshots.
- **Outputs:**
  - `output_data/silver_data_with_status/`;
  - `output_data/output_kpi_data/` (`intermediate_10m_data`, `ris_view_data`, `ris_trends_data`), with `year=/month=/day=` and `latest/`.

Unit tests (`pytest`) use an in-memory S3 stand-in and need no AWS access.

## Layout

```
├── pyproject.toml              # dependencies, `ris-kpi` command, pytest settings
├── src/ris_kpi/
│   ├── cli.py, pipelines.py    # ris-kpi command, run_full
│   ├── config.py               # all settings (S3 locations at the top)
│   ├── common/                 # silver.py (UPC folders -> one table), io.py, s3.py, storage.py (dated / latest
│   │                           # folders), db_tables.py, db_ids.py, keys.py, logging.py
│   ├── ris_view/
│   │   ├── pipeline.py         # scenario 1 / 2, runs the three steps (INSERT / UPDATE)
│   │   ├── incremental.py      # new / existing surveys, INSERT / UPDATE of a saved table
│   │   ├── silver_status.py    # step 1   <- status.py
│   │   ├── intermediate.py     # step 2   <- intermediate_10m.py
│   │   ├── view.py             # step 3   <- view_table.py, db_ids.py, project_master.py
│   │   └── loaders.py
│   ├── ris_trends/             # pipeline.py, loaders.py, kpi_metrics.py, categories.py, db_ids.py, append.py, spark.py
│   └── tools/db_ids_sync.py    # ris-kpi sync-db-ids
├── db_push/                    # standalone DB push (dry run / live)
└── tests/
```
