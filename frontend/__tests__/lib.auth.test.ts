/**
 * Unit tests for lib/auth.ts — SAU-118
 * Coverage target: ≥90% on lib/auth.ts
 */

import {
  getAccessToken,
  getRefreshToken,
  getUser,
  saveSession,
  clearSession,
  isAuthenticated,
  UserProfile,
} from "@/lib/auth";

const mockUser: UserProfile = {
  id: "user-123",
  email: "test@example.com",
  tier: "free",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
});

// ─── getAccessToken ───────────────────────────────────────────────────────────

describe("getAccessToken", () => {
  it("returns null when not set", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("returns token when set", () => {
    localStorage.setItem("sdg_access_token", "tok_abc");
    expect(getAccessToken()).toBe("tok_abc");
  });
});

// ─── getRefreshToken ──────────────────────────────────────────────────────────

describe("getRefreshToken", () => {
  it("returns null when not set", () => {
    expect(getRefreshToken()).toBeNull();
  });

  it("returns refresh token when set", () => {
    localStorage.setItem("sdg_refresh_token", "ref_xyz");
    expect(getRefreshToken()).toBe("ref_xyz");
  });
});

// ─── getUser ──────────────────────────────────────────────────────────────────

describe("getUser", () => {
  it("returns null when not set", () => {
    expect(getUser()).toBeNull();
  });

  it("returns parsed user when set", () => {
    localStorage.setItem("sdg_user", JSON.stringify(mockUser));
    expect(getUser()).toEqual(mockUser);
  });

  it("returns null when stored value is invalid JSON", () => {
    localStorage.setItem("sdg_user", "not-json{{{{");
    expect(getUser()).toBeNull();
  });
});

// ─── saveSession ──────────────────────────────────────────────────────────────

describe("saveSession", () => {
  it("persists all three values in localStorage", () => {
    saveSession("access_tok", "refresh_tok", mockUser);
    expect(localStorage.getItem("sdg_access_token")).toBe("access_tok");
    expect(localStorage.getItem("sdg_refresh_token")).toBe("refresh_tok");
    expect(JSON.parse(localStorage.getItem("sdg_user")!)).toEqual(mockUser);
  });
});

// ─── clearSession ─────────────────────────────────────────────────────────────

describe("clearSession", () => {
  it("removes all auth keys", () => {
    saveSession("access_tok", "refresh_tok", mockUser);
    clearSession();
    expect(localStorage.getItem("sdg_access_token")).toBeNull();
    expect(localStorage.getItem("sdg_refresh_token")).toBeNull();
    expect(localStorage.getItem("sdg_user")).toBeNull();
  });
});

// ─── isAuthenticated ──────────────────────────────────────────────────────────

describe("isAuthenticated", () => {
  it("returns false when no access token", () => {
    expect(isAuthenticated()).toBe(false);
  });

  it("returns true when access token is present", () => {
    localStorage.setItem("sdg_access_token", "tok_abc");
    expect(isAuthenticated()).toBe(true);
  });
});

// ─── SSR guard (typeof window === "undefined") ────────────────────────────────

describe("SSR guard — returns null when window is undefined", () => {
  let savedWindow: typeof window;

  beforeEach(() => {
    savedWindow = global.window;
    // @ts-ignore — simulate SSR environment
    delete global.window;
  });

  afterEach(() => {
    global.window = savedWindow;
  });

  it("getAccessToken returns null in SSR", () => {
    // Re-require to force fresh execution (module is cached, but function re-evaluates typeof window)
    expect(getAccessToken()).toBeNull();
  });

  it("getRefreshToken returns null in SSR", () => {
    expect(getRefreshToken()).toBeNull();
  });

  it("getUser returns null in SSR", () => {
    expect(getUser()).toBeNull();
  });
});
