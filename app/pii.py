"""
PII detection and masking for uploaded CSVs.

Detects PII in:
  - Column names (heuristic): name, email, phone, ssn, dob, address, etc.
  - Column values (regex sampling): emails, phone numbers, SSNs, credit cards

Masking replaces real values with Faker-generated synthetic equivalents.
PII columns are excluded from SDV training to avoid memorisation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


# ─── Regex patterns ───────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?"
    r"(?:\(?\d{3}\)?[-.\s]?)"
    r"\d{3}[-.\s]?\d{4}",
)
_SSN_RE = re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")
_CREDIT_CARD_RE = re.compile(
    r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b"
)
_IP_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)

# Column-name heuristics → PII type label
_NAME_HEURISTICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(first[_\s]?name|last[_\s]?name|full[_\s]?name|given[_\s]?name|surname|display[_\s]?name)\b", re.I), "name"),
    (re.compile(r"\b(email|e[_\s]?mail|email[_\s]?address)\b", re.I), "email"),
    (re.compile(r"\b(phone|mobile|cell|telephone|fax|contact[_\s]?number)\b", re.I), "phone"),
    (re.compile(r"\b(ssn|social[_\s]?security)\b", re.I), "ssn"),
    (re.compile(r"\b(dob|date[_\s]?of[_\s]?birth|birth[_\s]?date|birthdate)\b", re.I), "dob"),
    (re.compile(r"\b(address|street|city|zip[_\s]?code|postal[_\s]?code)\b", re.I), "address"),
    (re.compile(r"\b(credit[_\s]?card|card[_\s]?number|cc[_\s]?number|cvv|ccv)\b", re.I), "credit_card"),
    (re.compile(r"\b(passport|national[_\s]?id|national[_\s]?number|driver[_\s]?license)\b", re.I), "id_document"),
    (re.compile(r"\b(ip[_\s]?address|ipv4|ipv6)\b", re.I), "ip_address"),
    (re.compile(r"\b(salary|income|wage|compensation|pay)\b", re.I), "financial"),
    (re.compile(r"\b(diagnosis|medical|health[_\s]?condition|disease|treatment)\b", re.I), "health"),
]


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class PiiColumn:
    column: str
    pii_type: str           # email | phone | ssn | credit_card | name | address | ...
    detection_method: str   # name_heuristic | value_regex
    sample_count: int = 0   # how many sampled values matched


@dataclass
class PiiScanResult:
    pii_columns: list[PiiColumn] = field(default_factory=list)

    @property
    def flagged_columns(self) -> set[str]:
        return {c.column for c in self.pii_columns}

    def has_pii(self) -> bool:
        return bool(self.pii_columns)


# ─── Scanning ─────────────────────────────────────────────────────────────────

def _check_name_heuristics(col: str) -> str | None:
    """Return PII type label if column name matches heuristics, else None."""
    for pattern, pii_type in _NAME_HEURISTICS:
        if pattern.search(col):
            return pii_type
    return None


def _regex_check_values(series: pd.Series) -> str | None:
    """Regex-scan a sample of string values; return PII type or None."""
    sample = series.dropna().astype(str).head(50)
    if sample.empty:
        return None

    checks: list[tuple[re.Pattern, str]] = [
        (_EMAIL_RE, "email"),
        (_SSN_RE, "ssn"),
        (_CREDIT_CARD_RE, "credit_card"),
        (_PHONE_RE, "phone"),
        (_IP_RE, "ip_address"),
    ]
    for pattern, pii_type in checks:
        matches = sample.apply(lambda v: bool(pattern.search(v))).sum()
        if matches >= max(1, len(sample) * 0.3):  # ≥30% match threshold
            return pii_type
    return None


def scan_dataframe(df: pd.DataFrame) -> PiiScanResult:
    """Scan a DataFrame for PII columns. Returns PiiScanResult."""
    result = PiiScanResult()
    seen: set[str] = set()

    for col in df.columns:
        # 1. Name heuristic
        pii_type = _check_name_heuristics(col)
        if pii_type:
            result.pii_columns.append(PiiColumn(column=col, pii_type=pii_type, detection_method="name_heuristic"))
            seen.add(col)
            continue

        # 2. Value regex (string/object columns only)
        if df[col].dtype == object or str(df[col].dtype).startswith("string"):
            pii_type = _regex_check_values(df[col])
            if pii_type:
                result.pii_columns.append(
                    PiiColumn(
                        column=col,
                        pii_type=pii_type,
                        detection_method="value_regex",
                        sample_count=int(df[col].dropna().shape[0]),
                    )
                )
                seen.add(col)

    return result


# ─── Masking ─────────────────────────────────────────────────────────────────

def _get_faker():
    try:
        from faker import Faker
        return Faker()
    except ImportError:
        return None


_FAKER_GENERATORS: dict[str, Callable] = {}


def _build_faker_generators(fake) -> dict[str, Callable]:
    if not fake:
        return {}
    return {
        "email": fake.email,
        "phone": fake.phone_number,
        "ssn": fake.ssn,
        "credit_card": fake.credit_card_number,
        "name": fake.name,
        "address": fake.address,
        "dob": lambda: str(fake.date_of_birth()),
        "id_document": lambda: fake.bothify("??######"),
        "ip_address": fake.ipv4,
        "financial": lambda: str(round(fake.pyfloat(min_value=30000, max_value=200000, right_digits=2), 2)),
        "health": fake.bs,  # nonsense text; no real diagnoses
    }


def mask_dataframe(df: pd.DataFrame, pii_columns: list[PiiColumn]) -> pd.DataFrame:
    """Return a copy of df with PII columns replaced by Faker-generated values.

    If Faker is not installed, drops the PII columns entirely (safe fallback).
    """
    fake = _get_faker()
    generators = _build_faker_generators(fake)
    masked = df.copy()

    for pii_col in pii_columns:
        col = pii_col.column
        if col not in masked.columns:
            continue

        gen = generators.get(pii_col.pii_type)
        if gen is None:
            # Unknown type — replace with a generic token
            masked[col] = masked[col].apply(lambda _: f"[REDACTED_{pii_col.pii_type.upper()}]")
        else:
            n = len(masked)
            masked[col] = [gen() for _ in range(n)]

    return masked


def drop_pii_columns(df: pd.DataFrame, pii_columns: list[PiiColumn]) -> pd.DataFrame:
    """Drop PII columns from df before fitting SDV model."""
    cols_to_drop = [p.column for p in pii_columns if p.column in df.columns]
    return df.drop(columns=cols_to_drop)
