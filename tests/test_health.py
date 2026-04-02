"""Tests for GET /health."""

import pytest


@pytest.mark.anyio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_health_no_auth_required(client):
    """Health endpoint must respond without any auth headers."""
    resp = await client.get("/health", headers={})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_health_response_format(client):
    """Health response must be JSON with a 'status' key."""
    resp = await client.get("/health")
    body = resp.json()
    assert "status" in body
    assert isinstance(body["status"], str)
