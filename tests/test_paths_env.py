"""S3 locations come from .env: S3_DB_TABLES_DIR, S3_SILVER_INPUT_DIR, S3_GOLD_OUTPUT_DIR."""
import os
import subprocess
import sys

from ris_kpi import config


def test_env_s3(monkeypatch):
    monkeypatch.setenv("X_DIR", "s3://bucket/a/b/")
    assert config.env_s3("X_DIR", "s3://other") == "s3://bucket/a/b"              # no trailing '/'
    monkeypatch.setenv("X_DIR", "")
    assert config.env_s3("X_DIR", "s3://other") == "s3://other"                    # empty = default


def test_paths_from_environment():
    """A fresh interpreter (config is read at import): the 3 keys set every input / output location."""
    env = {**os.environ, "RIS_KPI_STORAGE": "s3", "S3_DB_TABLES_DIR": "s3://b/db", "S3_SILVER_INPUT_DIR": "s3://b/silver",
           "S3_GOLD_OUTPUT_DIR": "s3://b/gold/"}
    code = ("from ris_kpi import config as c; print(c.DB_TABLES_DIR, c.SILVER_INPUT_DIR, c.RisView.SILVER_STATUS_ROOT, "
            "c.RisView.INTERMEDIATE_ROOT, c.RisView.KPI_OUTPUT_ROOT, c.RisTrends.OUTPUT_ROOT, c.LATEST_FOLDER)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.split()
    assert out == ["s3://b/db", "s3://b/silver"] + ["s3://b/gold"] * 4 + ["Latest"]
