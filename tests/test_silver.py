"""Silver tables of the new layout: every UPC folder read and stacked, locally and on S3."""
import pandas as pd

import pytest

from ris_kpi import cli, config
from ris_kpi.common.silver import find_silver_files, keep_scope, load_silver, scope_folders

ALL = {"Zone": "", "Phase": "", "Cycle": "", "UPC": ""}

CSV_A = "id,upc,phase,cycle,detected_on,pavement_type,extra\n1,N/1,2.0,1,2026-04-21 00:00:00,Cement concrete,x\n"
CSV_B = "id,upc,phase,cycle,detected_on,pavement_type\n1,N_2,1,1,2024-11-10,Asphalt\n"


def test_load_silver_stacks_every_upc_folder(tmp_path):
    a = tmp_path / "Zone=B/Phase=2/Cycle=1/UPC=N_1/dlm_kpi_new"
    b = tmp_path / "Zone=B/Phase=1/Cycle=1/UPC=N_2"
    a.mkdir(parents=True), b.mkdir(parents=True)
    (a / "master_pothole.csv").write_text(CSV_A)
    pd.read_csv(pd.io.common.StringIO(CSV_B)).to_parquet(b / "master_pothole.parquet")
    (a / "master_pothole_old.csv").write_text(CSV_A)                    # other table: not read

    assert len(find_silver_files(tmp_path, "master_pothole", ALL)) == 2
    df = load_silver(tmp_path, "master_pothole", scope=ALL)
    assert sorted(df["upc"]) == ["N/1", "N/2"]                           # '_' form read as '/'
    assert set(df["phase"]) == {"1", "2"} and set(df["detected_on"]) == {"2026-04-21", "2024-11-10"}
    assert set(df["pavement_type"]) == {"Cement Concrete", "Asphalt"}   # spelling unified
    assert df["extra"].isna().sum() == 1                                 # column missing in one file: empty
    assert load_silver(tmp_path, "master_pothole", keep_upcs=["N/2"], scope=ALL)["upc"].tolist() == ["N/2"]
    # scope: only the folders of one survey
    one = {"Zone": "B", "Phase": "1", "Cycle": "1", "UPC": "N/2"}
    assert load_silver(tmp_path, "master_pothole", scope=one)["upc"].tolist() == ["N/2"]
    assert len(find_silver_files(tmp_path, "master_pothole", {**ALL, "Phase": "2"})) == 1   # open Zone, fixed Phase
    with pytest.raises(FileNotFoundError, match="Zone=C"):
        load_silver(tmp_path, "master_pothole", scope={**one, "Zone": "C"})


def test_load_silver_on_s3(fake_s3):
    root = "s3://bucket/NSV/Silver/new_logic"
    fake_s3.put(f"{root}/Zone=B/Phase=2/Cycle=1/UPC=N_1/dlm_kpi_new/master_pothole.csv", CSV_A)
    fake_s3.put(f"{root}/Zone=B/Phase=1/Cycle=1/UPC=N_2/master_pothole.csv", CSV_B)
    fake_s3.put(f"{root}/Zone=B/Phase=1/Cycle=1/UPC=N_2/master_ravelling.csv", CSV_B)
    df = load_silver(root, "master_pothole", columns=["upc", "phase", "cycle", "detected_on"], scope=ALL)
    assert sorted(df["upc"]) == ["N/1", "N/2"] and list(df.columns) == ["upc", "phase", "cycle", "detected_on"]
    one = load_silver(root, "master_pothole", scope={"Zone": "B", "Phase": "1", "Cycle": "1", "UPC": "N_2"})
    assert one["upc"].tolist() == ["N/2"]


def test_scope_folders_and_keep_scope():
    assert scope_folders({"Zone": "B", "Phase": "", "Cycle": "1", "UPC": "N/01002/01001/GJ"}) == [
        "Zone=B", "Cycle=1", "UPC=N_01002_01001_GJ"]
    gold = pd.DataFrame({"upc": ["N/1", "N/2", "N/2"], "zone": ["B", "B", "A"], "phase": ["1.0", "1", "1"],
                         "cycle": ["1", "1", "1"]})
    assert keep_scope(gold, {"Zone": "B", "Phase": "1", "Cycle": "1", "UPC": "N_2"})["zone"].tolist() == ["B"]
    assert len(keep_scope(gold, {"Zone": "", "Phase": "1", "Cycle": "", "UPC": ""})) == 3


def test_cli_scope_options(monkeypatch):
    monkeypatch.setattr(config, "SILVER_SCOPE", {"Zone": "B", "Phase": "1", "Cycle": "1", "UPC": "N/1"})
    cli.apply_scope(cli._build_parser().parse_args(["run", "--upc", "N/9", "--phase", "2"]))
    assert config.SILVER_SCOPE == {"Zone": "B", "Phase": "2", "Cycle": "1", "UPC": "N/9"}
    cli.apply_scope(cli._build_parser().parse_args(["ris-trends", "--all-upcs"]))
    assert config.SILVER_SCOPE == ALL
