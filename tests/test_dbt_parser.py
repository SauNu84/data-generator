"""Unit tests for app/dbt_parser.py — dbt schema.yml parser."""

import pytest

from app.dbt_parser import (
    ParsedColumn,
    ParsedModel,
    _extract_constraints,
    _map_type,
    parse_dbt_schema,
    to_sdv_metadata,
)

# ─── _map_type ────────────────────────────────────────────────────────────────

def test_map_type_integer():
    sdtype, props, warn = _map_type("int")
    assert sdtype == "numerical"
    assert props == {}
    assert warn is None


def test_map_type_varchar():
    sdtype, props, warn = _map_type("varchar")
    assert sdtype == "categorical"
    assert warn is None


def test_map_type_varchar_with_length():
    """varchar(255) should strip precision suffix and map correctly."""
    sdtype, props, warn = _map_type("varchar(255)")
    assert sdtype == "categorical"
    assert warn is None


def test_map_type_boolean():
    sdtype, props, warn = _map_type("boolean")
    assert sdtype == "boolean"
    assert warn is None


def test_map_type_date():
    sdtype, props, warn = _map_type("date")
    assert sdtype == "datetime"
    assert props == {"datetime_format": "%Y-%m-%d"}
    assert warn is None


def test_map_type_timestamp():
    sdtype, props, warn = _map_type("timestamp")
    assert sdtype == "datetime"
    assert "datetime_format" in props


def test_map_type_uuid():
    sdtype, props, warn = _map_type("uuid")
    assert sdtype == "id"
    assert "regex" in props
    assert warn is None


def test_map_type_jsonb_warns():
    """jsonb is a _WARN_TYPES member — should return categorical with warning."""
    sdtype, props, warn = _map_type("jsonb")
    assert sdtype == "categorical"
    assert warn is not None
    assert "not supported" in warn.lower() or "jsonb" in warn


def test_map_type_unknown_warns():
    sdtype, props, warn = _map_type("myCustomType")
    assert sdtype == "categorical"
    assert warn is not None
    assert "unknown" in warn.lower()


def test_map_type_empty_string():
    sdtype, props, warn = _map_type("")
    assert sdtype == "categorical"
    assert warn is None


def test_map_type_case_insensitive():
    sdtype, props, warn = _map_type("INT")
    assert sdtype == "numerical"


def test_map_type_numeric_with_precision():
    sdtype, props, warn = _map_type("numeric(10,2)")
    assert sdtype == "numerical"
    assert warn is None


# ─── _extract_constraints ─────────────────────────────────────────────────────

def test_extract_constraints_string_tests():
    result = _extract_constraints(["unique", "not_null"])
    assert result == ["unique", "not_null"]


def test_extract_constraints_dict_tests():
    result = _extract_constraints([{"accepted_values": {"values": ["A", "B"]}}])
    assert result == ["accepted_values"]


def test_extract_constraints_mixed():
    result = _extract_constraints(["not_null", {"relationships": {}}])
    assert "not_null" in result
    assert "relationships" in result


def test_extract_constraints_empty():
    assert _extract_constraints([]) == []


# ─── parse_dbt_schema ─────────────────────────────────────────────────────────

VALID_YAML = """
version: 2

models:
  - name: orders
    columns:
      - name: order_id
        data_type: uuid
        tests:
          - unique
          - not_null
      - name: customer_id
        data_type: bigint
      - name: status
        data_type: varchar
      - name: created_at
        data_type: timestamp
"""


def test_parse_valid_schema():
    models = parse_dbt_schema(VALID_YAML)
    assert len(models) == 1
    model = models[0]
    assert model.name == "orders"
    assert len(model.columns) == 4


def test_parse_order_id_becomes_id_type():
    """Column with unique + not_null tests should become sdtype='id'."""
    models = parse_dbt_schema(VALID_YAML)
    col = next(c for c in models[0].columns if c.name == "order_id")
    assert col.sdtype == "id"
    assert "unique" in col.constraints
    assert "not_null" in col.constraints


def test_parse_timestamp_column():
    models = parse_dbt_schema(VALID_YAML)
    col = next(c for c in models[0].columns if c.name == "created_at")
    assert col.sdtype == "datetime"


