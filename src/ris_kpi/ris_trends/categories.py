"""Roughness / rutting condition categories: 3 rows per grain (row_rank 1-3, used only to pair the roughness and
rutting rows and to sort; not in the output).

    row_rank  <defect>_ca_category  <defect>_irc_category
       1      Good                  Good
       2      Poor                  Poor
       3      (empty)               Fair

CA (master_<defect>, rows with threshold_name 'As per CA'; threshold = the row's own threshold_value):
    <defect>_ca_threshold     '(<= <threshold_value> <unit>)' / '(> <threshold_value> <unit>)' / empty
                              unit: mm/km (roughness), mm (rutting)
    lane_length_<defect>_ca   sum(end_chainage - start_chainage) in m of the rows with
                              metric_value <= threshold_value (Good) / > threshold_value (Poor) / empty (row 3)
IRC (no 'As per IRC' rows in the new silver data): only the category label is filled;
    <defect>_irc_threshold and lane_length_<defect>_irc are always empty.
"""
import logging
from functools import reduce

import numpy as np
import pandas as pd

from ris_kpi.ris_trends.kpi_metrics import GRAIN
from ris_kpi.ris_trends.spark import spark_round

log = logging.getLogger(__name__)

UNITS = {"roughness": "mm/km", "rutting": "mm"}


def _fmt_number(x):
    """2750.0 -> '2750', 2750.5 -> '2750.50'"""
    if pd.isna(x):
        return pd.NA
    s = f"{x:.2f}"
    return s[:-3] if s.endswith(".00") else s


def ca_rows(master, ca_threshold_name):
    """'As per CA' rows with metric value, segment length and threshold as numbers."""
    src = master[master["threshold_name"] == ca_threshold_name]
    return src[GRAIN].assign(
        mv=pd.to_numeric(src["metric_value"], errors="coerce"),
        thr=pd.to_numeric(src["threshold_value"], errors="coerce"),
        len_m=pd.to_numeric(src["end_chainage"], errors="coerce") - pd.to_numeric(src["start_chainage"], errors="coerce"),
    )


def grain_lengths(src, defect):
    """Per grain: Good / Poor length (m) and the threshold_value used for the text."""
    # empty metric_value or threshold_value -> neither Good nor Poor (counts 0)
    good = src["len_m"].where(src["mv"] <= src["thr"], 0.0)
    poor = src["len_m"].where(src["mv"] > src["thr"], 0.0)
    grouped = src.assign(good=good, poor=poor).groupby(GRAIN, dropna=False)
    out = grouped[["good", "poor"]].sum(min_count=1)
    out["thr"] = grouped["thr"].first()

    n_thr = grouped["thr"].nunique()
    if (n_thr > 1).any():
        log.warning("%s: several threshold_value values in one grain (first one shown in the text):\n%s", defect,
                    n_thr[n_thr > 1].rename("threshold_values").reset_index().to_string(index=False))
    no_thr = int(src["thr"].isna().sum())
    if no_thr:
        log.warning("%s: %s 'As per CA' row(s) without threshold_value — counted in neither Good nor Poor",
                    defect, no_thr)
    return out.reset_index()


def build_defect_categories(defect, master, ca_threshold_name):
    """3 rows per grain with the <defect>_ca_* / <defect>_irc_* / lane_length_<defect>_* columns."""
    g = grain_lengths(ca_rows(master, ca_threshold_name), defect)
    thr_txt = g["thr"].map(_fmt_number).astype("string")
    unit = UNITS[defect]
    empty_len = pd.Series(np.nan, index=g.index)
    rows = [
        # rank, CA category, CA threshold text (empty when no threshold_value), CA length, IRC category
        (1, "Good", "(<= " + thr_txt + f" {unit})", g["good"], "Good"),
        (2, "Poor", "(> " + thr_txt + f" {unit})", g["poor"], "Poor"),
        (3, pd.NA, pd.NA, empty_len, "Fair"),
    ]
    frames = [
        g[GRAIN].assign(**{
            "row_rank": rank,
            f"{defect}_ca_category": ca_category,
            f"{defect}_ca_threshold": ca_text,
            f"lane_length_{defect}_ca": spark_round(ca_length, 3),
            f"{defect}_irc_category": irc_category,
            f"{defect}_irc_threshold": pd.NA,                    # no 'As per IRC' data
            f"lane_length_{defect}_irc": np.nan,
        })
        for rank, ca_category, ca_text, ca_length, irc_category in rows
    ]
    return pd.concat(frames, ignore_index=True)


def build_categories(roughness, rutting, ca_threshold_name):
    frames = [build_defect_categories("roughness", roughness, ca_threshold_name),
              build_defect_categories("rutting", rutting, ca_threshold_name)]
    cat = reduce(lambda a, b: a.merge(b, on=GRAIN + ["row_rank"], how="outer"), frames)
    log.info("Categories: %s rows (%s grain(s) x 3)", len(cat), len(cat[GRAIN].drop_duplicates()))
    return cat
