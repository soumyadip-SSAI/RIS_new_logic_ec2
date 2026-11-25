"""DB id columns of ris_view_data / ris_trends_data, and the UPC lengths from master_upc_project."""
import uuid

import pandas as pd

from ris_kpi.ris_trends import db_ids as trends_ids
from ris_kpi.ris_view import db_ids as view_ids
from ris_kpi.ris_view.project_master import load_project_master

DB = {
    "master_upc_project": pd.DataFrame({"id": ["11", "12"], "upc": ["N/1", "N_2"],
                                        "length_km": ["10.5", "x"], "lane_length_km": ["42", None]}),
    "master_survey_system": pd.DataFrame({"id": ["9"], "system_code": ["NSV"]}),
    "survey_data_map": pd.DataFrame({"survey_schedule_id": ["s1", "s1"], "upc": ["N/1", "N/1"],
                                     "phase": ["1", "1"], "cycle": ["2.0", "2"],
                                     "survey_date": ["2026-01-05", "2026-02-01"]}),
    "master_defect_type": pd.DataFrame({
        "id": ["d-pot", "d-crack-rigid", "d-rut-flex"],
        "survey_system": ["NSV"] * 3,
        "asset_type": ["Flexible Pavement", "Rigid Pavements", "Flexible Pavement"],
        "defect_name": ["Pothole", "Crack on Rigid Pavement", "Rutting"]}),
}


def test_defect_type_keys():
    asset, name = view_ids.defect_type_keys(pd.Series(["Cracking", "Cracking", "Pothole", "Rutting", "Total"]),
                                            pd.Series(["Cement Concrete", "", None, "", ""]))
    assert asset.tolist()[:4] == ["Rigid Pavements", "Flexible Pavement", "Flexible Pavement", "PAVEMENT"]
    assert name.tolist()[:4] == ["Crack on Rigid Pavement", "Cracking", "Pothole", "Rutting"]
    assert pd.isna(asset.iloc[4]) and pd.isna(name.iloc[4])


def view_rows(*rows):
    return pd.DataFrame([{"source_system": "NSV", "upc": u, "zone": "B", "phase": "1", "cycle": "2",
                          "defect_detected_on": "2026-01-05", "defect_type": d, "pavement_type": p, "status": "open",
                          "upc_length_km": 1.0, "upc_lane_length_km": 4.0, "total_defects_ca": 1,
                          "affected_length_ca_m": 10.0, "affected_length_ca_km": 0.01} for u, d, p in rows])


def test_view_ids_fallback_and_dropped_rows():
    out = view_ids.add_db_ids(view_rows(("N/1", "Pothole", None), ("N/1", "Cracking", "Cement Concrete"),
                                        ("N/1", "Rutting", None), ("N/1", "Total", None),
                                        ("N/2", "Pothole", None)), DB)
    out = view_ids.drop_rows_without_ids(out)                              # N/2: no survey_data_map row
    assert out["upc"].tolist() == ["N/1"] * 4
    assert out["upc_id"].unique().tolist() == ["11"] and out["survey_source_id"].unique().tolist() == ["9"]
    assert out["survey_id"].unique().tolist() == ["s1"]                    # same id on two rows: one survey
    assert out["defect_type_id"].tolist()[:3] == ["d-pot", "d-crack-rigid", "d-rut-flex"]  # Rutting via fallback
    assert pd.isna(out["defect_type_id"].iloc[3])                          # Total: no defect type id
    assert out["id"].is_unique and str(uuid.UUID(out["id"].iloc[0])) == out["id"].iloc[0]
    final = view_ids.finalize(out.assign(created_at="t", updated_at="t"))
    assert final.columns.tolist() == view_ids.FINAL_COLUMNS and "zone" not in final.columns


def test_trends_ids_and_dropped_rows():
    rows = pd.DataFrame({"upc": ["N/1", "N/2"], "phase": ["1", "1"], "cycle": ["2", "2"],
                         "source_system": ["NSV"] * 2, "defect_detected_on": ["2026-01-05"] * 2})
    out = trends_ids.drop_rows_without_ids(trends_ids.add_db_ids(rows, DB))
    assert out["upc"].tolist() == ["N/1"]                                  # N/2: no survey id -> dropped
    assert out[["upc_id", "survey_id", "survey_source_id"]].iloc[0].tolist() == ["11", "s1", "9"]
    assert "created_at" not in out.columns                                 # set when appended
    kept = trends_ids.drop_rows_without_ids(trends_ids.add_db_ids(rows, DB), drop=False)
    assert kept["upc"].tolist() == ["N/1", "N/2"] and pd.isna(kept["survey_id"].iloc[1])   # DROP_ROWS_WITHOUT_IDS=False


def test_view_rows_without_ids_kept_when_not_dropping():
    out = view_ids.add_db_ids(view_rows(("N/1", "Pothole", None), ("N/2", "Pothole", None)), DB)
    assert view_ids.drop_rows_without_ids(out)["upc"].tolist() == ["N/1"]
    kept = view_ids.drop_rows_without_ids(out, drop=False)
    assert kept["upc"].tolist() == ["N/1", "N/2"] and pd.isna(kept["survey_id"].iloc[1])


def test_drop_rows_setting_from_env(monkeypatch):
    from ris_kpi import config
    for value, expected in (("False", False), ("no", False), ("TRUE", True), ("", True)):
        monkeypatch.setenv("DROP_ROWS_WITHOUT_IDS", value)
        assert config.env_bool("DROP_ROWS_WITHOUT_IDS", True) is expected
    assert config.RisView.DROP_ROWS_WITHOUT_IDS is config.RisTrends.DROP_ROWS_WITHOUT_IDS   # one setting, both tables


