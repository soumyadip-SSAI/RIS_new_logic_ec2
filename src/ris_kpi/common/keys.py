"""Key / type normalisation shared by the steps (Spark semantics the notebooks relied on)."""
from datetime import date

import numpy as np
import pandas as pd


def upc_slash(series):
    """N_02015_03002_GJ -> N/02015/03002/GJ (defect tables use '/', master_chainage uses '_')."""
    return series.astype("string").str.replace("_", "/", regex=False)


def upc_underscore(series):
    return series.astype("string").str.replace("/", "_", regex=False)


def filter_upcs(df, keep_upcs):
    """Rows of the selected UPCs (compared on the '/' form); all rows when keep_upcs is empty."""
    if not keep_upcs or "upc" not in df.columns:
        return df
    return df[upc_slash(df["upc"]).isin(keep_upcs).fillna(False).astype(bool)].reset_index(drop=True)


def to_str_id(series):
    """Cast phase/cycle style keys to string, '2.0' -> '2', so joins match across sources."""
    return series.astype("string").str.replace(r"\.0$", "", regex=True)


def to_int(series):
    """Spark .cast('int') on a double: truncate toward zero, null stays null."""
    return np.trunc(pd.to_numeric(series, errors="coerce")).astype("Int64")


def to_date(series):
    """'0202-07-14' -> datetime.date, matching the date objects in the parquet tables.
    (pd.to_datetime cannot hold years before 1677, which occur in the source data.)"""
    return series.map(lambda v: date.fromisoformat(str(v)[:10]) if pd.notna(v) else None)
