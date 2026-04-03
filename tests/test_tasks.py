"""Unit tests for app/tasks.py — generate_synthetic_data + _build_quality_score.

Coverage targets (SAU-110):
  - generate_synthetic_data: happy paths (GaussianCopula, CTGAN), schema overrides,
    PII column dropping, job-not-found, dataset-not-found, SoftTimeLimitExceeded,
    generic exception + retry, MaxRetriesExceededError.
  - generate_multi_table_data: happy path, job/dataset not found, schema mode check,
    SoftTimeLimitExceeded, MaxRetriesExceededError, relationship errors.
  - _build_quality_score: normal path, exception fallback, empty details.
  - cleanup_expired_outputs (supplemental): output_s3_key already-None guard.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

# ─── Shared test data ─────────────────────────────────────────────────────────

SAMPLE_DF = pd.DataFrame(
    {
        "age": [25, 30, 35, 40, 45],
        "income": [50000, 60000, 70000, 80000, 90000],
        "category": ["A", "B", "A", "C", "B"],
    }
)
SAMPLE_CSV = SAMPLE_DF.to_csv(index=False).encode()

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_job():
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = "queued"
    job.output_s3_key = None
    job.quality_score_json = None
    job.error_detail = None
    job.expires_at = None
    job.completed_at = None
    return job


def _make_dataset(schema_json=None):
    ds = MagicMock()
    ds.s3_key = "inputs/test.csv"
    ds.schema_json = schema_json if schema_json is not None else []
    return ds


def _make_session(job, dataset):
    """Return (ctx_manager, session_mock) whose .get() dispatches by model class."""
    from app.models import Dataset, GenerationJob

    session = MagicMock()

    def _get(cls, pk):
        if cls is GenerationJob:
            return job
        if cls is Dataset:
            return dataset
        return None

    session.get.side_effect = _get

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, session


# ─── generate_synthetic_data ─────────────────────────────────────────────────


class TestGenerateSyntheticData:
    """Tests for the generate_synthetic_data Celery task."""

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _run(
        self,
        job,
        dataset,
        model_type="GaussianCopula",
        rows=10,
        overrides=None,
    ):
        """Run generate_synthetic_data.run() with standard mocks applied.

        Returns (result, session_mock).
        """
        import contextlib

        from app.tasks import generate_synthetic_data

        cm, session = _make_session(job, dataset)
        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("app.tasks._get_session", return_value=cm))
            stack.enter_context(patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV))
            stack.enter_context(patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"))
            stack.enter_context(
                patch("app.tasks._build_quality_score", return_value={"overall": 85.0, "columns": []})
            )
            stack.enter_context(patch("app.tasks.GaussianCopulaSynthesizer", return_value=synth))
            stack.enter_context(patch("app.tasks.CTGANSynthesizer", return_value=synth))
            mock_meta_cls = stack.enter_context(patch("app.tasks.Metadata"))
            mock_meta_cls.detect_from_dataframe.return_value = MagicMock()

            result = generate_synthetic_data.run(
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                model_type,
                rows,
                overrides,
            )

        return result, session

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_happy_path_gaussian_copula(self):
        """GaussianCopula happy path: returns done with output_key and quality."""
        job, dataset = _make_job(), _make_dataset()
        result, session = self._run(job, dataset, model_type="GaussianCopula")

        assert result["status"] == "done"
        assert result["output_key"] == "outputs/out.csv"
        assert result["quality"]["overall"] == 85.0
        assert job.status == "done"
        assert job.output_s3_key == "outputs/out.csv"
        assert job.quality_score_json == {"overall": 85.0, "columns": []}
        session.commit.assert_called()

    def test_happy_path_ctgan_selects_ctgan_synthesizer(self):
        """CTGAN model_type uses CTGANSynthesizer, not GaussianCopulaSynthesizer."""
        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, session = _make_session(job, dataset)

        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV), \
             patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"), \
             patch("app.tasks._build_quality_score", return_value={"overall": 72.0, "columns": []}), \
             patch("app.tasks.GaussianCopulaSynthesizer") as MockGaussian, \
             patch("app.tasks.CTGANSynthesizer", return_value=synth) as MockCTGAN, \
             patch("app.tasks.Metadata") as MockMeta:

            MockMeta.detect_from_dataframe.return_value = MagicMock()
            result = generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "CTGAN", 10, None
            )

        assert result["status"] == "done"
        MockCTGAN.assert_called_once()
        MockGaussian.assert_not_called()

    def test_happy_path_sets_expires_at_and_completed_at(self):
        """Completed job has expires_at and completed_at populated."""
        job, dataset = _make_job(), _make_dataset()
        self._run(job, dataset)

        assert job.expires_at is not None
        assert job.completed_at is not None

    # ------------------------------------------------------------------
    # Job / dataset not found
    # ------------------------------------------------------------------

    def test_job_not_found_returns_failed_immediately(self):
        """When the job row is missing, return failed without touching the DB."""
        from app.tasks import generate_synthetic_data

        dataset = _make_dataset()
        cm, session = _make_session(None, dataset)

        with patch("app.tasks._get_session", return_value=cm):
            result = generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
            )

        assert result["status"] == "failed"
        assert "not found" in result["error"].lower()
        session.commit.assert_not_called()

    def test_dataset_not_found_marks_job_failed_and_retries(self):
        """Dataset missing raises ValueError → job.status=failed, retry called."""
        from app.tasks import generate_synthetic_data

        job = _make_job()
        cm, session = _make_session(job, None)

        with patch("app.tasks._get_session", return_value=cm), \
             patch.object(
                 generate_synthetic_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ) as mock_retry:

            result = generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
            )

        assert result["status"] == "failed"
        assert job.status == "failed"
        assert job.error_detail is not None
        assert "Dataset" in job.error_detail
        mock_retry.assert_called_once()
        session.commit.assert_called()

    # ------------------------------------------------------------------
    # Exception handling
    # ------------------------------------------------------------------

    def test_soft_time_limit_marks_failed_and_reraises(self):
        """SoftTimeLimitExceeded sets job to failed and propagates the exception."""
        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, session = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch(
                 "app.tasks.download_object_bytes",
                 side_effect=SoftTimeLimitExceeded(),
             ):
            with pytest.raises(SoftTimeLimitExceeded):
                generate_synthetic_data.run(
                    str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
                )

        assert job.status == "failed"
        assert job.error_detail is not None
        assert "timed out" in job.error_detail.lower() or "soft limit" in job.error_detail.lower()
        assert job.completed_at is not None
        session.commit.assert_called()

    def test_generic_exception_retries_then_returns_failed(self):
        """RuntimeError triggers retry; MaxRetriesExceededError → returns failed dict."""
        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, session = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch(
                 "app.tasks.download_object_bytes",
                 side_effect=RuntimeError("SDV crash"),
             ), \
             patch.object(
                 generate_synthetic_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ) as mock_retry:

            result = generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
            )

        assert result["status"] == "failed"
        assert "SDV crash" in result["error"]
        assert job.status == "failed"
        assert "SDV crash" in job.error_detail
        assert job.completed_at is not None
        mock_retry.assert_called_once()
        session.commit.assert_called()

    def test_generic_exception_retry_propagates_when_retries_remain(self):
        """When retry raises celery.exceptions.Retry (retries remain), it propagates."""
        from celery.exceptions import Retry

        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, _ = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch(
                 "app.tasks.download_object_bytes",
                 side_effect=RuntimeError("transient"),
             ), \
             patch.object(
                 generate_synthetic_data,
                 "retry",
                 side_effect=Retry(),
             ):
            with pytest.raises(Retry):
                generate_synthetic_data.run(
                    str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
                )

        assert job.status == "failed"

    # ------------------------------------------------------------------
    # Schema overrides
    # ------------------------------------------------------------------

    def test_schema_overrides_mapped_to_sdv_sdtypes(self):
        """schema_overrides keys are translated (numeric→numerical) and applied via metadata."""
        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, _ = _make_session(job, dataset)

        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV), \
             patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"), \
             patch("app.tasks._build_quality_score", return_value={"overall": 80.0, "columns": []}), \
             patch("app.tasks.GaussianCopulaSynthesizer", return_value=synth), \
             patch("app.tasks.CTGANSynthesizer", return_value=synth), \
             patch("app.tasks.Metadata") as MockMeta:

            mock_meta = MagicMock()
            MockMeta.detect_from_dataframe.return_value = mock_meta

            result = generate_synthetic_data.run(
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "GaussianCopula",
                10,
                {"age": "numeric", "category": "categorical", "income": "datetime"},
            )

        assert result["status"] == "done"
        mock_meta.update_column.assert_any_call("age", sdtype="numerical")
        mock_meta.update_column.assert_any_call("category", sdtype="categorical")
        mock_meta.update_column.assert_any_call("income", sdtype="datetime")

    def test_schema_override_exception_is_skipped_gracefully(self):
        """If update_column raises for a column, the task continues and succeeds."""
        from app.tasks import generate_synthetic_data

        job, dataset = _make_job(), _make_dataset()
        cm, _ = _make_session(job, dataset)

        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV), \
             patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"), \
             patch("app.tasks._build_quality_score", return_value={"overall": 80.0, "columns": []}), \
             patch("app.tasks.GaussianCopulaSynthesizer", return_value=synth), \
             patch("app.tasks.CTGANSynthesizer", return_value=synth), \
             patch("app.tasks.Metadata") as MockMeta:

            mock_meta = MagicMock()
            mock_meta.update_column.side_effect = ValueError("column not in metadata")
            MockMeta.detect_from_dataframe.return_value = mock_meta

            result = generate_synthetic_data.run(
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "GaussianCopula",
                10,
                {"ghost_col": "numeric"},
            )

        assert result["status"] == "done"

    # ------------------------------------------------------------------
    # PII column dropping
    # ------------------------------------------------------------------

    def test_pii_columns_dropped_before_fitting(self):
        """PII columns in schema_json are stripped from the DataFrame before SDV fitting."""
        from app.tasks import generate_synthetic_data

        job = _make_job()
        dataset = _make_dataset(
            schema_json={
                "pii_columns": [
                    {"column": "email", "pii_type": "email", "detection_method": "regex"}
                ]
            }
        )
        cm, _ = _make_session(job, dataset)

        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV), \
             patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"), \
             patch("app.tasks._build_quality_score", return_value={"overall": 80.0, "columns": []}), \
             patch("app.tasks.GaussianCopulaSynthesizer", return_value=synth), \
             patch("app.tasks.CTGANSynthesizer", return_value=synth), \
             patch("app.tasks.Metadata") as MockMeta, \
             patch("app.pii.drop_pii_columns", return_value=SAMPLE_DF) as mock_drop:

            MockMeta.detect_from_dataframe.return_value = MagicMock()
            result = generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
            )

        assert result["status"] == "done"
        mock_drop.assert_called_once()

    def test_no_pii_columns_skips_drop(self):
        """When schema_json has no pii_columns, drop_pii_columns is never called."""
        from app.tasks import generate_synthetic_data

        job = _make_job()
        dataset = _make_dataset(schema_json={"mode": "single_table", "pii_columns": []})
        cm, _ = _make_session(job, dataset)

        synth = MagicMock()
        synth.sample.return_value = SAMPLE_DF

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=SAMPLE_CSV), \
             patch("app.tasks.upload_dataframe_as_csv", return_value="outputs/out.csv"), \
             patch("app.tasks._build_quality_score", return_value={"overall": 80.0, "columns": []}), \
             patch("app.tasks.GaussianCopulaSynthesizer", return_value=synth), \
             patch("app.tasks.CTGANSynthesizer", return_value=synth), \
             patch("app.tasks.Metadata") as MockMeta, \
             patch("app.pii.drop_pii_columns") as mock_drop:

            MockMeta.detect_from_dataframe.return_value = MagicMock()
            generate_synthetic_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), "GaussianCopula", 10, None
            )

        mock_drop.assert_not_called()


# ─── _build_quality_score ────────────────────────────────────────────────────


class TestBuildQualityScore:
    """Tests for the _build_quality_score helper (directly tested, not via task)."""

    def test_normal_path_computes_overall_and_column_scores(self):
        """With a working evaluate_quality report, returns parsed score dict."""
        from app.tasks import _build_quality_score

        details_df = pd.DataFrame(
            {"Column": ["age", "income"], "Score": [0.9, 0.8]}
        )
        mock_report = MagicMock()
        mock_report.get_score.return_value = 0.85
        mock_report.get_details.return_value = details_df

        with patch("app.tasks.evaluate_quality", return_value=mock_report):
            result = _build_quality_score(SAMPLE_DF, SAMPLE_DF, MagicMock())

        assert result["overall"] == 85.0
        assert len(result["columns"]) == 2
        assert result["columns"][0] == {"column": "age", "score": 0.9}
        assert result["columns"][1] == {"column": "income", "score": 0.8}

    def test_exception_fallback_returns_zero_score(self):
        """When evaluate_quality raises, fallback is overall=0.0, columns=[]."""
        from app.tasks import _build_quality_score

        with patch("app.tasks.evaluate_quality", side_effect=RuntimeError("SDV error")):
            result = _build_quality_score(SAMPLE_DF, SAMPLE_DF, MagicMock())

        assert result["overall"] == 0.0
        assert result["columns"] == []

    def test_empty_details_dataframe_returns_no_columns(self):
        """Empty details DataFrame yields an empty columns list."""
        from app.tasks import _build_quality_score

        mock_report = MagicMock()
        mock_report.get_score.return_value = 0.75
        mock_report.get_details.return_value = pd.DataFrame()

        with patch("app.tasks.evaluate_quality", return_value=mock_report):
            result = _build_quality_score(SAMPLE_DF, SAMPLE_DF, MagicMock())

        assert result["overall"] == 75.0
        assert result["columns"] == []

    def test_none_details_returns_no_columns(self):
        """None returned from get_details yields empty columns list."""
        from app.tasks import _build_quality_score

        mock_report = MagicMock()
        mock_report.get_score.return_value = 0.60
        mock_report.get_details.return_value = None

        with patch("app.tasks.evaluate_quality", return_value=mock_report):
            result = _build_quality_score(SAMPLE_DF, SAMPLE_DF, MagicMock())

        assert result["overall"] == 60.0
        assert result["columns"] == []


# ─── cleanup_expired_outputs (supplemental) ───────────────────────────────────


def _run_cleanup(jobs):
    """Run cleanup_expired_outputs with a mocked session returning `jobs`."""
    from app.tasks import cleanup_expired_outputs

    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = jobs

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)

    with patch("app.tasks._get_session", return_value=cm), \
         patch("app.tasks.delete_object") as mock_delete:
        result = cleanup_expired_outputs()
    return result, mock_delete, mock_session


def test_cleanup_job_with_null_s3_key_skipped():
    """A job returned by the query with output_s3_key=None is skipped (not deleted)."""
    job = MagicMock()
    job.output_s3_key = None

    result, mock_delete, _ = _run_cleanup([job])

    assert result["deleted"] == 0
    mock_delete.assert_not_called()


def test_cleanup_mixed_null_and_set_keys():
    """Only jobs with a non-null output_s3_key are deleted."""
    job_with_key = MagicMock()
    job_with_key.output_s3_key = "outputs/to-delete.csv"

    job_null_key = MagicMock()
    job_null_key.output_s3_key = None

    result, mock_delete, _ = _run_cleanup([job_with_key, job_null_key])

    assert result["deleted"] == 1
    mock_delete.assert_called_once_with("outputs/to-delete.csv")
    assert job_with_key.output_s3_key is None


# ─── generate_multi_table_data ────────────────────────────────────────────────


def _make_multi_table_zip(tables: dict[str, "pd.DataFrame"] | None = None) -> bytes:
    """Build an in-memory ZIP of CSV files for multi-table tests."""
    import io as _io
    import zipfile

    if tables is None:
        tables = {
            "users": pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]}),
            "orders": pd.DataFrame({"order_id": [10, 20], "user_id": [1, 2], "amount": [100.0, 200.0]}),
        }
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, df in tables.items():
            zf.writestr(f"{name}.csv", df.to_csv(index=False))
    return buf.getvalue()


def _make_multi_table_dataset(mode: str = "multi_table", tables=None, relationships=None):
    ds = MagicMock()
    ds.s3_key = "inputs/test-multi.zip"
    ds.schema_json = {
        "mode": mode,
        "tables": tables or ["users", "orders"],
        "relationships": relationships or [],
    }
    return ds


def _run_multi(
    job,
    dataset,
    zip_bytes=None,
    scale_factor=1.0,
    *,
    patch_upload_key="outputs-multi/out.zip",
    synthetic_tables=None,
    relationship_error=False,
):
    """Run generate_multi_table_data.run() with standard mocks.

    Because ``upload_csv_bytes`` is not imported into app.tasks at module level,
    we inject it into the module namespace before calling and remove it after.
    """
    import contextlib
    import app.tasks as tasks_module
    from app.tasks import generate_multi_table_data

    if zip_bytes is None:
        zip_bytes = _make_multi_table_zip()
    if synthetic_tables is None:
        synthetic_tables = {
            "users": pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]}),
            "orders": pd.DataFrame({"order_id": [10, 20], "user_id": [1, 2], "amount": [100.0, 200.0]}),
        }

    cm, session = _make_session(job, dataset)

    mock_synth = MagicMock()
    mock_synth.sample.return_value = synthetic_tables

    mock_meta = MagicMock()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("app.tasks._get_session", return_value=cm))
        stack.enter_context(patch("app.tasks.download_object_bytes", return_value=zip_bytes))
        mock_upload = stack.enter_context(
            patch.object(tasks_module, "upload_csv_bytes", create=True, return_value=patch_upload_key)
        )
        stack.enter_context(
            patch("app.tasks.evaluate_quality", return_value=MagicMock(get_score=MagicMock(return_value=0.85)))
        )

        # Patch multi-table SDV classes via their lazy import paths
        hma_cls = stack.enter_context(
            patch("sdv.multi_table.HMASynthesizer", return_value=mock_synth)
        )
        multi_meta_cls = stack.enter_context(patch("sdv.metadata.MultiTableMetadata", return_value=mock_meta))

        if relationship_error:
            mock_meta.add_relationship.side_effect = ValueError("bad relationship")

        result = generate_multi_table_data.run(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            scale_factor,
        )

    return result, session, mock_upload


class TestGenerateMultiTableData:
    """Tests for the generate_multi_table_data Celery task."""

    def test_happy_path_returns_done_with_output_key(self):
        """Happy path: returns done, uploads ZIP, sets job fields."""
        job = _make_job()
        dataset = _make_multi_table_dataset()

        result, session, mock_upload = _run_multi(job, dataset)

        assert result["status"] == "done"
        assert result["output_key"] == "outputs-multi/out.zip"
        assert result["quality"]["overall"] >= 0
        assert job.status == "done"
        assert job.output_s3_key == "outputs-multi/out.zip"
        assert job.expires_at is not None
        assert job.completed_at is not None
        mock_upload.assert_called_once()
        session.commit.assert_called()

    def test_job_not_found_returns_failed_immediately(self):
        """Missing job row returns failed without touching the DB."""
        from app.tasks import generate_multi_table_data

        dataset = _make_multi_table_dataset()
        cm, session = _make_session(None, dataset)

        with patch("app.tasks._get_session", return_value=cm):
            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "failed"
        assert "not found" in result["error"].lower()
        session.commit.assert_not_called()

    def test_dataset_not_found_marks_failed_and_retries(self):
        """Missing dataset raises ValueError → job.status=failed, retry called."""
        from app.tasks import generate_multi_table_data

        job = _make_job()
        cm, session = _make_session(job, None)

        with patch("app.tasks._get_session", return_value=cm), \
             patch.object(
                 generate_multi_table_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ) as mock_retry:

            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "failed"
        assert job.status == "failed"
        mock_retry.assert_called_once()

    def test_non_multi_table_mode_raises_and_marks_failed(self):
        """Dataset without mode=multi_table raises ValueError → job.status=failed."""
        from app.tasks import generate_multi_table_data

        job = _make_job()
        dataset = _make_multi_table_dataset(mode="single_table")
        cm, session = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch.object(
                 generate_multi_table_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ):

            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "failed"
        assert job.status == "failed"
        assert "multi-table" in job.error_detail.lower() or "multi_table" in job.error_detail.lower()

    def test_empty_zip_raises_and_marks_failed(self):
        """A ZIP with no CSV files raises ValueError and marks job failed."""
        import io as _io
        import zipfile

        from app.tasks import generate_multi_table_data

        empty_zip = _io.BytesIO()
        with zipfile.ZipFile(empty_zip, "w"):
            pass  # no files

        job = _make_job()
        dataset = _make_multi_table_dataset()
        cm, session = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=empty_zip.getvalue()), \
             patch.object(
                 generate_multi_table_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ):

            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "failed"
        assert job.status == "failed"

    def test_soft_time_limit_marks_failed_and_reraises(self):
        """SoftTimeLimitExceeded sets job to failed and re-raises."""
        from app.tasks import generate_multi_table_data

        job = _make_job()
        dataset = _make_multi_table_dataset()
        cm, session = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch(
                 "app.tasks.download_object_bytes",
                 side_effect=SoftTimeLimitExceeded(),
             ):
            with pytest.raises(SoftTimeLimitExceeded):
                generate_multi_table_data.run(
                    str(uuid.uuid4()), str(uuid.uuid4()), 1.0
                )

        assert job.status == "failed"
        assert "timed out" in job.error_detail.lower() or "soft" in job.error_detail.lower()
        assert job.completed_at is not None
        session.commit.assert_called()

    def test_max_retries_exceeded_returns_failed_dict(self):
        """After exhausting retries, returns failed dict (not exception)."""
        from app.tasks import generate_multi_table_data

        job = _make_job()
        dataset = _make_multi_table_dataset()
        cm, _ = _make_session(job, dataset)

        with patch("app.tasks._get_session", return_value=cm), \
             patch(
                 "app.tasks.download_object_bytes",
                 side_effect=RuntimeError("HMA crash"),
             ), \
             patch.object(
                 generate_multi_table_data,
                 "retry",
                 side_effect=MaxRetriesExceededError(),
             ):

            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "failed"
        assert "HMA crash" in result["error"]

    def test_relationship_errors_are_skipped_gracefully(self):
        """Bad relationships log a warning but do not abort the task."""
        job = _make_job()
        dataset = _make_multi_table_dataset(
            relationships=[
                {
                    "parent_table": "users",
                    "parent_primary_key": "id",
                    "child_table": "orders",
                    "child_foreign_key": "user_id",
                }
            ]
        )

        result, session, _ = _run_multi(job, dataset, relationship_error=True)

        assert result["status"] == "done"
        assert job.status == "done"

    def test_scale_factor_passed_to_sample(self):
        """scale_factor is forwarded to synthesizer.sample(scale=...)."""
        import app.tasks as tasks_module
        from app.tasks import generate_multi_table_data

        job = _make_job()
        dataset = _make_multi_table_dataset()
        cm, session = _make_session(job, dataset)

        synthetic_tables = {
            "users": pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]}),
        }
        mock_synth = MagicMock()
        mock_synth.sample.return_value = synthetic_tables

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=_make_multi_table_zip()), \
             patch.object(tasks_module, "upload_csv_bytes", create=True, return_value="key"), \
             patch("app.tasks.evaluate_quality", return_value=MagicMock(get_score=MagicMock(return_value=0.8))), \
             patch("sdv.multi_table.HMASynthesizer", return_value=mock_synth), \
             patch("sdv.metadata.MultiTableMetadata", return_value=MagicMock()):

            generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 2.5
            )

        mock_synth.sample.assert_called_once_with(scale=2.5)

    def test_per_table_quality_score_failure_falls_back_to_zero(self):
        """If evaluate_quality raises for a table, that table gets quality=0.0 and task still succeeds."""
        import app.tasks as tasks_module
        from app.tasks import generate_multi_table_data

        job = _make_job()
        dataset = _make_multi_table_dataset()
        cm, session = _make_session(job, dataset)

        synthetic_tables = {
            "users": pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]}),
        }
        mock_synth = MagicMock()
        mock_synth.sample.return_value = synthetic_tables

        with patch("app.tasks._get_session", return_value=cm), \
             patch("app.tasks.download_object_bytes", return_value=_make_multi_table_zip()), \
             patch.object(tasks_module, "upload_csv_bytes", create=True, return_value="key"), \
             patch("sdv.evaluation.single_table.evaluate_quality", side_effect=RuntimeError("quality fail")), \
             patch("sdv.multi_table.HMASynthesizer", return_value=mock_synth), \
             patch("sdv.metadata.MultiTableMetadata", return_value=MagicMock()):

            result = generate_multi_table_data.run(
                str(uuid.uuid4()), str(uuid.uuid4()), 1.0
            )

        assert result["status"] == "done"
        assert result["quality"]["tables"]["users"] == 0.0
