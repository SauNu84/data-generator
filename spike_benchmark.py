"""
SDV spike: benchmark GaussianCopula at 1k/10k/100k rows and score quality.

Run: python3.11 spike_benchmark.py
"""

import time
import warnings
import pandas as pd
import numpy as np
from sdv.metadata import Metadata
from sdv.single_table import GaussianCopulaSynthesizer
from sdv.evaluation.single_table import evaluate_quality, run_diagnostic

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# 1. Build a realistic 10-column reference dataset (100 000 rows)
# ---------------------------------------------------------------------------
rng = np.random.default_rng(42)
N_REF = 100_000

ref_data = pd.DataFrame({
    "age":        rng.integers(18, 80, N_REF).astype(int),
    "income":     rng.lognormal(mean=10.5, sigma=0.8, size=N_REF).round(2),
    "score":      rng.uniform(300, 850, N_REF).round(1),
    "tenure":     rng.integers(0, 30, N_REF).astype(int),
    "balance":    rng.normal(5000, 3000, N_REF).round(2),
    "num_txn":    rng.poisson(12, N_REF).astype(int),
    "is_active":  rng.choice([True, False], N_REF),
    "region":     rng.choice(["North", "South", "East", "West"], N_REF),
    "product":    rng.choice(["Basic", "Premium", "Elite"], N_REF, p=[0.5, 0.35, 0.15]),
    "churn":      rng.choice([0, 1], N_REF, p=[0.85, 0.15]),
})

print(f"Reference dataset: {ref_data.shape[0]:,} rows × {ref_data.shape[1]} columns")
print(ref_data.dtypes.to_string())
print()

# ---------------------------------------------------------------------------
# 2. Detect metadata once (reuse across benchmarks)
# ---------------------------------------------------------------------------
metadata = Metadata.detect_from_dataframe(ref_data)

# ---------------------------------------------------------------------------
# 3. Benchmark generation: fit on a 5 000-row sample, generate at 3 scales
# ---------------------------------------------------------------------------
TRAIN_SAMPLE = 5_000
BENCH_SIZES = [1_000, 10_000, 100_000]

train_df = ref_data.sample(n=TRAIN_SAMPLE, random_state=42)

print("=" * 60)
print(f"Fitting GaussianCopula on {TRAIN_SAMPLE:,}-row sample...")
t_fit_start = time.perf_counter()
synth = GaussianCopulaSynthesizer(metadata)
synth.fit(train_df)
fit_sec = time.perf_counter() - t_fit_start
print(f"  Fit time: {fit_sec:.3f}s")
print()

results = []
for n in BENCH_SIZES:
    t0 = time.perf_counter()
    _ = synth.sample(n)
    elapsed = time.perf_counter() - t0
    rps = n / elapsed
    results.append({"rows": n, "seconds": round(elapsed, 3), "rows_per_sec": int(rps)})
    print(f"  Generate {n:>7,} rows: {elapsed:.3f}s  ({rps:,.0f} rows/s)")

print()
print("Benchmark summary:")
bench_df = pd.DataFrame(results)
print(bench_df.to_string(index=False))

# ---------------------------------------------------------------------------
# 4. Quality scoring (fit on full 100 k for a meaningful score)
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("Quality scoring (fitting on full 100k ref dataset)...")
t0 = time.perf_counter()
synth_full = GaussianCopulaSynthesizer(metadata)
synth_full.fit(ref_data)
fit_full_sec = time.perf_counter() - t0
print(f"  Full fit time: {fit_full_sec:.3f}s")

synth_sample = synth_full.sample(10_000)

quality_report = evaluate_quality(ref_data, synth_sample, metadata, verbose=False)
diag_report = run_diagnostic(ref_data, synth_sample, metadata, verbose=False)

overall = quality_report.get_score()
col_shapes = quality_report.get_details("Column Shapes")
col_pair_trends = quality_report.get_details("Column Pair Trends")

print(f"\n  Overall Quality Score: {overall:.4f}")
print("\n  Column Shapes (distribution match per column):")
print(col_shapes[["Column", "Metric", "Score"]].to_string(index=False))
print("\n  Column Pair Trends (correlation preservation, top 10 pairs):")
print(col_pair_trends[["Column 1", "Column 2", "Metric", "Score"]].head(10).to_string(index=False))

diag_score = diag_report.get_score()
print(f"\n  Diagnostic Score: {diag_score:.4f}")

# ---------------------------------------------------------------------------
# 5. Limitation probes
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("Limitation probes")
print()

# High-cardinality text
hc_df = pd.DataFrame({
    "id":    range(1000),
    "email": [f"user{i}@example.com" for i in range(1000)],
    "value": rng.uniform(0, 100, 1000).round(2),
})
hc_meta = Metadata.detect_from_dataframe(hc_df)
synth_hc = GaussianCopulaSynthesizer(hc_meta)
try:
    synth_hc.fit(hc_df)
    out_hc = synth_hc.sample(5)
    print("High-cardinality text (email column, 1 000 unique values):")
    print(f"  Fit: OK — SDV treats high-cardinality strings as categorical IDs.")
    print(f"  Sample emails: {list(out_hc['email'].values)}")
    print("  ⚠ Values are drawn from the training set, no truly new emails generated.")
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Datetime handling
dt_df = pd.DataFrame({
    "ts":    pd.date_range("2023-01-01", periods=500, freq="1h"),
    "amount": rng.uniform(1, 500, 500).round(2),
})
dt_meta = Metadata.detect_from_dataframe(dt_df)
synth_dt = GaussianCopulaSynthesizer(dt_meta)
try:
    synth_dt.fit(dt_df)
    out_dt = synth_dt.sample(3)
    print("Datetime column handling:")
    print(f"  Fit: OK — SDV auto-converts timestamps to numeric.")
    print(f"  Sample: {list(out_dt['ts'].astype(str).values)}")
    print("  ⚠ Synthetic timestamps may fall outside training range if distribution extrapolates.")
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Null handling
null_df = pd.DataFrame({
    "a": [1.0, None, 3.0, None, 5.0] * 200,
    "b": rng.normal(0, 1, 1000).round(3),
})
null_meta = Metadata.detect_from_dataframe(null_df)
synth_null = GaussianCopulaSynthesizer(null_meta)
try:
    synth_null.fit(null_df)
    out_null = synth_null.sample(500)
    null_rate = out_null["a"].isna().mean()
    print(f"Null value handling (40% nulls in col 'a'):")
    print(f"  Fit: OK — SDV preserves null rates via internal NullTransformer.")
    print(f"  Synthetic null rate in 'a': {null_rate:.2%}  (real: 40%)")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("Done.")
