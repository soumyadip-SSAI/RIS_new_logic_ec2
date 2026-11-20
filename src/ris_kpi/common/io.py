"""Reading table files (.parquet / .csv / .xlsx) and writing CSV outputs, on S3 or on the local disk.

A location is an S3 URI ('s3://bucket/dir/file.csv') or a local path; every function here accepts both, so the
pipelines do not care where the data lives (config.STORAGE decides).
"""
import logging
import tempfile
from contextlib import contextmanager
from functools import partial
from pathlib import Path, PurePosixPath

import pandas as pd

from ris_kpi.common import s3

log = logging.getLogger(__name__)

_READERS = {
    ".parquet": pd.read_parquet,
    ".csv": partial(pd.read_csv, low_memory=False),
    ".xlsx": pd.read_excel,
    ".xls": pd.read_excel,
}
# keyword each reader uses to read only some columns
_COLUMNS_ARG = {".parquet": "columns", ".csv": "usecols", ".xlsx": "usecols", ".xls": "usecols"}


# --- locations (S3 URI or local path) ---

def join(directory, filename):
    """<directory>/<filename> as an S3 URI or a local Path, like directory."""
    if s3.is_s3_uri(directory):
        return f"{str(directory).rstrip('/')}/{filename}"
    return Path(directory) / filename


def name_of(location):
    """File name of a location ('ris_view_dw.csv')."""
    return PurePosixPath(str(location)).name if s3.is_s3_uri(location) else Path(location).name


def suffix_of(location):
    return PurePosixPath(name_of(location)).suffix.lower()


def exists(location):
    return s3.exists(location) if s3.is_s3_uri(location) else Path(location).exists()


@contextmanager
def local_file(location):
    """A local path to read the location from (a temporary copy for S3, deleted afterwards)."""
    if s3.is_s3_uri(location):
        with s3.downloaded(location) as path:
            yield path
    else:
        if not Path(location).exists():
            raise FileNotFoundError(f"File not found: {location}")
        yield Path(location)


# --- reading ---

def read_table_file(location, columns=None):
    suffix = suffix_of(location)
    try:
        reader = _READERS[suffix]
    except KeyError:
        raise ValueError(f"Unsupported file type '{suffix}' for {location}") from None
    with local_file(location) as path:
        return reader(path, **({_COLUMNS_ARG[suffix]: columns} if columns else {}))


def find_table_file(directory, name):
    """Return <directory>/<name>.<ext> for the first supported extension that exists."""
    for ext in _READERS:
        candidate = join(directory, f"{name}{ext}")
        if exists(candidate):
            return candidate
    raise FileNotFoundError(f"No {name}.(parquet|csv|xlsx) found in {directory}")


def read_text_csv(location, empty_as_null=False):
    """CSV with every column as text. empty_as_null=False keeps empty cells as '' (round-trips exactly)."""
    with local_file(location) as path:
        return pd.read_csv(path, dtype=str, keep_default_na=empty_as_null, na_filter=empty_as_null,
                           encoding="utf-8-sig")


def load_columns(directory, name, columns):
    """Only the given columns of <directory>/<n>.* (keeps large tables like master_cracking light)."""
    location = find_table_file(directory, name)
    df = read_table_file(location, columns)[columns]
    log.info("Loaded %-30s rows: %s (from %s)", name_of(location), f"{len(df):,}", directory)
    return df


# --- writing ---

def save_csv(df, output_dir, name):
    """Write <output_dir>/<name>.csv (S3 or local); returns its location."""
    location = join(output_dir, f"{name}.csv")
    if s3.is_s3_uri(location):
        with tempfile.TemporaryDirectory(prefix="ris_kpi_") as tmp:
            path = Path(tmp) / f"{name}.csv"
            df.to_csv(path, index=False)
            s3.upload(path, location)
    else:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        df.to_csv(location, index=False)
    log.info("Saved %s — %s rows", location, f"{len(df):,}")
    return location
