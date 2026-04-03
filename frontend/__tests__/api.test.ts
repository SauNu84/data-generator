/**
 * Unit tests for lib/api.ts
 * Uses jest.fn() to mock global.fetch — no network calls.
 */

import {
  ApiError,
  uploadCSV,
  generateJob,
  getJob,
  register,
  login,
  logout,
  getMe,
  getDashboard,
  deleteDataset,
  getUsageSummary,
  createCheckoutSession,
  listApiKeys,
  createApiKey,
  revokeApiKey,
} from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeResponse(
  body: unknown,
  status = 200,
  ok?: boolean
): Response {
  const isOk = ok !== undefined ? ok : (status >= 200 && status < 300);
  return {
    ok: isOk,
    status,
    json: jest.fn().mockResolvedValue(body),
  } as unknown as Response;
}

function makeErrorResponse(status: number, body: unknown): Response {
  return makeResponse(body, status, false);
}

function makeJsonParseErrorResponse(status: number): Response {
  return {
    ok: false,
    status,
    json: jest.fn().mockRejectedValue(new SyntaxError("Unexpected token")),
  } as unknown as Response;
}

// ── Setup / Teardown ─────────────────────────────────────────────────────────

const mockFetch = jest.fn();

beforeEach(() => {
  global.fetch = mockFetch;
  mockFetch.mockReset();
  // Clear localStorage so auth headers are empty by default
  localStorage.clear();
});

afterAll(() => {
  delete (global as unknown as Record<string, unknown>).fetch;
});

// ── ApiError ─────────────────────────────────────────────────────────────────

describe("ApiError", () => {
  it("is an instance of Error", () => {
    const err = new ApiError(404, "Not found");
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
  });

  it("has name set to ApiError", () => {
    const err = new ApiError(422, "Unprocessable");
    expect(err.name).toBe("ApiError");
  });

  it("exposes status and message", () => {
    const err = new ApiError(500, "Internal Server Error");
    expect(err.status).toBe(500);
    expect(err.message).toBe("Internal Server Error");
  });
});

// ── handleResponse (tested through exported functions) ────────────────────────

describe("handleResponse — via uploadCSV", () => {
  it("returns parsed JSON on 200 OK", async () => {
    const payload = { dataset_id: "ds-1", row_count: 100, columns: [] };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }));
    expect(result).toEqual(payload);
  });

  it("throws ApiError with body.detail on 4xx", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(422, { detail: "File too large" })
    );

    await expect(
      uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }))
    ).rejects.toMatchObject({ name: "ApiError", status: 422, message: "File too large" });
  });

  it("throws ApiError with body.message when detail is absent", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(400, { message: "Bad input" })
    );

    await expect(
      uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }))
    ).rejects.toMatchObject({ name: "ApiError", status: 400, message: "Bad input" });
  });

  it("throws ApiError with fallback message when body is not valid JSON", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonParseErrorResponse(413));

    await expect(
      uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }))
    ).rejects.toMatchObject({ name: "ApiError", status: 413, message: "HTTP 413" });
  });

  it("throws ApiError on 5xx with detail", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(503, { detail: "Service unavailable" })
    );

    await expect(
      uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }))
    ).rejects.toMatchObject({ name: "ApiError", status: 503, message: "Service unavailable" });
  });

  it("throws ApiError on 5xx with no parseable body", async () => {
    mockFetch.mockResolvedValueOnce(makeJsonParseErrorResponse(500));

    await expect(
      uploadCSV(new File(["a,b"], "data.csv", { type: "text/csv" }))
    ).rejects.toMatchObject({ name: "ApiError", status: 500, message: "HTTP 500" });
  });
});

// ── uploadCSV ────────────────────────────────────────────────────────────────

describe("uploadCSV", () => {
  it("POSTs FormData to /api/upload", async () => {
    const payload = { dataset_id: "ds-42", row_count: 200, columns: [] };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const file = new File(["col1,col2\n1,2"], "test.csv", { type: "text/csv" });
    const result = await uploadCSV(file);

    expect(result).toEqual(payload);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/upload");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBeInstanceOf(FormData);
  });

  it("includes Authorization header when token is present", async () => {
    localStorage.setItem("sdg_access_token", "tok-abc");
    mockFetch.mockResolvedValueOnce(makeResponse({ dataset_id: "ds-1", row_count: 1, columns: [] }));

    await uploadCSV(new File(["a"], "f.csv", { type: "text/csv" }));

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers).toMatchObject({ Authorization: "Bearer tok-abc" });
  });

  it("sends no Authorization header when no token", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ dataset_id: "ds-1", row_count: 1, columns: [] }));

    await uploadCSV(new File(["a"], "f.csv", { type: "text/csv" }));

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers?.Authorization).toBeUndefined();
  });
});

