# ADR-008: Multi-table Synthesis Architecture

- **Status**: Accepted
- **Date**: 2026-04-02
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-106 (parent: SAU-105)

---

## Context

Phase 1 synthesises a single CSV table. Enterprise customers model relational datasets — e.g. `customers → orders → order_items` — where foreign key (FK) integrity must be preserved in synthetic output. A synthetic `order` referencing a non-existent `customer_id` breaks downstream analytics, making Phase 1 output unusable for these users.

SDV provides the **HMA (Hierarchical Modeling Algorithm)** synthesiser specifically for relational multi-table synthesis. This ADR defines how we expose it.

**Goals:**
- Accept a multi-table dataset (multiple CSVs + FK relationship map)
- Synthesise all tables with referential integrity preserved
- Return a ZIP archive with one synthetic CSV per table

---

## SDV HMA Model Overview

SDV's `HMASynthesizer` accepts a `MultiTableMetadata` object defining:
1. Table schemas (per-column `sdtype`)
2. Relationships (parent FK → child FK column pairs)

It builds a recursive model: synthesise parent tables first, then use parent IDs as foreign keys when generating child rows.

**Limitation:** HMA performance degrades with >10 tables or complex many-to-many relationships (not a concern for Phase 2/3 scope: 2–6 tables typical).

---

## FK Graph Representation

### API Input Format

Users submit relationships as an adjacency list (simple, explicit):

```json
{
  "relationships": [
    {
      "parent_table": "customers",
      "parent_primary_key": "customer_id",
      "child_table": "orders",
      "child_foreign_key": "customer_id"
    },
    {
      "parent_table": "orders",
      "parent_primary_key": "order_id",
      "child_table": "order_items",
      "child_foreign_key": "order_id"
    }
  ]
}
```

### Validation Rules

Before accepting a multi-table job:
1. **Acyclicity check** — detect cycles in FK graph (e.g. A→B→A) via DFS. Reject with `422 circular_relationship`.
2. **Column existence check** — verify named FK columns exist in their respective table schemas.
3. **Root table check** — at least one table must have no incoming FK edges (root/parent table).
4. **Max tables** — cap at 10 tables (Phase 2); configurable via env var.

```python
def validate_fk_graph(tables: list[str], relationships: list[Relationship]) -> None:
    """Raises ValueError if graph contains cycles or invalid column references."""
    graph = defaultdict(list)
    for rel in relationships:
        graph[rel.parent_table].append(rel.child_table)

    visited, rec_stack = set(), set()

    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbour in graph[node]:
            if neighbour not in visited:
                dfs(neighbour)
            elif neighbour in rec_stack:
                raise ValueError(f"Circular relationship detected: {node} → {neighbour}")
        rec_stack.discard(node)

    for table in tables:
        if table not in visited:
            dfs(table)
```

---

## API Contract

### `POST /api/datasets/multi`

Upload multiple CSVs + relationship map. Returns a dataset ID for job submission.

```
POST /api/datasets/multi
Auth: Bearer JWT or X-API-Key (Enterprise tier required)
Content-Type: multipart/form-data

Fields:
  files[]:     <file>  # One CSV per table; filename = table name (e.g. customers.csv)
  metadata:   <JSON string>
    {
      "relationships": [
        {
          "parent_table": "customers",
          "parent_primary_key": "customer_id",
          "child_table": "orders",
          "child_foreign_key": "customer_id"
        }
      ]
    }

Response 201:
{
  "dataset_id": "uuid",
  "tables": ["customers", "orders", "order_items"],
  "table_count": 3,
  "relationship_count": 2,
  "schema_preview": {
    "customers": { "row_count": 500, "columns": 8 },
    "orders": { "row_count": 2400, "columns": 6 },
    "order_items": { "row_count": 8900, "columns": 5 }
  }
}

Response 422:
{
  "error": "circular_relationship",
  "detail": "Circular FK detected: orders → customers → orders"
}
```

### `POST /api/datasets/{dataset_id}/jobs/multi`

Enqueue a multi-table generation job.

```
POST /api/datasets/{dataset_id}/jobs/multi
Auth: Bearer JWT or X-API-Key (Enterprise tier required)
Content-Type: application/json

Request:
{
  "scale_factor": 1.0,    # 1.0 = same row count as source; 2.0 = 2x rows per table
  "sdv_model": "HMA"      # only valid value for multi-table
}

Response 202:
{
  "job_id": "uuid",
  "status": "queued",
  "dataset_id": "uuid",
  "estimated_tables": 3,
  "scale_factor": 1.0
}
```

### `GET /api/jobs/{job_id}/download`

