import { getAccessToken, saveSession, clearSession, UserProfile } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ColumnSchema {
  name: string;
  detected_type: "numeric" | "categorical" | "datetime" | "boolean";
}

export interface UploadResponse {
  dataset_id: string;
  row_count: number;
  columns: ColumnSchema[];
}

export interface GenerateRequest {
  dataset_id: string;
  row_count: number;
  model: "GaussianCopula" | "CTGAN";
  schema_overrides?: Record<string, ColumnSchema["detected_type"]>;
}

export interface GenerateResponse {
  job_id: string;
}

export interface ColumnQuality {
  column: string;
  score: number;
}

export interface JobResponse {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  error?: string;
  quality_score?: number;
  column_quality?: ColumnQuality[];
  download_url?: string;
  expires_at?: string;
}

export interface DatasetSummary {
  id: string;
  original_filename: string;
  row_count: number;
  created_at: string;
  job_count: number;
}

export interface DashboardResponse {
  datasets: DatasetSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface UsageSummary {
  tier: string;
  monthly_generations_used: number;
  monthly_generations_limit: number | null;
}

export interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: UserProfile;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, message);
  }
  return res.json();
}

function authHeaders(): HeadersInit {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function register(email: string, password: string): Promise<AuthTokenResponse> {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await handleResponse<AuthTokenResponse>(res);
  saveSession(data.access_token, data.refresh_token, data.user);
  return data;
}

export async function login(email: string, password: string): Promise<AuthTokenResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await handleResponse<AuthTokenResponse>(res);
  saveSession(data.access_token, data.refresh_token, data.user);
  return data;
}

export async function logout(refreshToken: string): Promise<void> {
  await fetch(`${API_BASE}/api/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  clearSession();
}

export async function getMe(): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { ...authHeaders() },
  });
  return handleResponse<UserProfile>(res);
}

// ── Upload / Generate ─────────────────────────────────────────────────────────

export async function uploadCSV(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    headers: { ...authHeaders() },
    body: form,
  });
  return handleResponse<UploadResponse>(res);
}

export async function generateJob(req: GenerateRequest): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(req),
  });
  return handleResponse<GenerateResponse>(res);
}

export async function getJob(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}`, {
    headers: { ...authHeaders() },
  });
  return handleResponse<JobResponse>(res);
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export async function getDashboard(page = 1): Promise<DashboardResponse> {
  const res = await fetch(`${API_BASE}/api/dashboard?page=${page}`, {
    headers: { ...authHeaders() },
  });
  return handleResponse<DashboardResponse>(res);
}

export async function deleteDataset(datasetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/dashboard/${datasetId}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (!res.ok && res.status !== 204) {
    await handleResponse<never>(res);
  }
}

// ── Billing ───────────────────────────────────────────────────────────────────

export async function getUsageSummary(): Promise<UsageSummary> {
  const res = await fetch(`${API_BASE}/api/billing/usage`, {
    headers: { ...authHeaders() },
  });
  return handleResponse<UsageSummary>(res);
}

export async function createCheckoutSession(): Promise<{ checkout_url: string }> {
  const res = await fetch(`${API_BASE}/api/billing/checkout`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  return handleResponse<{ checkout_url: string }>(res);
}

// ── API Keys ──────────────────────────────────────────────────────────────────

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  request_count: number;
  last_used_at: string | null;
  revoked: boolean;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await fetch(`${API_BASE}/api/keys`, {
    headers: { ...authHeaders() },
  });
  return handleResponse<ApiKey[]>(res);
}

export async function createApiKey(name?: string): Promise<ApiKeyCreated> {
  const res = await fetch(`${API_BASE}/api/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ name: name ?? "Default" }),
  });
  return handleResponse<ApiKeyCreated>(res);
}

export async function revokeApiKey(keyId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/keys/${keyId}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (!res.ok && res.status !== 204) {
    await handleResponse<never>(res);
  }
}
