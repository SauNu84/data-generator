"""
dbt schema.yml parser for the Synthetic Data Generator.

Parses dbt `schema.yml` (version: 2) files and converts column definitions
to SDV SingleTableMetadata format for synthetic data generation.

Supports:
  - Column type mapping (dbt data_type → SDV sdtype)
  - Constraint extraction (unique + not_null → id, accepted_values → categorical)
  - Unknown type handling with warnings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


# ─── Type mapping: dbt data_type → (SDV sdtype, extra_properties) ────────────

_TYPE_MAP: dict[str, tuple[str, dict]] = {
    # Integer types
    "int":       ("numerical", {}),
    "integer":   ("numerical", {}),
    "bigint":    ("numerical", {}),
    "smallint":  ("numerical", {}),
    "tinyint":   ("numerical", {}),
    # Float/numeric types
    "float":     ("numerical", {}),
    "double":    ("numerical", {}),
    "numeric":   ("numerical", {}),
    "decimal":   ("numerical", {}),
    "real":      ("numerical", {}),
    "number":    ("numerical", {}),
    # String types
    "varchar":   ("categorical", {}),
    "text":      ("categorical", {}),
    "char":      ("categorical", {}),
    "string":    ("categorical", {}),
    "nvarchar":  ("categorical", {}),
    "nchar":     ("categorical", {}),
    # Boolean
    "boolean":   ("boolean", {}),
    "bool":      ("boolean", {}),
    # Date types
    "date":      ("datetime", {"datetime_format": "%Y-%m-%d"}),
    "timestamp": ("datetime", {"datetime_format": "%Y-%m-%d %H:%M:%S"}),
    "timestamp_tz":   ("datetime", {"datetime_format": "%Y-%m-%d %H:%M:%S%z"}),
    "timestamptz":    ("datetime", {"datetime_format": "%Y-%m-%d %H:%M:%S%z"}),
    "timestamp_ntz":  ("datetime", {"datetime_format": "%Y-%m-%d %H:%M:%S"}),
    "datetime":  ("datetime", {"datetime_format": "%Y-%m-%d %H:%M:%S"}),
    # UUID
    "uuid":      ("id", {"regex": "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"}),
}

# Types to warn about (complex, unsupported → default to categorical)
_WARN_TYPES = {"jsonb", "json", "array", "struct", "map", "variant", "super"}

_MAX_MODELS = 100
_MAX_COLUMNS = 500


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ParsedColumn:
    name: str
    sdtype: str
    properties: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)


@dataclass
class ParsedModel:
    name: str
    columns: list[ParsedColumn]
    warnings: list[str] = field(default_factory=list)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _map_type(raw_type: str) -> tuple[str, dict, str | None]:
    """Map a dbt data_type string to (sdtype, properties, warning|None)."""
    t = (raw_type or "").strip().lower()
    # Strip length/precision suffixes: varchar(255) → varchar
    base = t.split("(")[0].strip()

    if base in _TYPE_MAP:
        sdtype, props = _TYPE_MAP[base]
        warning = None
        if base in _WARN_TYPES:
            warning = f"Column type '{raw_type}' is complex — defaulted to categorical"
        return sdtype, dict(props), warning

    if base in _WARN_TYPES:
        return "categorical", {}, f"Column type '{raw_type}' is not supported — defaulted to categorical"

    if not base:
        return "categorical", {}, None  # no type declared, safe default

    return "categorical", {}, f"Column type '{raw_type}' is unknown — defaulted to categorical"


def _extract_constraints(tests: list) -> list[str]:
    """Extract constraint names from dbt test declarations."""
    constraints = []
    for test in tests:
        if isinstance(test, str):
            constraints.append(test)
        elif isinstance(test, dict):
            for key in test:
                constraints.append(key)
    return constraints


# ─── Parser ───────────────────────────────────────────────────────────────────

def parse_dbt_schema(yaml_content: str) -> list[ParsedModel]:
    """Parse a dbt schema.yml string. Returns list of ParsedModel.

    Raises:
        ValueError: on invalid YAML or unsupported schema version.
    """
    try:
        doc = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise ValueError("Schema must be a YAML mapping at the root level.")

    version = doc.get("version")
    if version != 2:
        if version is None:
            raise ValueError("Missing 'version: 2' key in schema.yml.")
        raise ValueError(
            f"Unsupported dbt schema version: {version}. Only version: 2 is supported."
        )

    raw_models = doc.get("models") or []
    if len(raw_models) > _MAX_MODELS:
        raise ValueError(
            f"Schema too large: {len(raw_models)} models (max {_MAX_MODELS})."
        )

    total_columns = sum(len(m.get("columns") or []) for m in raw_models)
    if total_columns > _MAX_COLUMNS:
        raise ValueError(
            f"Schema too large: {total_columns} total columns (max {_MAX_COLUMNS})."
        )

    models = []
    for model_def in raw_models:
        model_name = model_def.get("name") or "unnamed"
        columns = []
        model_warnings = []

        for col in (model_def.get("columns") or []):
            col_name = col.get("name")
            if not col_name:
                continue

            raw_type = col.get("data_type") or ""
            sdtype, props, type_warning = _map_type(raw_type)
            if type_warning:
                model_warnings.append(f"Column '{col_name}': {type_warning}")

            constraints = _extract_constraints(col.get("tests") or [])

            # unique + not_null → treat as primary key / id column
            if "unique" in constraints and "not_null" in constraints:
                sdtype = "id"
                props = {}

            columns.append(ParsedColumn(
                name=col_name,
                sdtype=sdtype,
                properties=props,
                constraints=constraints,
            ))

        models.append(ParsedModel(name=model_name, columns=columns, warnings=model_warnings))

    return models


def to_sdv_metadata(model: ParsedModel) -> dict[str, Any]:
    """Convert a ParsedModel to SDV SingleTableMetadata dict."""
    return {
        "columns": {
            col.name: {"sdtype": col.sdtype, **col.properties}
            for col in model.columns
        }
    }
