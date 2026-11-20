"""RIS trends (new silver logic): averages, PCI and condition categories + DB ids -> ris_trends_data (new surveys appended)."""
from ris_kpi.ris_trends.pipeline import run_ris_trends

__all__ = ["run_ris_trends"]
