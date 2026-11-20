"""sync-db-ids: id / created_at of the DB KPI tables copied into the saved ris_view_data / ris_trends_data."""
from datetime import date

import pandas as pd
import pytest

from ris_kpi import config
from ris_kpi.common import storage
from ris_kpi.tools import db_ids_sync as sync

KEY = sync.TABLES["ris_view"]["key"]


def view(rows):
    """rows: (id, defect_type_id, status, detected_on, created_at)"""
    return pd.DataFrame([{"id": i, "survey_source_id": "S", "upc_id": "U", "defect_detected_on": d, "survey_id": "V",
                          "defect_type_id": t, "status": s, "total_defects_ca": "1", "created_at": c}
                         for i, t, s, d, c in rows])


def test_copies_db_id_and_created_at_on_matching_keys():
    run = view([("r1", "crack", "open", "2025-01-25", "run"), ("r2", "crack", "close", "2025-01-25", "run"),
                ("r3", "pothole", "open", "2025-01-25", "run")])
    db = view([("d1", "CRACK", "OPEN", "2025-01-25 00:00:00+00", "2026-10-22"),     # case / date format differ
               ("d2", "crack", "close", "25-01-2025", "2026-10-22"),
               ("d9", "rutting", "open", "2025-01-25", "2026-10-22")])               # only in the DB
    out, r = sync.copy_db_ids(run, db, KEY, "t")
    assert out["id"].tolist() == ["d1", "d2", "r3"]                                 # r3: not in the DB, keeps its id
    assert out["created_at"].tolist() == ["2026-10-22", "2026-10-22", "run"]
    assert out["total_defects_ca"].tolist() == ["1"] * 3 and len(out) == 3
    assert (r["matched"], r["changed"], r["not_in_db"], r["only_in_db"]) == (2, 2, 1, 1)
    assert r["changes"]["run_id"].tolist() == ["r1", "r2"]


def test_ambiguous_keys_are_left_unchanged():
    run = view([("r1", "crack", "open", "2025-01-25", "run")])
    db = view([("d1", "crack", "open", "2025-01-25", "x"), ("d2", "crack", "open", "2025-01-25", "y")])
    out, r = sync.copy_db_ids(run, db, KEY, "t")
    assert out["id"].tolist() == ["r1"] and r["ambiguous_db"] == 2 and r["matched"] == 0


def test_missing_key_column_is_reported():
    with pytest.raises(KeyError, match="status"):
        sync.copy_db_ids(view([]).drop(columns="status"), view([]), KEY, "t")


def test_sync_table_overwrites_latest_and_newest_dated_copy(tmp_path, monkeypatch):
    out_root, db_dir = tmp_path / "gold", tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setitem(sync.TABLES, "ris_view", {**sync.TABLES["ris_view"], "run_root": out_root})
    older = storage.save_run(view([("r0", "crack", "open", "2025-01-25", "run")]), out_root, "ris_view_data",
                             date(2026, 11, 10))
    dated = storage.save_run(view([("r1", "crack", "open", "2025-01-25", "run")]), out_root, "ris_view_data",
                             date(2026, 11, 17))
    latest = storage.latest_location(out_root, "ris_view_data")
    view([("d1", "crack", "open", "2025-01-25", "2026-10-22")]).to_csv(db_dir / "ris_view_kpi.csv", index=False)

    assert sync.sync_table("ris_view", dry_run=True, db_dir=db_dir)["changed"] == 1
    assert storage.read_text(latest)["id"].tolist() == ["r1"]                         # dry run: not saved
    assert storage.read_text(dated)["id"].tolist() == ["r1"]

    sync.sync_table("ris_view", db_dir=db_dir)
    for location in (latest, dated):                                                  # both overwritten in place
        assert storage.read_text(location)["id"].tolist() == ["d1"]
        assert storage.read_text(location)["created_at"].tolist() == ["2026-10-22"]
        assert sorted(p.name for p in location.parent.iterdir()) == ["ris_view_data.csv"]  # same name, no extra file
    assert storage.read_text(older)["id"].tolist() == ["r0"]                          # older dated copy: history
    assert storage.load_latest(out_root, "ris_view_data")[0]["id"].tolist() == ["d1"]  # what the next run reads
    assert sync.sync_table("ris_view", db_dir=db_dir)["changed"] == 0                 # second run: nothing to do


def test_sync_table_on_s3(fake_s3, monkeypatch):
    root = "s3://bucket/NSV/Gold/DIC_Staging_new_logic"
    monkeypatch.setitem(sync.TABLES, "ris_view", {**sync.TABLES["ris_view"], "run_root": root})
    run_csv = view([("r1", "crack", "open", "2025-01-25", "run")]).to_csv(index=False)
    fake_s3.put(f"{root}/{config.LATEST_FOLDER}/ris_view_data.csv", run_csv)
    fake_s3.put(f"{root}/year=2026/month=11/day=17/ris_view_data.csv", run_csv)
    fake_s3.put(f"{root}/year=2026/month=11/day=10/ris_view_data.csv", run_csv)
    fake_s3.put("s3://bucket/db/ris_view_kpi.csv",
                view([("d1", "crack", "open", "2025-01-25", "2026-10-22")]).to_csv(index=False))
    sync.sync_table("ris_view", db_dir="s3://bucket/db")
    assert "d1,S,U" in fake_s3.get(f"{root}/{config.LATEST_FOLDER}/ris_view_data.csv")
    assert "d1,S,U" in fake_s3.get(f"{root}/year=2026/month=11/day=17/ris_view_data.csv")   # newest dated copy
    assert "r1,S,U" in fake_s3.get(f"{root}/year=2026/month=11/day=10/ris_view_data.csv")   # older: untouched
