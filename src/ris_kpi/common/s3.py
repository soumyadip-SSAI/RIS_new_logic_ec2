"""S3 access (boto3). Credentials come from boto3's usual chain: the EC2 instance role, or `aws configure` locally.

Files are read through a temporary local copy that is deleted right after reading, and written through a temporary
file that is deleted after the upload, so nothing stays on disk (large files are transferred in parallel parts).
"""
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

# Error codes S3 gives for a missing key: 404, or 403 when the role may read objects but not list the bucket
MISSING_CODES = {"404", "NoSuchKey", "403", "AccessDenied"}


def is_s3_uri(location):
    return str(location).startswith("s3://")


def split_s3_uri(uri):
    """'s3://bucket/a/b.csv' -> ('bucket', 'a/b.csv')."""
    if not is_s3_uri(uri):
        raise ValueError(f"Not an S3 URI: {uri}")
    bucket, _, key = str(uri)[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"S3 URI needs a bucket and a key: {uri}")
    return bucket, key


@lru_cache(maxsize=None)
def get_client():
    import boto3  # imported here so local runs never need it
    return boto3.client("s3")


def _error_code(exc):
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def head(uri):
    """Object metadata (LastModified, ContentLength, ...), or None when it does not exist / cannot be seen."""
    bucket, key = split_s3_uri(uri)
    try:
        return get_client().head_object(Bucket=bucket, Key=key)
    except Exception as e:
        if _error_code(e) in MISSING_CODES:
            return None
        raise


def exists(uri):
    return head(uri) is not None


@contextmanager
def downloaded(uri):
    """Yield a temporary local copy of the object; it is deleted when the block ends."""
    bucket, key = split_s3_uri(uri)
    with tempfile.TemporaryDirectory(prefix="ris_kpi_") as tmp:
        path = Path(tmp) / Path(key).name
        try:
            get_client().download_file(bucket, key, str(path))
        except Exception as e:
            if _error_code(e) in MISSING_CODES:
                raise FileNotFoundError(f"{uri} not found (or no read access)") from e
            raise
        yield path


def upload(path, uri):
    bucket, key = split_s3_uri(uri)
    get_client().upload_file(str(path), bucket, key)


def list_uris(prefix_uri):
    """Every object URI under s3://bucket/prefix/ (needs s3:ListBucket on that prefix)."""
    bucket, prefix = split_s3_uri(prefix_uri)
    prefix = prefix.rstrip("/") + "/"
    pages = get_client().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    return [f"s3://{bucket}/{obj['Key']}" for page in pages for obj in page.get("Contents", [])]


def is_access_denied(exc):
    return _error_code(exc) in {"403", "AccessDenied"}
