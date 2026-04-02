"use client";

import { use, useEffect, useState, useRef } from "react";
import Link from "next/link";
import { getJob, JobResponse, ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";

const POLL_INTERVAL_MS = 3000;

export default function JobPage({
  params,
}: {
  params: Promise<{ job_id: string }>;
}) {
  const { job_id } = use(params);

  const [job, setJob] = useState<JobResponse | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [downloadExpired, setDownloadExpired] = useState(false);
  const trackedResults = useRef(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    async function poll() {
      try {
        const data = await getJob(job_id);
        if (cancelled) return;
        setJob(data);
        setFetchError(null);

        if (data.status === "done") {
          if (!trackedResults.current) {
            trackedResults.current = true;
            track("results_viewed", {
              job_id,
              quality_score: data.quality_score,
            });
          }
          // Check if download link has expired
          if (data.expires_at) {
            const expired = new Date(data.expires_at) < new Date();
            setDownloadExpired(expired);
          }
          return; // stop polling
        }

        if (data.status === "failed") return; // stop polling

        // queued or running — keep polling
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setFetchError(`Could not load job: ${err.message}`);
        } else {
          setFetchError("Network error while loading job. Retrying…");
          timer = setTimeout(poll, POLL_INTERVAL_MS);
        }
      }
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job_id]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 to-indigo-50 py-16 px-4">
      <div className="mx-auto max-w-2xl space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">Generation Results</h1>
          <p className="mt-1 text-xs text-gray-400 font-mono">{job_id}</p>
        </div>

        <div className="rounded-2xl bg-white p-8 shadow-sm ring-1 ring-gray-200 space-y-6">
          {fetchError && (
            <div role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 ring-1 ring-red-200">
              {fetchError}
            </div>
          )}

          {!job && !fetchError && (
            <div className="flex flex-col items-center py-12 space-y-3">
              <Spinner />
              <p className="text-sm text-gray-500">Loading job…</p>
            </div>
          )}

          {job && <JobContent job={job} downloadExpired={downloadExpired} />}
        </div>

        <div className="text-center">
          <Link
            href="/"
            className="text-sm text-indigo-600 hover:underline"
          >
            ← Generate another dataset
          </Link>
        </div>
      </div>
    </main>
  );
}

function JobContent({
  job,
  downloadExpired,
}: {
  job: JobResponse;
  downloadExpired: boolean;
}) {
  if (job.status === "queued" || job.status === "running") {
    return (
      <div className="flex flex-col items-center py-10 space-y-4">
        <Spinner />
        <div className="text-center">
          <StatusBadge status={job.status} />
          <p className="mt-2 text-sm text-gray-500">
            {job.status === "queued"
              ? "Your job is queued. It will start shortly."
              : "Generating your synthetic dataset…"}
          </p>
        </div>
      </div>
    );
  }

  if (job.status === "failed") {
    return (
      <div className="space-y-4">
        <StatusBadge status="failed" />
        <div role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 ring-1 ring-red-200">
          {job.error ?? "Generation failed for an unknown reason."}
        </div>
        <Link
          href="/"
          className="inline-block rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 transition-colors"
        >
          Try again
        </Link>
      </div>
    );
  }

  // done
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <StatusBadge status="done" />
      </div>

      {/* Overall quality score */}
      {job.quality_score !== undefined && (
        <div className="rounded-xl bg-indigo-50 p-6 text-center ring-1 ring-indigo-100">
          <p className="text-sm font-medium text-indigo-700 mb-1">Quality Score</p>
          <p className="text-5xl font-bold text-indigo-600">
            {Math.round(job.quality_score)}
            <span className="text-2xl">%</span>
          </p>
        </div>
      )}

      {/* Per-column quality bars */}
      {job.column_quality && job.column_quality.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Column Quality</h2>
          <div className="space-y-2">
            {job.column_quality.map((col) => (
              <div key={col.column} className="flex items-center gap-3">
                <span className="w-32 shrink-0 truncate font-mono text-xs text-gray-600">
                  {col.column}
                </span>
                <div className="flex-1 rounded-full bg-gray-100 h-2">
                  <div
                    className="rounded-full h-2 bg-indigo-500 transition-all"
                    style={{ width: `${Math.round(col.score)}%` }}
                    role="progressbar"
                    aria-valuenow={Math.round(col.score)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${col.column} quality`}
                  />
                </div>
                <span className="w-10 text-right text-xs font-medium text-gray-500">
                  {Math.round(col.score)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Download */}
      <DownloadButton
        url={job.download_url}
        expiresAt={job.expires_at}
        expired={downloadExpired}
      />
    </div>
  );
}

function DownloadButton({
  url,
  expiresAt,
  expired,
}: {
  url?: string;
  expiresAt?: string;
  expired: boolean;
}) {
  if (expired) {
    return (
      <div role="alert" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-700 ring-1 ring-amber-200">
        Download link has expired (24h TTL).{" "}
        <Link href="/" className="underline hover:no-underline">
          Re-generate to get a fresh copy.
        </Link>
      </div>
    );
  }

  if (!url) {
    return (
      <div className="text-sm text-gray-400">Download link not available yet.</div>
    );
  }

  const handleDownload = () => {
    track("download_clicked");
  };

  const expiry = expiresAt ? new Date(expiresAt).toLocaleString() : null;

  return (
    <div className="space-y-1">
      <a
        href={url}
        download
        onClick={handleDownload}
        className="flex items-center justify-center gap-2 w-full rounded-lg bg-green-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-green-700 transition-colors focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2"
      >
        <DownloadIcon />
        Download CSV
      </a>
      {expiry && (
        <p className="text-center text-xs text-gray-400">Link expires {expiry}</p>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: JobResponse["status"] }) {
  const config = {
    queued: { label: "Queued", cls: "bg-gray-100 text-gray-600" },
    running: { label: "Running", cls: "bg-blue-100 text-blue-700" },
    done: { label: "Done", cls: "bg-green-100 text-green-700" },
    failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  } as const;

  const { label, cls } = config[status];
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${cls}`}>
      {status === "running" && (
        <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
      )}
      {label}
    </span>
  );
}

function Spinner() {
  return (
    <svg
      className="h-8 w-8 animate-spin text-indigo-500"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
      />
    </svg>
  );
}