For multi-table jobs, returns a ZIP download URL (not a single CSV).

```
Response 200:
{
  "job_id": "uuid",
  "status": "done",
  "download_url": "https://s3.../synthetic_dataset_uuid.zip",  # presigned, 1h TTL
  "tables": [
    { "name": "customers", "row_count": 500 },
    { "name": "orders", "row_count": 2400 },
    { "name": "order_items", "row_count": 8900 }
  ],
  "quality_scores": {
    "customers": { "overall": 0.92 },
    "orders": { "overall": 0.88 },
    "order_items": { "overall": 0.85 }
  }
}
```

---

## Celery Task Design

```python
@celery_app.task(
    name="tasks.generate_multi_table",
    bind=True,
    max_retries=2,
    soft_time_limit=3600,  # 60 min soft; HMA on large datasets is slow
    time_limit=3900,
)
def generate_multi_table(self, job_id: str):
    job = db.get(GenerationJob, job_id)
    dataset = db.get(Dataset, job.dataset_id)

    # 1. Load all source CSVs from S3
    table_dfs = {}
    for table_name, s3_key in dataset.source_s3_keys.items():  # JSON col on Dataset
        table_dfs[table_name] = pd.read_csv(s3.get_object(s3_key))

    # 2. Build SDV MultiTableMetadata
    metadata = MultiTableMetadata()
    for table_name, schema in dataset.schema_json["tables"].items():
        metadata.add_table(table_name)
        for col_name, col_props in schema["columns"].items():
            metadata.update_column(table_name, col_name, **col_props)

    for rel in dataset.schema_json["relationships"]:
        metadata.add_relationship(
            parent_table_name=rel["parent_table"],
            parent_primary_key=rel["parent_primary_key"],
            child_table_name=rel["child_table"],
            child_foreign_key=rel["child_foreign_key"],
        )

    # 3. Fit HMA synthesiser
    synthesizer = HMASynthesizer(metadata)
    synthesizer.fit(table_dfs)

    # 4. Sample synthetic tables
    scale = dataset.schema_json.get("scale_factor", 1.0)
    synthetic_tables = synthesizer.sample(scale=scale)

    # 5. Compute quality scores per table
    quality_scores = {}
    for table_name, synth_df in synthetic_tables.items():
        report = evaluate_quality(table_dfs[table_name], synth_df, metadata)
        quality_scores[table_name] = {"overall": report.get_score()}

    # 6. Write ZIP to S3
    zip_key = f"outputs/{job_id}/synthetic.zip"
    zip_buffer = build_zip(synthetic_tables)  # BytesIO ZIP of CSVs
    s3.upload_fileobj(zip_buffer, zip_key)

    # 7. Update job
    job.status = "done"
    job.output_s3_key = zip_key
    job.quality_score_json = quality_scores
    job.completed_at = datetime.now(UTC)
    db.commit()
```

---

## Storage Schema Changes

Multi-table datasets require tracking multiple source files. The `Dataset` model's `schema_json` is extended:

```json
{
  "mode": "multi_table",
  "tables": {
    "customers": {
      "columns": { "customer_id": { "sdtype": "id" }, ... },
      "source_s3_key": "inputs/uuid/customers.csv",
      "row_count": 500
    },
    "orders": { ... }
  },
  "relationships": [
    { "parent_table": "customers", "parent_primary_key": "customer_id",
      "child_table": "orders", "child_foreign_key": "customer_id" }
  ],
  "scale_factor": 1.0
}
```

This is a backward-compatible extension to the existing `schema_json` JSON column — no migration required for Phase 1 datasets.

---

## Consequences

**Positive:**
- FK integrity preserved automatically by HMA — no post-processing needed
- ZIP output with per-table CSVs matches how analysts expect relational data
- Backward-compatible schema_json extension means no DB migration for existing single-table datasets
- DFS cycle check prevents infinite loops in the Celery worker

**Negative / Trade-offs:**
- HMA fitting is significantly slower than single-table synthesisers (5–10x); large datasets may approach the 60-min Celery soft limit — document in UI
- HMA accuracy degrades with complex many-to-many (junction tables); warn users and recommend normalising to 1:M before using
- `scale_factor` applies uniformly to all tables — individual table row count overrides are Phase 3
- Enterprise-only feature (tier check at route level)

---

## Dependencies

- `sdv` (already in stack) — HMA synthesiser is included in SDV ≥ 1.0
- No new infrastructure

---

## Revisit Trigger

Revisit if: users request many-to-many (M:M) junction table support → evaluate SDV `PARSynthesiser` or custom graph walk. Trigger: 3+ Enterprise customer requests for M:M.
