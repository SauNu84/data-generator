# ADR-007: dbt Integration Architecture

- **Status**: Accepted
- **Date**: 2026-04-02
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-106 (parent: SAU-105)

---

## Context

dbt (data build tool) is the dominant data transformation layer used by analytics engineers. It defines table schemas in `schema.yml` files with column-level metadata (type, description, tests). A significant share of our Pro/Enterprise target users already have dbt projects.

This feature allows users to:
1. Upload or paste a dbt `schema.yml` file
2. The platform parses column definitions and auto-generates an SDV metadata config
3. User clicks "Generate" → synthetic data matching their dbt schema is produced

**Goal:** Reduce time-to-first-dataset from "build schema manually" (10–30 min) to "paste schema.yml" (30 seconds).

---

## dbt schema.yml Structure (Relevant Subset)

```yaml
version: 2

models:
  - name: orders
    description: "E-commerce orders"
    columns:
      - name: order_id
        data_type: bigint
        tests: [unique, not_null]
      - name: customer_id
        data_type: bigint
      - name: status
        data_type: varchar
        tests:
          - accepted_values:
              values: ['placed', 'shipped', 'delivered', 'returned']
      - name: amount
        data_type: numeric
      - name: created_at
        data_type: timestamp
```

---

## Type Mapping: dbt → SDV Metadata

SDV requires a `SingleTableMetadata` dict with column `sdtype` values.

| dbt `data_type` | SDV `sdtype` | Notes |
|---|---|---|
| `int`, `integer`, `bigint`, `smallint` | `numerical` | |
| `float`, `double`, `numeric`, `decimal`, `real` | `numerical` | |
| `varchar`, `text`, `char`, `string` | `categorical` | |
| `boolean`, `bool` | `boolean` | |
| `date` | `datetime` | `datetime_format: "%Y-%m-%d"` |
| `timestamp`, `timestamp_tz`, `timestamptz` | `datetime` | `datetime_format: "%Y-%m-%d %H:%M:%S"` |
| `uuid` | `id` | `regex: "[0-9a-f]{8}-[0-9a-f]{4}-..."` |
| Unknown / null | `categorical` | Safe default — SDV handles it |

### Constraint Extraction

From dbt `tests`, extract constraints for SDV:
- `unique` + `not_null` → mark as candidate primary key (`sdtype: id`)
- `accepted_values: values: [...]` → `sdtype: categorical` with cardinality hint (inform CTGAN)
- `not_null` alone → flag column as non-nullable (post-generation validation step)
- `relationships` (dbt source refs) → used in multi-table mode (ADR-008) for FK graph

---

## Parser Design

### Module: `app/dbt_parser.py`

```python
from dataclasses import dataclass, field
from typing import Any
import yaml

@dataclass
class ParsedColumn:
    name: str
    sdtype: str
    properties: dict = field(default_factory=dict)  # datetime_format, regex, etc.
    constraints: list[str] = field(default_factory=list)  # unique, not_null, accepted_values

@dataclass
class ParsedModel:
    name: str
    columns: list[ParsedColumn]

def parse_dbt_schema(yaml_content: str) -> list[ParsedModel]:
    """Parse a dbt schema.yml and return SDV-ready metadata per model."""
    doc = yaml.safe_load(yaml_content)
    models = []
    for model_def in doc.get("models", []):
        columns = []
        for col in model_def.get("columns", []):
            sdtype, props = _map_type(col.get("data_type", ""))
            constraints = _extract_constraints(col.get("tests", []))
            # Override sdtype: unique + not_null → id
            if "unique" in constraints and "not_null" in constraints:
                sdtype = "id"
                props = {}
            columns.append(ParsedColumn(
                name=col["name"],
                sdtype=sdtype,
                properties=props,
                constraints=constraints,
            ))
        models.append(ParsedModel(name=model_def["name"], columns=columns))
    return models

def to_sdv_metadata(model: ParsedModel) -> dict[str, Any]:
    """Convert ParsedModel to SDV SingleTableMetadata dict."""
    return {
        "columns": {
            col.name: {"sdtype": col.sdtype, **col.properties}
            for col in model.columns
        }
    }
```

