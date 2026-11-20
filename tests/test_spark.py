import pandas as pd

from ris_kpi.ris_trends.spark import GT, null_safe_compare, spark_round


def test_spark_round_is_half_up_not_half_even():
    assert spark_round(pd.Series([0.125, 2.5, None]), 2).tolist()[:2] == [0.13, 2.5]
    assert spark_round(pd.Series([2.5]), 0).tolist() == [3.0]


def test_null_safe_compare_returns_na_for_nulls():
    out = null_safe_compare(pd.Series([3, None]), pd.Series([1, 1]), GT)
    assert out.iloc[0] is True or out.iloc[0] == True  # noqa: E712
    assert pd.isna(out.iloc[1])