// ── generateJob ───────────────────────────────────────────────────────────────

describe("generateJob", () => {
  const req = {
    dataset_id: "ds-1",
    row_count: 500,
    model: "GaussianCopula" as const,
  };

  it("POSTs JSON to /api/generate and returns job_id", async () => {
    const payload = { job_id: "job-abc" };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await generateJob(req);

    expect(result).toEqual(payload);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/generate");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toMatchObject(req);
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(402, { detail: "Upgrade required" })
    );

    await expect(generateJob(req)).rejects.toMatchObject({
      name: "ApiError",
      status: 402,
      message: "Upgrade required",
    });
  });
});

// ── getJob ────────────────────────────────────────────────────────────────────

describe("getJob", () => {
  it("GETs /api/jobs/{jobId} and returns JobResponse", async () => {
    const payload = {
      job_id: "job-1",
      status: "done",
      quality_score: 0.9,
      download_url: "http://example.com/file.csv",
    };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await getJob("job-1");

    expect(result).toEqual(payload);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/jobs/job-1");
  });

  it("throws ApiError on 404", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(404, { detail: "Job not found" })
    );

    await expect(getJob("bad-id")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "Job not found",
    });
  });
});

// ── register ──────────────────────────────────────────────────────────────────

describe("register", () => {
  const tokenResponse = {
    access_token: "at",
    refresh_token: "rt",
    token_type: "bearer",
    user: { id: "u1", email: "a@b.com", tier: "free", is_email_verified: false, created_at: "2024-01-01" },
  };

  it("POSTs to /api/auth/register and saves session", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(tokenResponse));

    const result = await register("a@b.com", "password123");

    expect(result).toEqual(tokenResponse);
    expect(localStorage.getItem("sdg_access_token")).toBe("at");
    expect(localStorage.getItem("sdg_refresh_token")).toBe("rt");
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/auth/register");
    expect(opts.method).toBe("POST");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(409, { detail: "Email already registered" })
    );

    await expect(register("a@b.com", "pass")).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
    });
  });
});

// ── login ─────────────────────────────────────────────────────────────────────

describe("login", () => {
  const tokenResponse = {
    access_token: "at2",
    refresh_token: "rt2",
    token_type: "bearer",
    user: { id: "u2", email: "x@y.com", tier: "pro", is_email_verified: true, created_at: "2024-01-01" },
  };

  it("POSTs to /api/auth/login and saves session", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(tokenResponse));

    const result = await login("x@y.com", "secure");

    expect(result).toEqual(tokenResponse);
    expect(localStorage.getItem("sdg_access_token")).toBe("at2");
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/auth/login");
    expect(opts.method).toBe("POST");
  });

  it("throws ApiError on 401", async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(401, { detail: "Invalid credentials" })
    );

    await expect(login("x@y.com", "wrong")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
  });
});

// ── logout ────────────────────────────────────────────────────────────────────

describe("logout", () => {
  it("POSTs to /api/auth/logout and clears session", async () => {
    localStorage.setItem("sdg_access_token", "tok");
    localStorage.setItem("sdg_refresh_token", "rtok");
    mockFetch.mockResolvedValueOnce(makeResponse({}, 200));

    await logout("rtok");

    expect(localStorage.getItem("sdg_access_token")).toBeNull();
    expect(localStorage.getItem("sdg_refresh_token")).toBeNull();
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/auth/logout");
    expect(opts.method).toBe("POST");
  });
});

// ── getMe ─────────────────────────────────────────────────────────────────────

describe("getMe", () => {
  it("GETs /api/auth/me and returns user profile", async () => {
    const user = { id: "u1", email: "a@b.com", tier: "free", is_email_verified: true, created_at: "2024-01-01" };
    mockFetch.mockResolvedValueOnce(makeResponse(user));

    const result = await getMe();

    expect(result).toEqual(user);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/auth/me");
  });

  it("throws ApiError on 401", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(401, { detail: "Unauthorized" }));

    await expect(getMe()).rejects.toMatchObject({ name: "ApiError", status: 401 });
  });
});

// ── getDashboard ──────────────────────────────────────────────────────────────

