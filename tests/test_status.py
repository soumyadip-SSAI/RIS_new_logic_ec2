"""The status rule the KPI depends on: Closed only for CLOSED / CANCELLED, Open otherwise (also with no case)."""
import pandas as pd
import pytest

from ris_kpi.ris_view.status import add_status, case_status, final_status


def test_final_status():
    s = pd.Series(["CLOSED", " cancelled ", "ASSIGNED", None])
    assert final_status(s).tolist() == ["Closed", "Closed", "Open", "Open"]


def test_add_status_joins_without_changing_rows():
    silver = pd.DataFrame({"id": ["1", "2", "3"], "metric": ["a", "b", "c"]})
    ref = pd.DataFrame({"defect_collection": ["rutting_parent"] * 2 + ["other"],
                        "defect_id": ["1", "2", "3"], "id": ["r1", "r2", "r3"]})
    cases = case_status(pd.DataFrame({"defect_reference_id": ["r1", "r2"], "current_status": ["CLOSED", "OPEN"]}))

    out = add_status(silver, "rutting_parent", ref, cases)

    assert list(out.columns) == ["id", "defect_reference_id", "current_status", "final_status", "metric"]
    assert out["final_status"].tolist() == ["Closed", "Open", "Open"]  # id 3 has no reference -> Open
    assert out["metric"].tolist() == ["a", "b", "c"]


def test_status_matched_on_display_defect_id():
    """RisView.STATUS_KEY: silver display_defect_id = udm_defect_reference.defect_id (not the silver id)."""
    from ris_kpi.config import RisView
    assert RisView.STATUS_KEY == "display_defect_id"
    silver = pd.DataFrame({"id": ["uuid-a", "uuid-b", "uuid-c", "uuid-d"],
                           "display_defect_id": [101.0, " 102 ", None, "103"]})          # float / spaces / empty
    ref = pd.DataFrame({"defect_collection": ["pothole_child_ca"] * 3 + ["other"],
                        "defect_id": ["101", "102", "uuid-c", "103"], "id": ["r1", "r2", "r3", "r9"]})
    cases = case_status(pd.DataFrame({"defect_reference_id": ["r1", "r2", "r3"],
                                      "current_status": ["CLOSED", "ASSIGNED", "CLOSED"]}))
    out = add_status(silver, "pothole_child_ca", ref, cases, silver_key=RisView.STATUS_KEY)
    assert out["defect_reference_id"].tolist()[:2] == ["r1", "r2"]
    assert pd.isna(out["defect_reference_id"].iloc[2])            # matched on display_defect_id, not on id
    assert pd.isna(out["defect_reference_id"].iloc[3])            # 103 is in another defect_collection
    assert out["final_status"].tolist() == ["Closed", "Open", "Open", "Open"]
    assert out["id"].tolist() == silver["id"].tolist()            # silver id kept as it is


def test_missing_status_key_column_is_reported():
    ref = pd.DataFrame({"defect_collection": ["c"], "defect_id": ["1"], "id": ["r1"]})
    cases = case_status(pd.DataFrame({"defect_reference_id": ["r1"], "current_status": ["CLOSED"]}))
    with pytest.raises(KeyError, match="display_defect_id"):
        add_status(pd.DataFrame({"id": ["1"]}), "c", ref, cases, silver_key="display_defect_id")


def test_duplicate_case_rows_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        case_status(pd.DataFrame({"defect_reference_id": ["r1", "r1"], "current_status": ["OPEN", "CLOSED"]}))
