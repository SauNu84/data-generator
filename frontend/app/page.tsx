"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import DropZone from "@/components/DropZone";
import SchemaTable from "@/components/SchemaTable";
import { uploadCSV, generateJob, ColumnSchema, ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";

type Stage = "idle" | "uploading" | "schema" | "generating";

export default function UploadPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);

  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [schema, setSchema] = useState<ColumnSchema[]>([]);
  const [sourceRowCount, setSourceRowCount] = useState<number>(100);

  const [overrides, setOverrides] = useState<Record<string, ColumnSchema["detected_type"]>>({});
  const [rowCount, setRowCount] = useState<number>(100);
  const [model, setModel] = useState<"GaussianCopula" | "CTGAN">("GaussianCopula");

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    setStage("uploading");
    try {
      const result = await uploadCSV(file);
      setDatasetId(result.dataset_id);
      setSchema(result.schema);
      setSourceRowCount(result.row_count);
      setRowCount(result.row_count);
      setOverrides({});
      setStage("schema");
      track("upload_success", { row_count: result.row_count, columns: result.schema.length });
    } catch (err) {
      setStage("idle");
      if (err instanceof ApiError) {
        const msg = err.message.toLowerCase();
        if (msg.includes("100k") || msg.includes("100,000") || msg.includes("100000")) {
          setError("File exceeds 100k row limit. Please upload a smaller file.");
        } else {
          setError(`Upload failed: ${err.message}`);
        }
      } else {
        setError("Upload failed due to a network error. Please try again.");
      }
    }
  }, []);

  const handleGenerate = async () => {
    if (!datasetId) return;
    setError(null);
    setStage("generating");
    track("generate_initiated", { model, row_count: rowCount });
    try {
      const result = await generateJob({
        dataset_id: datasetId,
        row_count: rowCount,
        model,
        schema_overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
      });
      router.push(`/jobs/${result.job_id}`);
    } catch (err) {
      setStage("schema");
      if (err instanceof ApiError) {
        setError(`Generation failed: ${err.message}`);
      } else {
        setError("Generation failed due to a network error. Please try again.");
      }
    }
  };

  const handleOverride = (column: string, type: ColumnSchema["detected_type"]) => {
    setOverrides((prev) => ({ ...prev, [column]: type }));
  };

  const reset = () => {
    setStage("idle");
    setError(null);
    setDatasetId(null);
    setSchema([]);
    setOverrides({});
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 to-indigo-50 py-16 px-4">
      <div className="mx-auto max-w-2xl space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">Synthetic Data Generator</h1>
          <p className="mt-2 text-gray-500">
            Upload a CSV, tweak the schema, and generate a privacy-safe synthetic copy.
          </p>
        </div>

        <div className="rounded-2xl bg-white p-8 shadow-sm ring-1 ring-gray-200 space-y-6">
          {stage === "idle" && (
            <DropZone onFile={handleFile} disabled={false} />
          )}

          {stage === "uploading" && (
            <div className="flex flex-col items-center justify-center py-12 space-y-3">
              <Spinner />
              <p className="text-sm text-gray-500">Uploading and analysing schema…</p>
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 ring-1 ring-red-200"
            >
              {error}
              {stage === "idle" && (
                <button
                  onClick={() => setError(null)}
                  className="ml-2 underline hover:no-underline"
                >
                  Dismiss
                </button>
              )}
            </div>
          )}

          {(stage === "schema" || stage === "generating") && schema.length > 0 && (
            <div className="space-y-6">
              <SchemaTable schema={schema} overrides={overrides} onOverride={handleOverride} />

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="row-count" className="block text-sm font-medium text-gray-700 mb-1">
                    Rows to generate{" "}
                    <span className="text-gray-400 font-normal">(max 100k)</span>
                  </label>
                  <input
                    id="row-count"
                    type="number"
                    min={1}
                    max={100000}
                    value={rowCount}
                    onChange={(e) =>
                      setRowCount(Math.min(100000, Math.max(1, Number(e.target.value))))
                    }
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                  <p className="mt-0.5 text-xs text-gray-400">
                    Source file: {sourceRowCount.toLocaleString()} rows
                  </p>
                </div>

                <div>
                  <p className="block text-sm font-medium text-gray-700 mb-1">Model</p>
                  <div className="flex rounded-lg border border-gray-300 overflow-hidden">
                    {(["GaussianCopula", "CTGAN"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setModel(m)}
                        className={[
                          "flex-1 py-2 text-sm font-medium transition-colors",
                          model === m
                            ? "bg-indigo-600 text-white"
                            : "bg-white text-gray-700 hover:bg-gray-50",
                        ].join(" ")}
                      >
                        {m}
                      </button>
                    ))}
                  </div>
                  <p className="mt-0.5 text-xs text-gray-400">
                    {model === "GaussianCopula"
                      ? "Faster · better for numeric data"
                      : "Slower · better for categorical data"}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={handleGenerate}
                  disabled={stage === "generating"}
                  className="flex-1 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
                >
                  {stage === "generating" ? (
                    <span className="flex items-center justify-center gap-2">
                      <Spinner size="sm" /> Generating…
                    </span>
                  ) : (
                    "Generate"
                  )}
                </button>
                <button
                  type="button"
                  onClick={reset}
                  disabled={stage === "generating"}
                  className="rounded-lg border border-gray-300 px-4 py-2.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                >
                  Upload different file
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function Spinner({ size = "md" }: { size?: "sm" | "md" }) {
  const cls = size === "sm" ? "h-4 w-4" : "h-8 w-8";
  return (
    <svg
      className={`${cls} animate-spin text-indigo-500`}
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}
