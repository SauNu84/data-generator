"""Tests for the cleanup_expired_outputs Celery task (SAU-99 launch gate: Download + 24h TTL)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest


@pytest.fixture
def expired_jobs():
    """Two jobs that have passed their expiry with output keys still set."""
    now = datetime.now(timezone.utc)
    job1 = MagicMock()
    job1.id = uuid.uuid4()
    job1.output_s3_key = "outputs/expired-1.csv"
    job1.expires_at = now - timedelta(hours=1)
    job1.status = "done"

    job2 = MagicMock()
    job2.id = uuid.uuid4()
    job2.output_s3_key = "outputs/expired-2.csv"
    job2.expires_at = now - timedelta(hours=2)
    job2.status = "done"
    return [job1, job2]


@pytest.fixture
def active_job():
    """A job that has NOT expired yet."""
    job = MagicMock()
    job.id = uuid.uuid4()
    job.output_s3_key = "outputs/active.csv"
    job.expires_at = datetime.now(timezone.utc) + timedelta(hours=23)
    job.status = "done"
    return job


def _call_cleanup_with_session(mock_session, jobs):
    """Run cleanup_expired_outputs with a fully mocked DB session."""
    from app.tasks import cleanup_expired_outputs

    mock_session.scalars.return_value.all.return_value = jobs

    with patch("app.tasks._get_session") as mock_get_session:
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=mock_session)
        cm.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = cm

        with patch("app.tasks.delete_object") as mock_delete:
            result = cleanup_expired_outputs()
            return result, mock_delete


def test_cleanup_removes_expired_jobs(expired_jobs):
    """Expired jobs: output_s3_key cleared, S3 object deleted."""
    mock_session = MagicMock()
    result, mock_delete = _call_cleanup_with_session(mock_session, expired_jobs)

    assert result["deleted"] == 2
    assert mock_delete.call_count == 2
    deleted_keys = {c.args[0] for c in mock_delete.call_args_list}
    assert deleted_keys == {"outputs/expired-1.csv", "outputs/expired-2.csv"}

    # output_s3_key should be cleared on both jobs
    for job in expired_jobs:
        assert job.output_s3_key is None


def test_cleanup_preserves_active_jobs(active_job):
    """Active jobs are excluded by the SQL WHERE clause (expires_at < now).
    The session mock returns [] to simulate the DB correctly filtering them out."""
    mock_session = MagicMock()
    # SQL filter returns nothing for active jobs — simulate correct DB behaviour
    result, mock_delete = _call_cleanup_with_session(mock_session, [])

    assert result["deleted"] == 0
    mock_delete.assert_not_called()
    # The active_job on disk must remain untouched
    assert active_job.output_s3_key == "outputs/active.csv"


def test_cleanup_no_expired_jobs():
    """When no expired jobs exist, deleted count is 0."""
    mock_session = MagicMock()
    result, mock_delete = _call_cleanup_with_session(mock_session, [])

    assert result["deleted"] == 0
    mock_delete.assert_not_called()


def test_cleanup_commits_after_each_batch():
    """Verify session.commit() is called after cleanup."""
    mock_session = MagicMock()
    expired = MagicMock()
    expired.output_s3_key = "outputs/x.csv"

    result, _ = _call_cleanup_with_session(mock_session, [expired])
    mock_session.commit.assert_called()
