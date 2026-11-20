"""Reading / writing tables on S3 (in-memory fake S3) and on the local disk through the same functions."""
from pathlib import Path

import pandas as pd
import pytest

from ris_kpi.common import s3
from ris_kpi.common.io import find_table_file, join, name_of, read_table_file, read_text_csv, save_csv

DIR = "s3://bucket/NSV/Gold_kpi_status"


def test_split_s3_uri():
    assert s3.split_s3_uri("s3://bucket/a/b.csv") == ("bucket", "a/b.csv")
    with pytest.raises(ValueError):
        s3.split_s3_uri("/local/b.csv")
    with pytest.raises(ValueError):
        s3.split_s3_uri("s3://bucket")


def test_join_and_name():
    assert join(DIR, "x.csv") == f"{DIR}/x.csv"
    assert join(DIR + "/", "x.csv") == f"{DIR}/x.csv"
    assert join(Path("out"), "x.csv") == Path("out") / "x.csv"
    assert name_of(f"{DIR}/x.csv") == "x.csv"


def test_save_and_read_back_on_s3(fake_s3):
    df = pd.DataFrame({"upc": ["N/1"], "length": [1.5]})
    location = save_csv(df, DIR, "ris_view_dw")
    assert location == f"{DIR}/ris_view_dw.csv"
    assert fake_s3.get(location).splitlines() == ["upc,length", "N/1,1.5"]
    pd.testing.assert_frame_equal(read_table_file(location), df)
    assert read_text_csv(location)["length"].tolist() == ["1.5"]


def test_find_table_file_on_s3(fake_s3):
    fake_s3.put(f"{DIR}/t.csv", "a\n1\n")
    assert find_table_file(DIR, "t") == f"{DIR}/t.csv"
    with pytest.raises(FileNotFoundError, match="nope"):
        find_table_file(DIR, "nope")


def test_missing_s3_file(fake_s3):
    with pytest.raises(FileNotFoundError):
        read_table_file(f"{DIR}/missing.csv")


def test_local_files_still_work(tmp_path):
    location = save_csv(pd.DataFrame({"a": [1]}), tmp_path / "out", "t")
    assert find_table_file(tmp_path / "out", "t") == location
    assert read_table_file(location)["a"].tolist() == [1]
