"""Output folders of every saved table (silver status, final_intermediate, ris_view_data, ris_trends_data), on S3 or
on the local disk (a root is an S3 URI or a local path):

    <root>/year=YYYY/month=MM/day=DD/<name>.csv   one folder per run date (a second run on the same day overwrites)
    <root>/<LATEST_FOLDER>/<name>.csv             copy of the newest run, overwritten by every run
                                                  (config.LATEST_FOLDER: 'Latest' on S3, 'latest' locally)

A step reads the newest dated copy of a table (find_latest: the latest run date that has the file; the latest folder
only when there is no dated copy), changes it and saves it under the current run date + latest (save_run).

On S3, finding the dated copies lists <root>/ (s3:ListBucket on that prefix). Without that permission the latest/
copy is used (logged): every run writes it together with the dated copy, so it is the same table.
"""
import io
import logging
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath

import pandas as pd

from ris_kpi import config
from ris_kpi.common import s3
from ris_kpi.common.io import exists, join, local_file

log = logging.getLogger(__name__)

_PART = re.compile(r"^(year|month|day)=(\d+)$")


def dated_dir(root, day):
    return join(root, f"year={day.year}/month={day.month:02d}/day={day.day:02d}")


def latest_location(root, name):
    return join(root, f"{config.LATEST_FOLDER}/{name}.csv")


def parent_of(location):
    """Folder of a file location (S3 URI or local path), same kind as the location."""
    if s3.is_s3_uri(location):
        return str(location).rsplit("/", 1)[0]
    return Path(location).parent


def _parts(location):
    return PurePosixPath(str(location)).parts if s3.is_s3_uri(location) else Path(location).parts


def folder_date(location):
    """date of a .../year=YYYY/month=M/day=D/<file> location (zero padding optional), None when it is not one."""
    parts = [_PART.match(p) for p in _parts(location)[-4:-1]]
    if len(parts) != 3 or not all(parts) or [m.group(1) for m in parts] != ["year", "month", "day"]:
        return None
    try:
        return date(*(int(m.group(2)) for m in parts))
    except ValueError:
        return None


def _saved_copies(root, name):
    """Every <root>/year=*/month=*/day=*/<name>.csv location, directly under root (other folders in a shared root are
    ignored). S3: listed; None when listing is not allowed."""
    if s3.is_s3_uri(root):
        prefix = str(root).rstrip("/") + "/"
        pattern = re.compile(rf"^year=[^/]+/month=[^/]+/day=[^/]+/{re.escape(name)}\.csv$")
        try:
            return [uri for uri in s3.list_uris(root) if pattern.match(uri[len(prefix):])]
        except Exception as e:
            if s3.is_access_denied(e):
                log.warning("No s3:ListBucket on %s — the %s/ copy of %s is used", root,
                            config.LATEST_FOLDER, name)
                return None
            raise
    return list(Path(root).glob(f"year=*/month=*/day=*/{name}.csv"))


def find_latest(root, name):
    """Newest dated <root>/year=/month=/day=/<name>.csv; else <root>/<LATEST_FOLDER>/<name>.csv; else None."""
    dated = [(d, str(loc), loc) for loc in (_saved_copies(root, name) or []) if (d := folder_date(loc))]
    if dated:
        return max(dated)[2]
    latest = latest_location(root, name)
    return latest if exists(latest) else None


def require_latest(root, name):
    """find_latest, raising when there is no saved copy (a step reads the output of the step before it)."""
    location = find_latest(root, name)
    if location is None:
        raise FileNotFoundError(f"No saved {name}.csv in {root} (year=/month=/day= or latest) — run the step that "
                                "writes it first (`ris-kpi ris-view` runs all steps)")
    return location


def read_text(location, empty_as_null=True):
    """CSV with every column as text; empty_as_null=False keeps empty cells as '' (written back unchanged)."""
    with local_file(location) as path:
        return pd.read_csv(path, dtype=str, keep_default_na=empty_as_null, na_filter=empty_as_null,
                           encoding="utf-8-sig", low_memory=False)


def as_text(df, empty_as_null=True):
    """df as it reads back from its CSV (every column text), so it compares exactly with a saved table."""
    return read_text_buffer(df.to_csv(index=False), empty_as_null)


def read_text_buffer(text, empty_as_null=True):
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=empty_as_null, na_filter=empty_as_null,
                       low_memory=False)


def load_latest(root, name, empty_as_null=True):
    """(newest saved copy of <name> as text, its location); (None, None) when there is none."""
    location = find_latest(root, name)
    if location is None:
        log.info("%s: no saved copy in %s", name, root)
        return None, None
    df = read_text(location, empty_as_null)
    log.info("%s: latest saved copy %s — %s rows", name, location, f"{len(df):,}")
    return df, location


def save_run(df, root, name, run_date):
    """<root>/year=/month=/day=<run_date>/<name>.csv, then copied to <root>/<LATEST_FOLDER>/<name>.csv. Returns the dated
    location."""
    out = join(dated_dir(root, run_date), f"{name}.csv")
    latest = latest_location(root, name)
    if s3.is_s3_uri(out):
        with tempfile.TemporaryDirectory(prefix="ris_kpi_") as tmp:
            path = Path(tmp) / f"{name}.csv"
            df.to_csv(path, index=False)
            s3.upload(path, out)
            s3.upload(path, latest)
    else:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        Path(latest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out, latest)
    log.info("Saved %s — %s rows (+ %s)", out, f"{len(df):,}", parent_of(latest))
    return out
