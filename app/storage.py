"""
MinIO/S3 storage client.

Configurable via S3_ENDPOINT_URL:
  - Set to http://minio:9000 in dev (Docker Compose)
  - Absent in prod → boto3 uses AWS default endpoint
"""

from __future__ import annotations

import io
import uuid

import boto3
from botocore.exceptions import ClientError

from app.config import settings

_client: boto3.client | None = None


def get_s3_client():
    global _client
    if _client is None:
        kwargs = dict(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _client = boto3.client("s3", **kwargs)
    return _client


def ensure_bucket() -> None:
    """Create bucket if it doesn't exist (dev/MinIO helper)."""
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=settings.s3_bucket_name)
        else:
            raise


def upload_csv_bytes(data: bytes, prefix: str) -> str:
    """Upload raw bytes to S3/MinIO and return the s3_key."""
    key = f"{prefix}/{uuid.uuid4()}.csv"
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=data,
        ContentType="text/csv",
    )
    return key


def download_object_bytes(s3_key: str) -> bytes:
    """Download an object from S3/MinIO and return raw bytes."""
    client = get_s3_client()
    resp = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return resp["Body"].read()


def upload_dataframe_as_csv(df, prefix: str) -> str:
    """Serialize a pandas DataFrame to CSV and upload; return s3_key."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return upload_csv_bytes(buf.getvalue().encode(), prefix)


def generate_presigned_url(s3_key: str, expiry: int | None = None) -> str:
    """Return a presigned GET URL valid for `expiry` seconds (default: config value)."""
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": s3_key},
        ExpiresIn=expiry or settings.s3_presigned_url_expiry,
    )


def delete_object(s3_key: str) -> None:
    """Delete an object from S3/MinIO."""
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    except ClientError:
        pass  # best-effort cleanup
