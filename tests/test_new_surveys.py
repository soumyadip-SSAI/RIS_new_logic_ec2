"""New vs existing surveys: scenario 1 / 2, INSERT / UPDATE of the saved tables, ris_trends_data append."""
from datetime import date, datetime

import pandas as pd
import pytest

from ris_kpi import config
from ris_kpi.common import storage
from ris_kpi.common.db_ids import survey_key
from ris_kpi.ris_trends import append
from ris_kpi.ris_view import incremental as inc
from ris_kpi.ris_view import pipeline
from ris_kpi.ris_view.status import add_open_status, add_status, case_status

T1 = datetime(2026, 11, 17)


# --- surveys ---

def silver():
    return pd.DataFrame({"id": ["1", "2", "3"], "upc": ["N/1", "N_2", "N/1"], "phase": ["1", "1", "2.0"],
                         "cycle": ["1", "1", "1"], "detected_on": ["2026-01-05", "2026-02-01", "2026-03-01 00:00:00"]})


def test_survey_key_normalises_upc_phase_and_date():
    assert survey_key(silver(), inc.SILVER_SURVEY).tolist() == [
        "N/1|1|1|2026-01-05", "N/2|1|1|2026-02-01", "N/1|2|1|2026-03-01"]


def test_keep_surveys():
    df = silver()
    assert inc.keep_surveys(df, None, inc.SILVER_SURVEY) is df
    assert inc.keep_surveys(df, {"N/2|1|1|2026-02-01"}, inc.SILVER_SURVEY)["id"].tolist() == ["2"]
    assert inc.keep_surveys(df, set(), inc.SILVER_SURVEY).empty


# --- silver status: INSERT all Open, UPDATE only rows with a DB case ---

def test_new_surveys_are_open_without_db_lookup():
    out = add_open_status(silver(), "c")
    assert out["final_status"].tolist() == ["Open"] * 3
    assert out["current_status"].isna().all()
    assert list(out.columns[:4]) == ["id", "defect_reference_id", "current_status", "final_status"]


def test_update_silver_status_changes_only_rows_with_a_db_case():
    base = storage.as_text(add_open_status(silver(), "c"), empty_as_null=False)
    ref = pd.DataFrame({"defect_collection": ["c"], "defect_id": ["1"], "id": ["r1"]})
    cases = case_status(pd.DataFrame({"defect_reference_id": ["r1"], "current_status": ["CLOSED"]}))
    fresh = storage.as_text(add_status(silver(), "c", ref, cases))
    out = inc.update_silver_status(base, fresh, "c")
    assert out["final_status"].tolist() == ["Closed", "Open", "Open"]
    assert out["defect_reference_id"].tolist() == ["r1", "", ""]           # rows without a case keep saved values
    assert len(out) == len(base)


# --- INSERT / UPDATE of a saved table ---

def view_rows(*items):
    """(upc, survey_id, defect_type, status, total_defects_ca)"""
    return pd.DataFrame([{"id": f"id-{u}-{d}-{s}", "upc": u, "upc_id": "u" + u, "phase": "1", "cycle": "1",
                          "defect_detected_on": "2026-01-05", "survey_id": sid, "survey_source_id": "9",
                          "defect_type": d, "pavement_type": None, "status": s,
                          "total_defects_ca": n, "affected_length_ca_m": "0", "affected_length_ca_km": "0",
                          "created_at": "2026-11-01", "updated_at": "2026-11-01"} for u, sid, d, s, n in items])


def test_insert_surveys_adds_rows_and_replaces_resaved_surveys():
    base = view_rows(("A", "s1", "Rutting", "Open", "3"))
    assert inc.insert_surveys(None, base, inc.TABLE_SURVEY, "t").equals(base)
    new = view_rows(("B", "s2", "Rutting", "Open", "1"))
    assert inc.insert_surveys(base, new, inc.TABLE_SURVEY, "t")["upc"].tolist() == ["A", "B"]
    again = view_rows(("A", "s1", "Rutting", "Open", "9"))                  # survey already saved: replaced
    out = inc.insert_surveys(base, again, inc.TABLE_SURVEY, "t")
    assert out["total_defects_ca"].tolist() == ["9"]


def test_update_rows_changes_only_update_columns_and_touches_changed_rows():
    base = view_rows(("A", "s1", "Rutting", "Open", "3"), ("A", "s1", "Rutting", "Closed", "1"),
                     ("Z", "s9", "Rutting", "Open", "7"))
    fresh = view_rows(("A", "s1", "Rutting", "Open", "5"), ("A", "s1", "Rutting", "Closed", "1"),
                      ("B", "s2", "Rutting", "Open", "4")).assign(id="other", created_at="x")
    out = inc.update_rows(base, fresh, inc.RIS_VIEW_KEY, inc.RIS_VIEW_UPDATE, "t", touch=("updated_at", "T1"))
    assert out["total_defects_ca"].tolist() == ["5", "1", "7"]
    assert out["updated_at"].tolist() == ["T1", "2026-11-01", "2026-11-01"]  # only the changed row
    assert out["id"].tolist() == base["id"].tolist() and (out["created_at"] == "2026-11-01").all()  # saved ids kept
    assert len(out) == 3                                                       # B (no saved row) not inserted


