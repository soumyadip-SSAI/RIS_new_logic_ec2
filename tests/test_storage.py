"""Dated output folders (<root>/year=/month=/day=/ + latest/) on the local disk and on S3 (in-memory fake S3)."""
from datetime import date

import pandas as pd
import pytest

from ris_kpi import config
from ris_kpi.common import storage

LATEST = config.LATEST_FOLDER  # "Latest" on S3 (default), "latest" with RIS_KPI_STORAGE=local

ROOT = "s3://bucket/NSV/Gold_kpi_status"
DF = pd.DataFrame({"upc": ["N/1"], "phase": [1], "note": [None]})


def test_save_run_writes_dated_copy_and_latest_locally(tmp_path):
    out = storage.save_run(DF, tmp_path, "t", date(2026, 11, 7))
    assert out == tmp_path / "year=2026" / "month=11" / "day=07" / "t.csv"
    assert (tmp_path / LATEST / "t.csv").read_text() == out.read_text()


def test_find_latest_picks_newest_run_date_locally(tmp_path):
    assert storage.find_latest(tmp_path, "t") is None
    storage.save_run(DF.assign(phase=2), tmp_path, "t", date(2026, 12, 1))
    storage.save_run(DF.assign(phase=1), tmp_path, "t", date(2026, 11, 30))   # older run saved later
    newest = storage.find_latest(tmp_path, "t")
    assert storage.folder_date(newest) == date(2026, 12, 1)
    assert storage.read_text(newest)["phase"].tolist() == ["2"]
    (tmp_path / "year=2026" / "month=x").mkdir()                              # not a run-date folder: ignored
    assert storage.find_latest(tmp_path, "t") == newest


def test_find_latest_falls_back_to_latest_folder(tmp_path):
    (tmp_path / LATEST).mkdir()
    DF.to_csv(tmp_path / LATEST / "t.csv", index=False)
    assert storage.find_latest(tmp_path, "t") == tmp_path / LATEST / "t.csv"
    with pytest.raises(FileNotFoundError, match="no_such"):
        storage.require_latest(tmp_path, "no_such")


def test_save_and_find_on_s3(fake_s3):
    storage.save_run(DF, ROOT, "t", date(2026, 11, 7))
    storage.save_run(DF.assign(phase=3), ROOT, "t", date(2026, 11, 17))
    storage.save_run(DF, ROOT, "other", date(2026, 12, 1))                    # another table: not picked
    storage.save_run(DF, f"{ROOT}/sub", "t", date(2026, 12, 1))               # other folder in the root: not picked
    assert fake_s3.get(f"{ROOT}/{LATEST}/t.csv") == fake_s3.get(f"{ROOT}/year=2026/month=11/day=17/t.csv")
    newest = storage.find_latest(ROOT, "t")
    assert newest == f"{ROOT}/year=2026/month=11/day=17/t.csv"
    assert storage.parent_of(newest) == f"{ROOT}/year=2026/month=11/day=17"
    df, location = storage.load_latest(ROOT, "t")
    assert location == newest and df["phase"].tolist() == ["3"] and df["note"].isna().all()


def test_s3_without_list_permission_uses_latest_copy(fake_s3):
    storage.save_run(DF, ROOT, "t", date(2026, 11, 7))
    fake_s3.deny_list = True
    assert storage.find_latest(ROOT, "t") == f"{ROOT}/{LATEST}/t.csv"
    assert storage.find_latest(ROOT, "missing") is None


def test_as_text_matches_a_saved_table(tmp_path):
    out = storage.save_run(DF, tmp_path, "t", date(2026, 11, 7))
    pd.testing.assert_frame_equal(storage.as_text(DF), storage.read_text(out))
    assert storage.as_text(DF, empty_as_null=False)["note"].tolist() == [""]
