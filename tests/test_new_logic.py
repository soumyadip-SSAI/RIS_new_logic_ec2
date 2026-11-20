"""New-logic calculations: intermediate_10m_data flags / statuses, ris_view counts / lengths, ris_trends categories."""
import pandas as pd

from ris_kpi.ris_trends.categories import build_categories
from ris_kpi.ris_trends.kpi_metrics import build_kpis
from ris_kpi.ris_view.intermediate_10m import build_intermediate_10m, ca_only, cracking_10m, line_defect_10m
from ris_kpi.ris_view.view_table import build_view, pavement_group

SURVEY = {"upc": "N/1", "zone": "B", "phase": "1", "cycle": "1", "source_system": "NSV", "detected_on": "2026-01-05"}


def rows(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame({**{k: [v] * n for k, v in SURVEY.items()}, **cols})


# --- intermediate_10m_data ---

def line_tables(parent_breach, ca_breach, status):
    parent = rows(parent_id=["P1"], breach_flag=[parent_breach], final_status=[status])
    ca = rows(id=["C1", "C2"], parent_id=["P1", "P1"], lane=["L1", "L1"], end_chainage=["20", "40"],
              breach_flag=ca_breach)
    child = rows(parent_id=["P1"] * 4, lane=["L1"] * 4, start_chainage=["0", "10", "20", "30"],
                 end_chainage=["10", "20", "30", "40"], breach_flag=["0"] * 4, pavement_type=["Asphalt"] * 4,
                 threshold_name=["As per CA"] * 4)
    return parent, ca, child


def test_intermediate_10m_flags_and_statuses():
    parent, ca, child = line_tables("1", ["1", "0"], "Closed")
    rough = line_defect_10m(parent, ca, ca_only(child, "As per IRC", "r"), "roughness")
    rut = line_defect_10m(*line_tables("0", ["1", "1"], "Open"), "rutting")
    crack_ca = rows(id=["K1"], lane=["L1"], end_chainage=["40"], breach_flag=["1"], final_status=["Open"])
    crack = cracking_10m(crack_ca, child.drop(columns="parent_id"))
    m = build_intermediate_10m(rough, rut, crack)
    assert m["roughness_ca_id"].tolist() == ["C1", "C1", "C2", "C2"]     # 10m end <= CA end, nearest
    assert m["roughness_final_flag"].tolist() == [1, 1, 0, 0]           # parent AND CA breach
    assert m["rutting_final_flag"].tolist() == [0, 0, 0, 0]             # parent not breached
    assert m["cracking_final_flag"].tolist() == [1, 1, 1, 1]
    assert m["roughness_status"].tolist() == ["Closed"] * 4
    # Total: Closed only when every flagged defect of the row is Closed (cracking is Open here)
    assert m["total_status"].tolist() == ["Open"] * 4 and m["length_m"].tolist() == [10.0] * 4


def test_ca_only_drops_irc_rows():
    df = pd.DataFrame({"threshold_name": ["As per CA", "As per IRC", "As per CA & IRC"]})
    assert ca_only(df, "As per IRC", "t")["threshold_name"].tolist() == ["As per CA", "As per CA & IRC"]


# --- ris_view_data ---

def test_view_counts_lengths_and_statuses():
    status = lambda **c: rows(**c)
    tables = {
        "Roughness": status(id=["a", "b", "c"], breach_flag=["1", "1", "0"], final_status=["Open", "Closed", "Open"],
                            pavement_type=["Asphalt"] * 3),
        "Rutting": status(id=["a"], breach_flag=["0"], final_status=["Open"], pavement_type=["Asphalt"]),
        "Cracking": status(id=["a", "b"], breach_flag=["1", "1"], final_status=["Open", "Open"],
                           pavement_type=["Cement Concrete", "Hybrid"]),
        "Pothole": status(id=["a"], breach_flag=["1"], final_status=["Closed"], pavement_type=["Asphalt"]),
        "Ravelling": status(id=["a"], breach_flag=["0"], final_status=["Open"], pavement_type=["Asphalt"]),
    }
    m10 = rows(lane=["L1", "L1"], start_chainage=[0.0, 10.0], end_chainage=[10.0, 20.0], length_m=[10.0, 10.0],
               pavement_type=["Cement Concrete", "Cement Concrete"],
               roughness_final_flag=[1, 1], roughness_status=["Open", "Closed"],
               rutting_final_flag=[0, 0], rutting_status=["Open", "Open"],
               cracking_final_flag=[1, 0], cracking_status=["Open", "Open"],
               final_flag=[1, 1], total_status=["Open", "Closed"])
    view = build_view(tables, m10, pd.DataFrame({"upc": ["N/1"], "upc_length_km": [1.0], "upc_lane_length_km": [4.0]}))
    get = lambda d, s, p=None: view[(view.defect_type == d) & (view.status == s)
                                    & (view.pavement_type.isna() if p is None else view.pavement_type == p)].iloc[0]
    assert get("Roughness", "open").total_defects_ca == 1 and get("Roughness", "close").total_defects_ca == 1
    assert get("Roughness", "open").affected_length_ca_m == 10 and get("Roughness", "close").affected_length_ca_m == 10
    assert get("Cracking", "open", "Cement Concrete").total_defects_ca == 1
    assert get("Cracking", "open", "Asphalt").total_defects_ca == 1     # Hybrid counted as Asphalt
    assert get("Cracking", "open", "Cement Concrete").affected_length_ca_m == 10
    assert get("Pothole", "close", "Asphalt").total_defects_ca == 1
    assert get("Total", "open").total_defects_ca == 3 and get("Total", "close").total_defects_ca == 2
    assert get("Total", "open").affected_length_ca_m == 10 and get("Total", "close").affected_length_ca_km == 0.01
    assert set(view.status) == {"open", "close"} and (view.upc_lane_length_km == 4.0).all()


def test_pavement_group():
    pav = pd.Series(["Cement Concrete", " cememt concrete ", "Hybrid", None])
    assert pavement_group("Cracking", pav).tolist() == ["Cement Concrete", "Cement Concrete", "Asphalt", "Asphalt"]
    assert pavement_group("Rutting", pav).tolist() == ["", "", "", ""]


# --- ris_trends_data ---

def test_trends_categories_from_threshold_value():
    parent = lambda mv: rows(threshold_name=["As per CA"] * 2, metric_value=mv, threshold_value=["2750.0"] * 2,
                             start_chainage=["0", "1000"], end_chainage=["1000", "1240"], pci=["80", "90"],
                             pavement_type=["Asphalt"] * 2)
    rough, rut = parent(["2000", "3000"]), parent(["1", "2"]).assign(threshold_value="10")
    kpis = build_kpis(rough, rut, rough, "As per CA")
    assert kpis[["roughness_average_mm_km", "average_rutting_depth_mm", "pci_value"]].iloc[0].tolist() == [2500, 1.5, 85]
    cat = build_categories(rough, rut, "As per CA").sort_values("row_rank")
    assert cat["roughness_ca_category"].tolist()[:2] == ["Good", "Poor"] and pd.isna(cat["roughness_ca_category"].iloc[2])
    assert cat["roughness_ca_threshold"].tolist()[:2] == ["(<= 2750 mm/km)", "(> 2750 mm/km)"]
    assert cat["lane_length_roughness_ca"].tolist()[:2] == [1000.0, 240.0]
    assert cat["rutting_ca_threshold"].iloc[0] == "(<= 10 mm)"
    assert cat["roughness_irc_category"].tolist() == ["Good", "Poor", "Fair"]
    assert cat["roughness_irc_threshold"].isna().all() and cat["lane_length_roughness_irc"].isna().all()
