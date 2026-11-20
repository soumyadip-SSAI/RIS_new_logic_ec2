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


def test_duplicate_case_rows_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        case_status(pd.DataFrame({"defect_reference_id": ["r1", "r1"], "current_status": ["OPEN", "CLOSED"]}))