def test_update_rows_rejects_duplicate_keys_in_fresh():
    rows = view_rows(("A", "s1", "Rutting", "Open", "3"), ("A", "s1", "Rutting", "Open", "4"))
    with pytest.raises(AssertionError, match="share the key"):
        inc.update_rows(rows, rows, inc.RIS_VIEW_KEY, inc.RIS_VIEW_UPDATE, "t")


# --- scenario 1 / 2 (pipeline.run_ris_view) ---

@pytest.fixture
def scenario(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "STEPS", {name: (lambda name: lambda **kw: calls.append((name, kw["mode"],
                                                                                         kw["surveys"])))(name)
                                            for name in pipeline.STEPS})

    def run(silver_set, saved_set):
        calls.clear()
        monkeypatch.setattr(pipeline, "silver_surveys", lambda: set(silver_set))
        monkeypatch.setattr(pipeline, "saved_surveys", lambda: set(saved_set))
        return pipeline.run_ris_view(run_time=T1), list(calls)
    return run


def test_scenario_1_inserts_new_surveys_then_updates_all(scenario):
    number, calls = scenario({"a", "b"}, {"a"})
    assert number == 1
    assert calls == [(s, inc.INSERT, {"b"}) for s in pipeline.STEPS] + [(s, inc.UPDATE, None) for s in pipeline.STEPS]


def test_scenario_2_updates_all(scenario):
    number, calls = scenario({"a"}, {"a", "old"})
    assert number == 2
    assert calls == [(s, inc.UPDATE, None) for s in pipeline.STEPS]


def test_selected_steps_run_as_update(scenario):
    calls = []
    pipeline.STEPS["view"] = lambda **kw: calls.append(kw["mode"])
    assert pipeline.run_ris_view(["view"], run_time=T1) is None and calls == [inc.UPDATE]


def test_saved_surveys_from_latest_ris_view_data_on_s3(fake_s3, monkeypatch):
    monkeypatch.setattr(pipeline.RisView, "KPI_OUTPUT_ROOT", "s3://bucket/NSV/Gold_kpi_status")
    assert pipeline.saved_surveys() == set()
    fake_s3.put(f"s3://bucket/NSV/Gold_kpi_status/{config.LATEST_FOLDER}/ris_view_data.csv",
                "upc,phase,cycle,defect_detected_on,status\nN/1,1.0,1,2026-01-05,Open\n")
    assert pipeline.saved_surveys() == {"N/1|1|1|2026-01-05"}


# --- ris_trends_data append ---

def test_keep_new_surveys_filters_silver_tables():
    existing = pd.DataFrame({"upc": ["N/1"], "phase": ["1"], "cycle": ["1"], "defect_detected_on": ["2026-01-05"]})
    tables = {"master_roughness_parent": silver(), "master_rutting_parent": silver()}
    out, n_new = append.keep_new_surveys(tables, existing)
    assert n_new == 2
    assert out["master_roughness_parent"]["id"].tolist() == ["2", "3"]
    assert out["master_rutting_parent"]["id"].tolist() == ["2", "3"]


def test_append_keeps_existing_rows_and_rejects_overlap():
    existing = pd.DataFrame({"id": ["x"], "upc": ["N/1"], "phase": ["1"], "cycle": ["1"],
                             "defect_detected_on": ["2026-01-05"], "created_at": ["2026-11-01"]})
    new = existing.assign(id="y", upc="N/2").drop(columns="created_at")
    out = append.append_ris_trends_data(existing, new, run_time=T1)
    assert out["id"].tolist() == ["x", "y"]
    assert out.loc[0, "created_at"] == "2026-11-01" and out.loc[1, "created_at"] == T1
    with pytest.raises(AssertionError, match="already in ris_trends_data"):
        append.append_ris_trends_data(existing, existing.drop(columns="created_at"))


def test_ris_trends_load_existing_reads_newest_saved_copy(tmp_path):
    assert append.load_existing(tmp_path, "ris_trends_data") is None
    old = pd.DataFrame({"upc": ["N/1"], "phase": ["1"], "cycle": ["1"], "defect_detected_on": ["2026-01-05"]})
    storage.save_run(old, tmp_path, "ris_trends_data", date(2026, 11, 1))
    storage.save_run(old.assign(upc="N/2"), tmp_path, "ris_trends_data", date(2026, 11, 17))
    assert append.load_existing(tmp_path, "ris_trends_data")["upc"].tolist() == ["N/2"]


def test_old_ris_trends_file_without_phase_is_rejected(tmp_path):
    storage.save_run(pd.DataFrame({"UPC": ["N/1"], "Defect_Detected_On": ["2026-01-05"]}), tmp_path,
                     "ris_trends_data", date(2026, 11, 1))
    with pytest.raises(ValueError, match="delete it"):
        append.load_existing(tmp_path, "ris_trends_data")
