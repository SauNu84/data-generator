"""Tests for POST /api/upload."""

import io
from unittest.mock import patch

import pandas as pd
import pytest
import pytest_asyncio


@pytest.mark.anyio
async def test_upload_happy_path(auth_client, sample_csv_bytes):
    with patch("app.main.upload_csv_bytes", return_value="inputs/test-key.csv"):
        resp = await auth_client.post(
            "/api/upload",
            files={"file": ("data.csv", sample_csv_bytes, "text/csv")},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "dataset_id" in body
    assert body["original_filename"] == "data.csv"
    assert body["row_count"] == 10
    assert len(body["columns"]) == 3
    cols = {c["name"] for c in body["columns"]}
    assert cols == {"age", "income", "category"}


@pytest.mark.anyio
async def test_upload_empty_csv(auth_client):
    empty = b"col1,col2\n"
    with patch("app.main.upload_csv_bytes", return_value="inputs/key.csv"):
        resp = await auth_client.post(
            "/api/upload",
            files={"file": ("empty.csv", empty, "text/csv")},
        )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_upload_non_csv(auth_client):
    resp = await auth_client.post(
        "/api/upload",
        files={"file": ("data.json", b'{"key":"value"}', "application/json")},
    )
    # Parser will try to read it; may get 400 or succeed weirdly — just must not crash
    assert resp.status_code in (400, 201)


@pytest.mark.anyio
async def test_upload_too_many_rows(auth_client):
    """CSV exceeding 100k row hard cap should return 422."""
    # Build a CSV larger than limit conceptually via mock
    import app.main as main_module

    original_limit = main_module.settings.max_upload_rows
    main_module.settings.max_upload_rows = 5

    big_df = pd.DataFrame({"x": range(10)})
    buf = io.BytesIO()
    big_df.to_csv(buf, index=False)

    with patch("app.main.upload_csv_bytes", return_value="inputs/key.csv"):
        resp = await auth_client.post(
            "/api/upload",
            files={"file": ("big.csv", buf.getvalue(), "text/csv")},
        )

    main_module.settings.max_upload_rows = original_limit
    assert resp.status_code == 422
    assert "row" in resp.json()["detail"].lower()