---

## API Endpoint Spec

### `POST /api/dbt/parse`

Validates and parses a dbt schema file, returns SDV metadata preview. **Does not generate data.**

```
POST /api/dbt/parse
Auth: Bearer JWT or X-API-Key (Pro tier required)
Content-Type: multipart/form-data OR application/json

Request (multipart):
  schema_file: <file upload>  # .yml / .yaml

Request (JSON):
  { "schema_yaml": "<raw yaml string>" }

Response 200:
{
  "models": [
    {
      "name": "orders",
      "column_count": 5,
      "sdv_metadata": {
        "columns": {
          "order_id": { "sdtype": "id" },
          "customer_id": { "sdtype": "numerical" },
          "status": { "sdtype": "categorical" },
          "amount": { "sdtype": "numerical" },
          "created_at": { "sdtype": "datetime", "datetime_format": "%Y-%m-%d %H:%M:%S" }
        }
      },
      "warnings": [
        "Column 'notes' has unknown type 'jsonb' — defaulted to categorical"
      ]
    }
  ]
}

Response 422:
{
  "error": "invalid_dbt_schema",
  "detail": "Missing 'version' key or malformed YAML"
}
```

### `POST /api/dbt/generate`

Parse schema + create a dataset + enqueue a generation job in one call.

```
POST /api/dbt/generate
Auth: Bearer JWT or X-API-Key (Pro tier required)
Content-Type: application/json

Request:
{
  "schema_yaml": "<raw yaml string>",
  "model_name": "orders",      # which model from the schema to generate
  "row_count": 1000,
  "sdv_model": "GaussianCopula"  # optional, default GaussianCopula
}

Response 202:
{
  "dataset_id": "uuid",
  "job_id": "uuid",
  "status": "queued",
  "model_name": "orders",
  "row_count": 1000
}
```

**Implementation flow:**
1. Parse + validate `schema_yaml` via `parse_dbt_schema()`
2. Extract model matching `model_name`
3. Convert to SDV metadata dict via `to_sdv_metadata()`
4. `INSERT Dataset` with `schema_json = sdv_metadata` + `source = 'dbt'`
5. `INSERT GenerationJob` + enqueue Celery task (same as existing flow)
6. Return 202

---

## Supported dbt Versions

- dbt Core schema.yml format `version: 2` (current standard)
- Compatible with: dbt-core ≥ 1.0, dbt Cloud, Snowflake dbt, BigQuery dbt
- **Not supported:** dbt `version: 1` (legacy, <1% of active projects)
- Parser is YAML-based, not dbt-runtime dependent — no dbt installation required

---

## Error Handling

| Scenario | HTTP | Error Code |
|---|---|---|
| Invalid YAML syntax | 422 | `invalid_yaml` |
| Missing `version: 2` | 422 | `unsupported_dbt_version` |
| `model_name` not found in schema | 422 | `model_not_found` |
| Zero columns parsed | 422 | `empty_model` |
| All columns unknown type (all categorical) | 200 + warning | `all_types_defaulted` |
| Schema too large (>100 models, >500 columns) | 413 | `schema_too_large` |

---

## Consequences

**Positive:**
- No dbt runtime dependency — pure YAML parsing, zero infrastructure
- Reduces onboarding friction for the core analytics engineer persona
- Reuses existing `Dataset` + `GenerationJob` pipeline with zero changes to Celery/SDV layer
- `warnings` array surfaces unknown types gracefully without failing

**Negative / Trade-offs:**
- Only handles `version: 2` schema format (acceptable for 99%+ of active dbt users)
- `jsonb`, `array`, `struct` types default to `categorical` — may produce lower quality synthetic data for complex types (document in user-facing warning)
- No support for dbt `sources:` block (cross-table FK relationships) in this ADR — deferred to Multi-table ADR (ADR-008)

---

## Dependencies

- `PyYAML` — dbt schema parsing (already in many Python environments; add to requirements.txt)
- No other new dependencies

---

## Revisit Trigger

Revisit if: users request support for dbt `sources:` block FK relationships → implement in ADR-008 multi-table flow. Also revisit if dbt releases `version: 3` schema format.
