const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ColumnSchema {
  name: string;
  detected_type: "numeric" | "categorical" | "datetime" | "boolean";
}

export interface UploadResponse {
  dataset_id: string;
  row_count: number;
  schema: ColumnSchema[];
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

export async function uploadCSV(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload`, { method: "POST", body: form });
  return handleResponse<UploadResponse>(res);
}

export async function generateJob(req: GenerateRequest): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<GenerateResponse>(res);
}

export async function getJob(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
  return handleResponse<JobResponse>(res);
}
