"""Unit tests for app/storage.py — coverage target: >= 80% (SAU-111).

Strategy:
  - Patch `app.storage.boto3.client` to return a MagicMock
  - Reset `app.storage._client = None` in setUp to avoid singleton pollution
  - Patch `app.storage.settings` for config-dependent branches
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
from botocore.exceptions import ClientError


def _make_client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test"}}, "op")


# ---------------------------------------------------------------------------
# get_s3_client
# ---------------------------------------------------------------------------


class TestGetS3Client:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_creates_client_without_endpoint_url(self):
        with patch("app.storage.settings") as mock_settings, \
             patch("app.storage.boto3.client") as mock_boto:
            mock_settings.s3_endpoint_url = None
            mock_settings.aws_access_key_id = "key"
            mock_settings.aws_secret_access_key = "secret"
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            from app.storage import get_s3_client
            result = get_s3_client()

            mock_boto.assert_called_once_with(
                "s3",
                aws_access_key_id="key",
                aws_secret_access_key="secret",
            )
            assert result is mock_client

    def test_creates_client_with_endpoint_url(self):
        with patch("app.storage.settings") as mock_settings, \
             patch("app.storage.boto3.client") as mock_boto:
            mock_settings.s3_endpoint_url = "http://minio:9000"
            mock_settings.aws_access_key_id = "key"
            mock_settings.aws_secret_access_key = "secret"
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            from app.storage import get_s3_client
            result = get_s3_client()

            mock_boto.assert_called_once_with(
                "s3",
                aws_access_key_id="key",
                aws_secret_access_key="secret",
                endpoint_url="http://minio:9000",
            )
            assert result is mock_client

    def test_returns_cached_singleton_on_second_call(self):
        with patch("app.storage.settings") as mock_settings, \
             patch("app.storage.boto3.client") as mock_boto:
            mock_settings.s3_endpoint_url = None
            mock_settings.aws_access_key_id = "key"
            mock_settings.aws_secret_access_key = "secret"
            mock_boto.return_value = MagicMock()

            from app.storage import get_s3_client
            first = get_s3_client()
            second = get_s3_client()

            assert mock_boto.call_count == 1
            assert first is second


# ---------------------------------------------------------------------------
# ensure_bucket
# ---------------------------------------------------------------------------


class TestEnsureBucket:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def _patched(self, mock_client):
        """Patch get_s3_client to return mock_client."""
        return patch("app.storage.get_s3_client", return_value=mock_client)

    def test_head_bucket_success_no_create(self):
        mock_client = MagicMock()
        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import ensure_bucket
            ensure_bucket()

        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_client.create_bucket.assert_not_called()

    def test_head_bucket_404_creates_bucket(self):
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = _make_client_error("404")
        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import ensure_bucket
            ensure_bucket()

        mock_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_head_bucket_no_such_bucket_creates_bucket(self):
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = _make_client_error("NoSuchBucket")
        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import ensure_bucket
            ensure_bucket()

        mock_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_head_bucket_other_error_reraises(self):
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = _make_client_error("403")
        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import ensure_bucket
            with pytest.raises(ClientError) as exc_info:
                ensure_bucket()

        assert exc_info.value.response["Error"]["Code"] == "403"
        mock_client.create_bucket.assert_not_called()


# ---------------------------------------------------------------------------
# upload_csv_bytes
# ---------------------------------------------------------------------------


class TestUploadCsvBytes:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_puts_object_with_correct_args_and_returns_key(self):
        mock_client = MagicMock()
        fake_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        data = b"col1,col2\n1,2\n"

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings, \
             patch("app.storage.uuid.uuid4", return_value=fake_uuid):
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import upload_csv_bytes
            result = upload_csv_bytes(data, "outputs")

        expected_key = f"outputs/{fake_uuid}.csv"
        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key=expected_key,
            Body=data,
            ContentType="text/csv",
        )
        assert result == expected_key


# ---------------------------------------------------------------------------
# download_object_bytes
# ---------------------------------------------------------------------------


class TestDownloadObjectBytes:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_returns_body_read(self):
        mock_client = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"some bytes"
        mock_client.get_object.return_value = {"Body": mock_body}

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import download_object_bytes
            result = download_object_bytes("outputs/test.csv")

        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="outputs/test.csv"
        )
        assert result == b"some bytes"


# ---------------------------------------------------------------------------
# upload_dataframe_as_csv
# ---------------------------------------------------------------------------


class TestUploadDataframeAsCsv:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_serializes_df_and_calls_upload_csv_bytes(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        expected_bytes = df.to_csv(index=False).encode()

        with patch("app.storage.upload_csv_bytes", return_value="outputs/df.csv") as mock_upload:
            from app.storage import upload_dataframe_as_csv
            result = upload_dataframe_as_csv(df, "outputs")

        mock_upload.assert_called_once_with(expected_bytes, "outputs")
        assert result == "outputs/df.csv"


# ---------------------------------------------------------------------------
# generate_presigned_url
# ---------------------------------------------------------------------------


class TestGeneratePresignedUrl:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_returns_url_with_explicit_expiry(self):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/signed"

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            mock_settings.s3_presigned_url_expiry = 86400
            from app.storage import generate_presigned_url
            result = generate_presigned_url("outputs/test.csv", expiry=3600)

        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "test-bucket", "Key": "outputs/test.csv"},
            ExpiresIn=3600,
        )
        assert result == "https://s3.example.com/signed"

    def test_returns_url_with_default_expiry_from_settings(self):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/signed"

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            mock_settings.s3_presigned_url_expiry = 86400
            from app.storage import generate_presigned_url
            result = generate_presigned_url("outputs/test.csv")

        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "test-bucket", "Key": "outputs/test.csv"},
            ExpiresIn=86400,
        )
        assert result == "https://s3.example.com/signed"


# ---------------------------------------------------------------------------
# delete_object
# ---------------------------------------------------------------------------


class TestDeleteObject:
    def setup_method(self):
        import app.storage
        app.storage._client = None

    def teardown_method(self):
        import app.storage
        app.storage._client = None

    def test_calls_delete_object(self):
        mock_client = MagicMock()

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import delete_object
            delete_object("outputs/test.csv")

        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="outputs/test.csv"
        )

    def test_swallows_client_error(self):
        mock_client = MagicMock()
        mock_client.delete_object.side_effect = _make_client_error("NoSuchKey")

        with patch("app.storage.get_s3_client", return_value=mock_client), \
             patch("app.storage.settings") as mock_settings:
            mock_settings.s3_bucket_name = "test-bucket"
            from app.storage import delete_object
            # Should not raise
            delete_object("outputs/missing.csv")
