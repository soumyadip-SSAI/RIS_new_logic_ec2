"""Table loaders for the RIS view steps.

  load_status_table   newest saved silver status table (step 1 output), keys normalised as the silver tables
  load_intermediate   newest saved intermediate_10m_data (step 2 output), back to the in-memory column names
Both keep only the rows of the run scope (upc_to_run.csv / every folder): other surveys are never recalculated.
Silver input tables: common/silver.load_silver.
"""
import logging

import pandas as pd

from ris_kpi.common.silver import keep_scope, normalise
from ris_kpi.common.storage import read_text, require_latest

log = logging.getLogger(__name__)

# One survey; every table is joined / grouped within it (date column 'detected_on' in memory)
SURVEY = ["upc", "zone", "phase", "cycle", "source_system", "detected_on"]


def num(series):
    """Numbers as float64 (NaN for empty / invalid), whatever the source dtype (text / string / object)."""
    return pd.to_numeric(series, errors="coerce").astype("float64")


def load_status_table(root, name):
    location = require_latest(root, name)
    df = keep_scope(normalise(read_text(location)))
    log.info("Loaded %-30s rows: %9s in scope  (%s)", name, f"{len(df):,}", location)
    return df


def load_intermediate(root, name):
    """intermediate_10m_data with detected_on (saved as defect_detected_on) and numeric chainage / flags."""
    location = require_latest(root, name)
    df = keep_scope(read_text(location)).rename(columns={"defect_detected_on": "detected_on"})
    for col in ("start_chainage", "end_chainage", "length_m"):
        df[col] = num(df[col])
    for col in [c for c in df.columns if c.endswith("_flag")]:
        df[col] = num(df[col]).fillna(0).astype(int)
    log.info("Loaded %-30s rows: %9s in scope  (%s)", name, f"{len(df):,}", location)
    return df
