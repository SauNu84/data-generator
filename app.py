"""
SDV FastAPI spike — CSV upload → GaussianCopula synthesizer → synthetic CSV.

Endpoints:
  POST /synthesize  — upload a CSV, get back synthetic rows
  GET  /health      — liveness check
"""

import io
import time
import logging

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sdv.metadata import Metadata
from sdv.single_table import GaussianCopulaSynthesizer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="SDV Synthetic Data API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/synthesize")
async def synthesize(
    file: UploadFile = File(..., description="Input CSV"),
    num_rows: int = Form(default=100, ge=1, le=500_000, description="Rows to generate"),
):
    """
    Accept a CSV, fit a GaussianCopulaSynthesizer, return synthetic CSV.

    Returns:
        StreamingResponse — synthetic CSV with Content-Disposition attachment header.
    """
    raw = await file.read()
    try:
        real_df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}") from exc

    if real_df.empty:
        raise HTTPException(status_code=400, detail="Uploaded CSV is empty.")

    log.info("Received %d rows × %d cols. Generating %d synthetic rows.", *real_df.shape, num_rows)

    t0 = time.perf_counter()
    metadata = Metadata.detect_from_dataframe(real_df)
    synth = GaussianCopulaSynthesizer(metadata)
    synth.fit(real_df)
    fit_sec = time.perf_counter() - t0

    t1 = time.perf_counter()
    synthetic_df = synth.sample(num_rows)
    gen_sec = time.perf_counter() - t1

    log.info("Fit: %.3fs  Generate %d rows: %.3fs", fit_sec, num_rows, gen_sec)

    buf = io.StringIO()
    synthetic_df.to_csv(buf, index=False)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="synthetic_{file.filename}"',
            "X-Fit-Seconds": f"{fit_sec:.3f}",
            "X-Generate-Seconds": f"{gen_sec:.3f}",
        },
    )
