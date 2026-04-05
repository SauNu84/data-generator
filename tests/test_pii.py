"""Unit tests for app/pii.py — PII detection and masking."""

import pandas as pd
import pytest

from app.pii import PiiScanResult, scan_dataframe


# ─── scan_dataframe ───────────────────────────────────────────────────────────

def test_scan_detects_email_by_column_name():
    df = pd.DataFrame({"email": ["user@example.com"], "age": [30]})
    result = scan_dataframe(df)
    assert result.has_pii()
    assert "email" in result.flagged_columns
    col = next(c for c in result.pii_columns if c.column == "email")
    assert col.pii_type == "email"
    assert col.detection_method == "name_heuristic"


def test_scan_detects_phone_by_column_name():
    df = pd.DataFrame({"phone": ["555-1234"], "score": [9.5]})
    result = scan_dataframe(df)
    assert "phone" in result.flagged_columns


def test_scan_detects_ssn_by_column_name():
    df = pd.DataFrame({"ssn": ["123-45-6789"]})
    result = scan_dataframe(df)
    assert "ssn" in result.flagged_columns


def test_scan_detects_email_by_value_regex():
    df = pd.DataFrame({
        "contact": ["alice@example.com", "bob@test.org", "carol@domain.net"]
    })
    result = scan_dataframe(df)
    assert result.has_pii()
    assert "contact" in result.flagged_columns
    col = next(c for c in result.pii_columns if c.column == "contact")
    assert col.detection_method == "value_regex"
    assert col.pii_type == "email"


def test_scan_no_pii_clean_data():
    df = pd.DataFrame({
        "age": [25, 30, 35],
        "score": [8.5, 9.0, 7.5],
        "bucket": ["A", "B", "C"],
    })
    result = scan_dataframe(df)
    assert not result.has_pii()
    assert result.flagged_columns == set()


def test_scan_detects_name_heuristic():
    df = pd.DataFrame({"first_name": ["Alice"], "last_name": ["Smith"]})
    result = scan_dataframe(df)
    assert "first_name" in result.flagged_columns
    assert "last_name" in result.flagged_columns


def test_scan_detects_credit_card_by_column_name():
    df = pd.DataFrame({"credit_card": ["4111111111111111"]})
    result = scan_dataframe(df)
    assert "credit_card" in result.flagged_columns


def test_scan_detects_address_by_column_name():
    df = pd.DataFrame({"address": ["123 Main St"]})
    result = scan_dataframe(df)
    assert "address" in result.flagged_columns


def test_scan_empty_dataframe():
    df = pd.DataFrame()
    result = scan_dataframe(df)
    assert not result.has_pii()


def test_scan_name_heuristic_takes_priority_over_value_regex():
    """If column name matches heuristic, value scan is skipped (seen set)."""
    df = pd.DataFrame({"email_address": ["alice@example.com", "bob@test.org"]})
    result = scan_dataframe(df)
    assert "email_address" in result.flagged_columns
    col = next(c for c in result.pii_columns if c.column == "email_address")
    # Name heuristic fires first
    assert col.detection_method == "name_heuristic"


def test_pii_scan_result_flagged_columns():
    from app.pii import PiiColumn
    result = PiiScanResult()
    result.pii_columns.append(PiiColumn(column="email", pii_type="email", detection_method="name_heuristic"))
    result.pii_columns.append(PiiColumn(column="phone", pii_type="phone", detection_method="value_regex"))
    assert result.flagged_columns == {"email", "phone"}
    assert result.has_pii()


def test_scan_non_string_column_skips_value_regex():
    """Numeric columns should not trigger value regex scan."""
    df = pd.DataFrame({"account_number": [1234567890, 9876543210]})
    result = scan_dataframe(df)
    # account_number doesn't match any name heuristic, and is numeric → no PII
    assert "account_number" not in result.flagged_columns
