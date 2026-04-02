"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  getDashboard,
  deleteDataset,
  getUsageSummary,
  createCheckoutSession,
  ApiError,
  DatasetSummary,
  UsageSummary,
} from "@/lib/api";
import { getUser, getRefreshToken, clearSession } from "@/lib/auth";
import { logout } from "@/lib/api";

const PAGE_SIZE = 20;

export default function DashboardPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const user = getUser();

  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [upgrading, setUpgrading] = useState(false);
  const [showUpgradeBanner, setShowUpgradeBanner] = useState(false);

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!user) {
      router.replace("/login");
    }
  }, [user, router]);

  // Show upgrade banner when coming from generate (402) or upgrade=1 param
  useEffect(() => {
    if (searchParams.get("upgrade") === "1" || searchParams.get("upgraded") === "1") {
      setShowUpgradeBanner(true);
    }
  }, [searchParams]);

  const loadDashboard = useCallback(async (p: number) => {
    setLoading(true);
    setError(null);
    try {
      const [dash, usageData] = await Promise.all([getDashboard(p), getUsageSummary()]);
      setDatasets(dash.datasets);
      setTotal(dash.total);
      setUsage(usageData);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        router.replace("/login");
        return;
      }
      setError("Failed to load dashboard.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (user) loadDashboard(page);
  }, [user, page, loadDashboard]);

  async function handleDelete(datasetId: string) {
    if (!confirm("Delete this dataset and all its jobs?")) return;
    try {
      await deleteDataset(datasetId);
      loadDashboard(page);
    } catch {
      setError("Failed to delete dataset.");
    }
  }

  async function handleUpgrade() {
    setUpgrading(true);
    try {
      const { checkout_url } = await createCheckoutSession();
      window.location.href = checkout_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start checkout.");
      setUpgrading(false);
    }
  }

  async function handleLogout() {
    const rt = getRefreshToken();
    if (rt) await logout(rt);
    router.replace("/login");
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const isFreeTierLimitReached =
    usage?.tier === "free" &&
    usage.monthly_generations_limit !== null &&
    usage.monthly_generations_used >= (usage.monthly_generations_limit ?? 0);

  if (!user) return null;

  return (
    <main className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold text-gray-900">Synthetic Data Generator</h1>
          {usage && (
            <span
              className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                usage.tier === "free"
                  ? "bg-gray-100 text-gray-600"
                  : "bg-indigo-100 text-indigo-700"
              }`}
            >
              {usage.tier === "free" ? "Free" : "Pro"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="text-sm text-indigo-600 font-medium hover:underline"
          >
            + New Dataset
          </Link>
          <span className="text-sm text-gray-500">{user.email}</span>
          <button
            onClick={handleLogout}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {/* Upgrade banner */}
        {(showUpgradeBanner || isFreeTierLimitReached) && usage?.tier === "free" && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-amber-800">
                {isFreeTierLimitReached
                  ? `Free tier limit reached (${usage.monthly_generations_limit} generations/month).`
                  : "Unlock unlimited generations with Pro."}
              </p>
              <p className="text-xs text-amber-700 mt-0.5">
                Upgrade to Pro for $49/month — unlimited generations, API access, and more.
              </p>
            </div>
            <button
              onClick={handleUpgrade}
              disabled={upgrading}
              className="shrink-0 rounded-md bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {upgrading ? "Redirecting…" : "Upgrade to Pro"}
            </button>
          </div>
        )}

        {/* Usage summary */}
        {usage && usage.tier === "free" && !isFreeTierLimitReached && (
          <div className="text-sm text-gray-500">
            This month:{" "}
            <span className="font-medium text-gray-900">
              {usage.monthly_generations_used} / {usage.monthly_generations_limit}
            </span>{" "}
            generations used.
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-md bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Datasets table */}
        <section>
          <h2 className="text-base font-semibold text-gray-900 mb-4">
            Your Datasets{" "}
            {total > 0 && (
              <span className="text-sm font-normal text-gray-400">({total} total)</span>
            )}
          </h2>

          {loading ? (
            <div className="py-12 text-center text-sm text-gray-400">Loading…</div>
          ) : datasets.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-sm text-gray-500">No datasets yet.</p>
              <Link href="/" className="mt-2 inline-block text-sm text-indigo-600 hover:underline">
                Upload your first CSV
              </Link>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium">File</th>
                    <th className="px-4 py-3 text-left font-medium">Rows</th>
                    <th className="px-4 py-3 text-left font-medium">Jobs</th>
                    <th className="px-4 py-3 text-left font-medium">Created</th>
                    <th className="px-4 py-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {datasets.map((ds) => (
                    <tr key={ds.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900 max-w-xs truncate">
                        {ds.original_filename}
                      </td>
                      <td className="px-4 py-3 text-gray-600">
                        {ds.row_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-gray-600">{ds.job_count}</td>
                      <td className="px-4 py-3 text-gray-500">
                        {new Date(ds.created_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right space-x-3">
                        <Link
                          href={`/?dataset_id=${ds.id}`}
                          className="text-indigo-600 hover:underline"
                        >
                          Re-generate
                        </Link>
                        <button
                          onClick={() => handleDelete(ds.id)}
                          className="text-red-500 hover:underline"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Previous
              </button>
              <span className="text-gray-500">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
