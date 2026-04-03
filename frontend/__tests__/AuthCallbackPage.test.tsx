/**
 * Component tests for app/auth/callback/page.tsx — SAU-118
 * Coverage target: ≥80%
 *
 * Scenarios:
 *   - Shows "Signing you in…" loading state
 *   - Successful OAuth: fetches /api/auth/me, saves session, redirects to /dashboard
 *   - Missing tokens in hash: redirects to /login?error=oauth_failed
 *   - Fetch error: redirects to /login?error=oauth_failed
 */

import { render, screen, waitFor } from "@testing-library/react";
import AuthCallbackPage from "@/app/auth/callback/page";
import * as authLib from "@/lib/auth";

const mockReplace = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

jest.mock("@/lib/auth", () => ({
  ...jest.requireActual("@/lib/auth"),
  saveSession: jest.fn(),
}));

const mockSaveSession = authLib.saveSession as jest.MockedFunction<typeof authLib.saveSession>;

beforeEach(() => {
  jest.clearAllMocks();
  // Reset hash
  Object.defineProperty(window, "location", {
    writable: true,
    value: { ...window.location, hash: "" },
  });
  global.fetch = jest.fn();
});

function setHash(access_token: string, refresh_token: string) {
  Object.defineProperty(window, "location", {
    writable: true,
    value: {
      ...window.location,
      hash: `#access_token=${access_token}&refresh_token=${refresh_token}`,
    },
  });
}

describe("AuthCallbackPage", () => {
  it("renders loading text", () => {
    render(<AuthCallbackPage />);
    expect(screen.getByText(/signing you in/i)).toBeInTheDocument();
  });

  it("saves session and redirects to /dashboard on success", async () => {
    setHash("access_tok_123", "refresh_tok_456");

    const mockUser = {
      id: "u1",
      email: "oauth@example.com",
      tier: "free",
      is_email_verified: true,
      created_at: "2026-01-01",
    };

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      json: jest.fn().mockResolvedValue(mockUser),
    });

    render(<AuthCallbackPage />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/auth/me"),
        expect.objectContaining({
          headers: { Authorization: "Bearer access_tok_123" },
        })
      );
      expect(mockSaveSession).toHaveBeenCalledWith("access_tok_123", "refresh_tok_456", mockUser);
      expect(mockReplace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("redirects to /login?error=oauth_failed when tokens missing from hash", async () => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, hash: "#some_other_param=value" },
    });

    render(<AuthCallbackPage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login?error=oauth_failed");
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("redirects to /login?error=oauth_failed when hash is empty", async () => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, hash: "" },
    });

    render(<AuthCallbackPage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login?error=oauth_failed");
    });
  });

  it("redirects to /login?error=oauth_failed when fetch fails", async () => {
    setHash("access_tok_123", "refresh_tok_456");

    (global.fetch as jest.Mock).mockRejectedValueOnce(new Error("Network error"));

    render(<AuthCallbackPage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login?error=oauth_failed");
    });
    expect(mockSaveSession).not.toHaveBeenCalled();
  });
});
