"""Tests for the 50 MB upload size limit and 100k row cap (launch gate: error states)."""

import io

import pytest


@pytest.mark.anyio
async def test_upload_exceeds_50mb(auth_client):
    """Files larger than 50 MB must return HTTP 413."""
    # Build a byte string just over the 50 MB limit via mock
    import app.main as main_module

    original = main_module.settings.max_upload_bytes
    main_module.settings.max_upload_bytes = 100  # 100-byte limit for this test

    tiny_oversized = b"age,name\n" + b"1,a\n" * 5  # 59 bytes > 100 byte fake limit? no
    # Actually easier: make a file that is definitely > 100 bytes
    oversized = b"age,income\n" + b"25,50000\n" * 20  # ~180 bytes

    try:
        resp = await auth_client.post(
            "/api/upload",
            files={"file": ("big.csv", oversized, "text/csv")},
        )
    finally:
        main_module.settings.max_upload_bytes = original

    assert resp.status_code == 413
    assert "large" in resp.json()["detail"].lower() or "maximum" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_upload_exactly_at_row_limit_passes(auth_client):
    """A CSV at exactly max_upload_rows should be accepted."""
    import app.main as main_module
    from unittest.mock import patch
    import pandas as pd

    original = main_module.settings.max_upload_rows
    main_module.settings.max_upload_rows = 5

    df = pd.DataFrame({"x": range(5)})  # exactly 5 rows
    buf = io.BytesIO()
    df.to_csv(buf, index=False)

    try:
        with patch("app.main.upload_csv_bytes", return_value="inputs/test.csv"), \
             patch("app.main._infer_schema", return_value=[]):
            resp = await auth_client.post(
                "/api/upload",
                files={"file": ("at_limit.csv", buf.getvalue(), "text/csv")},
            )
    finally:
        main_module.settings.max_upload_rows = original

    # 5 rows at a limit of 5 should succeed (≤ is the check)
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_upload_one_over_row_limit_fails(auth_client):
    """A CSV one row over max_upload_rows should return 422."""
    import app.main as main_module
    from unittest.mock import patch
    import pandas as pd

    original = main_module.settings.max_upload_rows
    main_module.settings.max_upload_rows = 5

    df = pd.DataFrame({"x": range(6)})  # 6 rows > 5 limit
    buf = io.BytesIO()
    df.to_csv(buf, index=False)

    try:
        with patch("app.main.upload_csv_bytes", return_value="inputs/test.csv"):
            resp = await auth_client.post(
                "/api/upload",
                files={"file": ("over_limit.csv", buf.getvalue(), "text/csv")},
            )
    finally:
        main_module.settings.max_upload_rows = original

    assert resp.status_code == 422
    assert "row" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_upload_malformed_binary_returns_400(auth_client):
    """Non-parseable binary content must return HTTP 400."""
    garbage = b"\x00\x01\x02\xff\xfe garbage"
    resp = await auth_client.post(
        "/api/upload",
        files={"file": ("bad.csv", garbage, "text/csv")},
    )
    assert resp.status_code == 400
    assert "parse" in resp.json()["detail"].lower() or "csv" in resp.json()["detail"].lower()