describe("getDashboard", () => {
  const dashboardPayload = {
    datasets: [{ id: "ds-1", original_filename: "data.csv", row_count: 100, created_at: "2024-01-01", job_count: 2 }],
    total: 1,
    page: 1,
    page_size: 10,
  };

  it("GETs /api/dashboard with default page=1", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(dashboardPayload));

    const result = await getDashboard();

    expect(result).toEqual(dashboardPayload);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/dashboard?page=1");
  });

  it("GETs /api/dashboard with specified page", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(dashboardPayload));

    await getDashboard(3);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/dashboard?page=3");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(403, { detail: "Forbidden" }));

    await expect(getDashboard()).rejects.toMatchObject({ name: "ApiError", status: 403 });
  });
});

// ── deleteDataset ─────────────────────────────────────────────────────────────

describe("deleteDataset", () => {
  it("DELETEs /api/dashboard/{datasetId} on success", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, json: jest.fn() } as unknown as Response);

    await deleteDataset("ds-1");

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/dashboard/ds-1");
    expect(opts.method).toBe("DELETE");
  });

  it("does not throw on 204 (no content)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 204, json: jest.fn() } as unknown as Response);

    await expect(deleteDataset("ds-1")).resolves.toBeUndefined();
  });

  it("throws ApiError on non-204 error response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(404, { detail: "Dataset not found" }));

    await expect(deleteDataset("bad-id")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});

// ── getUsageSummary ───────────────────────────────────────────────────────────

describe("getUsageSummary", () => {
  it("GETs /api/billing/usage and returns usage", async () => {
    const payload = { tier: "free", monthly_generations_used: 5, monthly_generations_limit: 10 };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await getUsageSummary();

    expect(result).toEqual(payload);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/billing/usage");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(401, { detail: "Not authenticated" }));

    await expect(getUsageSummary()).rejects.toMatchObject({ name: "ApiError", status: 401 });
  });
});

// ── createCheckoutSession ─────────────────────────────────────────────────────

describe("createCheckoutSession", () => {
  it("POSTs to /api/billing/checkout and returns checkout_url", async () => {
    const payload = { checkout_url: "https://stripe.com/pay/xyz" };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await createCheckoutSession();

    expect(result).toEqual(payload);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/billing/checkout");
    expect(opts.method).toBe("POST");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(402, { detail: "Payment required" }));

    await expect(createCheckoutSession()).rejects.toMatchObject({ name: "ApiError", status: 402 });
  });
});

// ── listApiKeys ───────────────────────────────────────────────────────────────

describe("listApiKeys", () => {
  const keysPayload = [
    { id: "k1", name: "My Key", key_prefix: "sk-xxx", request_count: 10, last_used_at: null, revoked: false, created_at: "2024-01-01" },
  ];

  it("GETs /api/keys and returns list", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(keysPayload));

    const result = await listApiKeys();

    expect(result).toEqual(keysPayload);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/keys");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(401, { detail: "Unauthorized" }));

    await expect(listApiKeys()).rejects.toMatchObject({ name: "ApiError", status: 401 });
  });
});

// ── createApiKey ──────────────────────────────────────────────────────────────

describe("createApiKey", () => {
  const createdPayload = {
    id: "k2", name: "My Key", key_prefix: "sk-yyy", key: "sk-yyy-full",
    request_count: 0, last_used_at: null, revoked: false, created_at: "2024-01-01",
  };

  it("POSTs to /api/keys with provided name", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(createdPayload));

    const result = await createApiKey("My Key");

    expect(result).toEqual(createdPayload);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/keys");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ name: "My Key" });
  });

  it("uses Default as name when not provided", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(createdPayload));

    await createApiKey();

    const [, opts] = mockFetch.mock.calls[0];
    expect(JSON.parse(opts.body)).toEqual({ name: "Default" });
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(409, { detail: "Key already exists" }));

    await expect(createApiKey("dup")).rejects.toMatchObject({ name: "ApiError", status: 409 });
  });
});

// ── revokeApiKey ──────────────────────────────────────────────────────────────

describe("revokeApiKey", () => {
  it("DELETEs /api/keys/{keyId}", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, json: jest.fn() } as unknown as Response);

    await revokeApiKey("k1");

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/keys/k1");
    expect(opts.method).toBe("DELETE");
  });

  it("does not throw on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 204, json: jest.fn() } as unknown as Response);

    await expect(revokeApiKey("k1")).resolves.toBeUndefined();
  });

  it("throws ApiError on non-204 error response", async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(404, { detail: "Key not found" }));

    await expect(revokeApiKey("bad-key")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});
