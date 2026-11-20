"""DB tables read from S3 (in-memory fake S3, no AWS access needed) or from a local folder."""
import pytest

from ris_kpi.common.db_tables import load_db_tables

CASES = "id,defect_reference_id,current_status,defect_type,record_level\n1,r1,CLOSED,Pothole,child\n2,r2,,Pothole,child\n"
REFS = "id,defect_collection,defect_id\nr1,pothole_child_ca,7\n"
TABLES = ["dlm_defect_case", "udm_defect_reference"]
STATUS_DIR = "s3://bucket/DHMS/Staging/Latest"


def test_reads_from_s3_as_text_with_empty_as_null(fake_s3):
    fake_s3.put(f"{STATUS_DIR}/dlm_defect_case.csv", CASES)
    fake_s3.put(f"{STATUS_DIR}/udm_defect_reference.csv", REFS)
    tables = load_db_tables(STATUS_DIR, TABLES)
    cases = tables["dlm_defect_case"]
    assert cases["id"].tolist() == ["1", "2"]                        # text, not numbers
    assert cases["current_status"].isna().tolist() == [False, True]  # empty cell -> null
    assert tables["udm_defect_reference"]["defect_id"].tolist() == ["7"]


def test_missing_required_column_fails(fake_s3):
    fake_s3.put(f"{STATUS_DIR}/dlm_defect_case.csv", CASES)
    fake_s3.put(f"{STATUS_DIR}/udm_defect_reference.csv", "id,defect_id\nr1,7\n")
    with pytest.raises(ValueError, match="udm_defect_reference.*defect_collection"):
        load_db_tables(STATUS_DIR, TABLES)


def test_missing_table_fails(fake_s3):
    fake_s3.put(f"{STATUS_DIR}/dlm_defect_case.csv", CASES)
    with pytest.raises(FileNotFoundError, match="udm_defect_reference"):
        load_db_tables(STATUS_DIR, TABLES)


def test_local_folder(tmp_path):
    (tmp_path / "udm_defect_reference.csv").write_text(REFS)
    tables = load_db_tables(tmp_path, ["udm_defect_reference"])
    assert tables["udm_defect_reference"]["id"].tolist() == ["r1"]


def test_local_dated_snapshots_newest_used(tmp_path):
    for day, ref in (("13", "old"), ("17", "new")):
        folder = tmp_path / "udm_defect_reference" / "2026" / "11" / day
        folder.mkdir(parents=True)
        (folder / "udm_defect_reference.csv").write_text(f"id,defect_collection,defect_id\n{ref},c,7\n")
    (tmp_path / "udm_defect_reference" / "notes").mkdir()  # not a date folder: ignored
    tables = load_db_tables(tmp_path, ["udm_defect_reference"])
    assert tables["udm_defect_reference"]["id"].tolist() == ["new"]


def test_flat_file_wins_over_snapshots(tmp_path):
    folder = tmp_path / "udm_defect_reference" / "2026" / "11" / "17"
    folder.mkdir(parents=True)
    (folder / "udm_defect_reference.csv").write_text("id,defect_collection,defect_id\nsnap,c,7\n")
    (tmp_path / "udm_defect_reference.csv").write_text(REFS)
    assert load_db_tables(tmp_path, ["udm_defect_reference"])["udm_defect_reference"]["id"].tolist() == ["r1"]