def test_parse_invalid_yaml_raises():
    with pytest.raises(ValueError, match="Invalid YAML"):
        parse_dbt_schema("key: [unclosed")


def test_parse_non_mapping_raises():
    with pytest.raises(ValueError, match="mapping"):
        parse_dbt_schema("- item1\n- item2\n")


def test_parse_missing_version_raises():
    yaml = "models:\n  - name: foo\n    columns: []\n"
    with pytest.raises(ValueError, match="version"):
        parse_dbt_schema(yaml)


def test_parse_wrong_version_raises():
    yaml = "version: 1\nmodels:\n  - name: foo\n    columns: []\n"
    with pytest.raises(ValueError, match="Unsupported"):
        parse_dbt_schema(yaml)


def test_parse_no_models():
    yaml = "version: 2\n"
    models = parse_dbt_schema(yaml)
    assert models == []


def test_parse_column_without_name_skipped():
    yaml = """
version: 2
models:
  - name: foo
    columns:
      - data_type: int
      - name: valid_col
        data_type: int
"""
    models = parse_dbt_schema(yaml)
    assert len(models[0].columns) == 1
    assert models[0].columns[0].name == "valid_col"


def test_parse_unknown_type_produces_warning():
    yaml = """
version: 2
models:
  - name: foo
    columns:
      - name: weird_col
        data_type: mysterytype
"""
    models = parse_dbt_schema(yaml)
    assert len(models[0].warnings) == 1
    assert "mysterytype" in models[0].warnings[0]


def test_parse_too_many_models_raises():
    from app.dbt_parser import _MAX_MODELS
    models_yaml = "\n".join(
        f"  - name: model_{i}\n    columns: []" for i in range(_MAX_MODELS + 1)
    )
    yaml = f"version: 2\nmodels:\n{models_yaml}\n"
    with pytest.raises(ValueError, match="too large"):
        parse_dbt_schema(yaml)


def test_parse_too_many_columns_raises():
    from app.dbt_parser import _MAX_COLUMNS
    cols_yaml = "\n".join(
        f"      - name: col_{i}\n        data_type: int" for i in range(_MAX_COLUMNS + 1)
    )
    yaml = f"version: 2\nmodels:\n  - name: big_model\n    columns:\n{cols_yaml}\n"
    with pytest.raises(ValueError, match="too large"):
        parse_dbt_schema(yaml)


def test_parse_multiple_models():
    yaml = """
version: 2
models:
  - name: users
    columns:
      - name: id
        data_type: uuid
  - name: events
    columns:
      - name: event_id
        data_type: bigint
      - name: event_name
        data_type: varchar
"""
    models = parse_dbt_schema(yaml)
    assert len(models) == 2
    assert {m.name for m in models} == {"users", "events"}


def test_parse_model_without_columns():
    yaml = "version: 2\nmodels:\n  - name: empty_model\n"
    models = parse_dbt_schema(yaml)
    assert len(models) == 1
    assert models[0].columns == []


# ─── to_sdv_metadata ──────────────────────────────────────────────────────────

def test_to_sdv_metadata_basic():
    model = ParsedModel(
        name="orders",
        columns=[
            ParsedColumn(name="id", sdtype="id", properties={}),
            ParsedColumn(name="amount", sdtype="numerical", properties={}),
            ParsedColumn(name="status", sdtype="categorical", properties={}),
        ],
    )
    meta = to_sdv_metadata(model)
    assert "columns" in meta
    assert meta["columns"]["id"] == {"sdtype": "id"}
    assert meta["columns"]["amount"] == {"sdtype": "numerical"}


def test_to_sdv_metadata_includes_properties():
    model = ParsedModel(
        name="events",
        columns=[
            ParsedColumn(
                name="created_at",
                sdtype="datetime",
                properties={"datetime_format": "%Y-%m-%d"},
            ),
        ],
    )
    meta = to_sdv_metadata(model)
    assert meta["columns"]["created_at"]["datetime_format"] == "%Y-%m-%d"
    assert meta["columns"]["created_at"]["sdtype"] == "datetime"
