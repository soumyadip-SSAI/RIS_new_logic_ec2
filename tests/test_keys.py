from datetime import date

import pandas as pd

from ris_kpi.common.keys import filter_upcs, to_date, to_int, to_str_id, upc_slash, upc_underscore


def test_upc_forms():
    s = pd.Series(["N_02015_03002_GJ", "N/07024/01001/GJ", None])
    assert upc_slash(s).tolist()[:2] == ["N/02015/03002/GJ", "N/07024/01001/GJ"]
    assert upc_underscore(s).tolist()[:2] == ["N_02015_03002_GJ", "N_07024_01001_GJ"]


def test_to_str_id_drops_trailing_zero_decimal():
    assert to_str_id(pd.Series([2.0, "3", "10.0"])).tolist() == ["2", "3", "10"]


def test_to_int_truncates_toward_zero_and_keeps_null():
    out = to_int(pd.Series([2.9, -2.9, None]))
    assert out.tolist()[:2] == [2, -2]
    assert pd.isna(out.iloc[2])


def test_to_date_handles_years_before_1677():
    assert to_date(pd.Series(["0202-07-14", "2026-11-14 00:00:00"])).tolist() == [date(202, 7, 14), date(2026, 11, 14)]


def test_filter_upcs_matches_both_forms():
    df = pd.DataFrame({"upc": ["N_1_2_GJ", "N/1/2/GJ", "N/9/9/GJ"]})
    assert len(filter_upcs(df, ["N/1/2/GJ"])) == 2
    assert filter_upcs(df, []) is df