def test_upc_lengths_from_master_upc_project():
    info = load_project_master(DB["master_upc_project"]).set_index("upc")
    assert info.loc["N/1", "upc_length_km"] == 10.5 and info.loc["N/1", "upc_lane_length_km"] == 42
    assert pd.isna(info.loc["N/2", "upc_length_km"])                       # '_' form read as '/', invalid -> NaN


def test_survey_id_matched_on_upc_phase_cycle_only():
    """survey_id: upc + phase + cycle; survey_date (and defect_detected_on) are not part of the key."""
    db = dict(DB, survey_data_map=pd.DataFrame({
        "survey_schedule_id": ["s1", "s1", "s2"], "upc": ["N/1", "N_1", "N/2"], "phase": ["1", "1.0", "1"],
        "cycle": ["2", "2", "2"], "survey_date": ["2020-01-01", None, "2020-01-01"]}))   # dates differ from the data
    rows = pd.DataFrame({"upc": ["N_1", "N/1", "N/2", "N/1"], "phase": ["1", "1", "1", "1"],
                         "cycle": ["2", "2", "2", "3"], "source_system": ["NSV"] * 4,
                         "defect_detected_on": ["2026-01-05", "2026-03-09", None, "2026-01-05"]})
    out = trends_ids.add_db_ids(rows, db)
    assert out["survey_id"].tolist()[:3] == ["s1", "s1", "s2"]
    assert pd.isna(out["survey_id"].iloc[3])                               # cycle 3: no survey_data_map row
    view = view_ids.add_db_ids(view_rows(("N/1", "Pothole", None)).assign(defect_detected_on="2026-07-07"), db)
    assert view["survey_id"].tolist() == ["s1"]


# --- several survey_schedule_ids for one upc + phase + cycle (SURVEY_ID_FALLBACK) ------------------------------------

MULTI_MAP = pd.DataFrame({
    "survey_schedule_id": ["a1", "a2", "a3", "b1", "b2", "c1", "c2", "d1", "d2", "e1"],
    "upc": ["N/A"] * 3 + ["N/B"] * 2 + ["N/C"] * 2 + ["N/D"] * 2 + ["N/E"],
    "phase": ["1"] * 10, "cycle": ["1"] * 10, "survey_date": [None] * 10})
WFM = pd.DataFrame({
    "id": ["a1", "a2", "a3", "b1", "b2", "c1", "c2", "d1", "d2"],
    "upc": ["N/A", "N/A", "N/X", "N/B", "N_B", "N/C", "N/C", "N/D", "N/D"],
    "current_status": ["Validation in Progress", "Scheduled", "Survey Final Approved",   # a3: other UPC -> ignored
                       "validation in progress", " Survey Final Approved ",           # b2 wins (case / spaces)
                       "Scheduled", "Cancelled",                                       # c: no usable status
                       "Survey Final Approved", "Survey Final Approved"]})             # d: two equal -> empty


def lookup(fallback, wfm=WFM):
    from ris_kpi.common.db_ids import survey_id_lookup
    out = survey_id_lookup(MULTI_MAP, wfm, fallback)
    return dict(zip(out["_upc"], out["survey_id"]))


def test_several_ids_fallback_picks_by_wfm_status():
    assert lookup(True) == {"N/E": "e1", "N/A": "a1", "N/B": "b2"}         # C (no status) and D (tie): no survey_id


def test_several_ids_without_fallback_left_empty():
    assert lookup(False) == {"N/E": "e1"}


def test_several_ids_fallback_without_wfm_table():
    assert lookup(True, wfm=None) == {"N/E": "e1"}


def test_add_db_ids_uses_fallback_flag():
    db = dict(DB, survey_data_map=MULTI_MAP, wfm_survey_schedule=WFM,
              master_upc_project=pd.DataFrame({"id": ["u"], "upc": ["N/B"], "length_km": ["1"], "lane_length_km": ["4"]}))
    rows = pd.DataFrame({"upc": ["N_B"], "phase": ["1.0"], "cycle": ["1"], "source_system": ["NSV"],
                         "defect_detected_on": ["2026-01-05"]})
    assert trends_ids.add_db_ids(rows, db, survey_id_fallback=True)["survey_id"].tolist() == ["b2"]
    assert trends_ids.add_db_ids(rows, db, survey_id_fallback=False)["survey_id"].isna().all()
    view = view_ids.add_db_ids(view_rows(("N/B", "Pothole", None)).assign(phase="1", cycle="1"), db, True)
    assert view["survey_id"].tolist() == ["b2"]


def test_wfm_table_loaded_only_with_fallback():
    assert "wfm_survey_schedule" in view_ids.id_db_tables(True) and "wfm_survey_schedule" in trends_ids.id_db_tables(True)
    assert "wfm_survey_schedule" not in view_ids.id_db_tables(False) + trends_ids.id_db_tables(False)


def test_survey_id_fallback_setting_from_env(monkeypatch):
    import subprocess, sys, os
    for value, expected in (("False", "False"), ("True", "True"), ("", "True")):
        env = dict(os.environ, SURVEY_ID_FALLBACK=value)
        out = subprocess.run([sys.executable, "-c", "from ris_kpi import config as c; "
                              "print(c.SURVEY_ID_FALLBACK, c.RisView.SURVEY_ID_FALLBACK, c.RisTrends.SURVEY_ID_FALLBACK)"],
                             env=env, capture_output=True, text=True, check=True).stdout.split()
        assert out == [expected] * 3
