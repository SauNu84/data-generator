/**
 * Component tests for app/dashboard/page.tsx — SAU-118
 * Coverage target: ≥75%
 *
 * Scenarios:
 *   - Unauthenticated: redirects to /login
 *   - Authenticated: renders datasets, usage summary, user email
 *   - Upgrade banner shown when upgrade=1 param present
 *   - Upgrade banner shown when free tier limit reached
 *   - Usage summary displayed for free user with limit
 *   - Empty state shown when no datasets
 *   - Dataset list rendered
 *   - Delete dataset with confirmation
 *   - Logout clears session and redirects
 *   - 401 API error clears session and redirects
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "@/app/dashboard/page";
import * as api from "@/lib/api";
import * as authLib from "@/lib/auth";

const mockReplace = jest.fn();
const mockPush = jest.fn();

const mockSearchParams = { get: jest.fn() };

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: mockPush }),
  useSearchParams: () => mockSearchParams,
}));

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getDashboard: jest.fn(),
  deleteDataset: jest.fn(),
  getUsageSummary: jest.fn(),
  createCheckoutSession: jest.fn(),
  logout: jest.fn(),
  ApiError: jest.requireActual("@/lib/api").ApiError,
}));

jest.mock("@/lib/auth", () => ({
  ...jest.requireActual("@/lib/auth"),
  getUser: jest.fn(),
  getRefreshToken: jest.fn(),
  clearSession: jest.fn(),
}));

const mockGetDashboard = api.getDashboard as jest.MockedFunction<typeof api.getDashboard>;
const mockGetUsageSummary = api.getUsageSummary as jest.MockedFunction<typeof api.getUsageSummary>;
const mockDeleteDataset = api.deleteDataset as jest.MockedFunction<typeof api.deleteDataset>;
const mockCreateCheckout = api.createCheckoutSession as jest.MockedFunction<typeof api.createCheckoutSession>;
const mockLogout = api.logout as jest.MockedFunction<typeof api.logout>;
const mockGetUser = authLib.getUser as jest.MockedFunction<typeof authLib.getUser>;
const mockGetRefreshToken = authLib.getRefreshToken as jest.MockedFunction<typeof authLib.getRefreshToken>;
const mockClearSession = authLib.clearSession as jest.MockedFunction<typeof authLib.clearSession>;

const FREE_USER: authLib.UserProfile = {
  id: "u1",
  email: "user@example.com",
  tier: "free",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

const EMPTY_DASH = { datasets: [], total: 0 };
const FREE_USAGE = { tier: "free" as const, monthly_generations_used: 2, monthly_generations_limit: 10 };
const FREE_USAGE_MAXED = { tier: "free" as const, monthly_generations_used: 10, monthly_generations_limit: 10 };
const PRO_USAGE = { tier: "pro" as const, monthly_generations_used: 50, monthly_generations_limit: null };

beforeEach(() => {
  jest.clearAllMocks();
  mockSearchParams.get.mockReturnValue(null);
});

describe("DashboardPage — unauthenticated", () => {
  it("redirects to /login when no user session", async () => {
    mockGetUser.mockReturnValue(null);
    render(<DashboardPage />);
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });
});

describe("DashboardPage — authenticated", () => {
  beforeEach(() => {
    mockGetUser.mockReturnValue(FREE_USER);
    mockGetDashboard.mockResolvedValue(EMPTY_DASH as any);
    mockGetUsageSummary.mockResolvedValue(FREE_USAGE as any);
  });

  it("renders user email in header", async () => {
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText("user@example.com")).toBeInTheDocument();
    });
  });

  it("shows free tier badge", async () => {
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText("Free")).toBeInTheDocument();
    });
  });

  it("shows empty state when no datasets", async () => {
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText(/no datasets yet/i)).toBeInTheDocument();
    });
  });

  it("displays usage count for free user", async () => {
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText(/2 \/ 10/i)).toBeInTheDocument();
    });
  });

  it("shows upgrade banner when upgrade=1 param present", async () => {
    mockSearchParams.get.mockImplementation((key: string) =>
      key === "upgrade" ? "1" : null
    );
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText(/upgrade to pro/i, { selector: "button" })).toBeInTheDocument();
    });
  });

  it("shows upgrade banner when free tier limit reached", async () => {
    mockGetUsageSummary.mockResolvedValue(FREE_USAGE_MAXED as any);
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText(/free tier limit reached/i)).toBeInTheDocument();
    });
  });

  it("renders dataset rows", async () => {
    const datasets = [
      { id: "ds-1", original_filename: "sales.csv", row_count: 1000, job_count: 3, created_at: "2026-03-01T00:00:00Z" },
      { id: "ds-2", original_filename: "customers.csv", row_count: 500, job_count: 1, created_at: "2026-03-15T00:00:00Z" },
    ];
    mockGetDashboard.mockResolvedValue({ datasets, total: 2 } as any);
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText("sales.csv")).toBeInTheDocument();
      expect(screen.getByText("customers.csv")).toBeInTheDocument();
    });
  });

  it("renders Pro badge for pro user", async () => {
    mockGetUser.mockReturnValue({ ...FREE_USER, tier: "pro" });
    mockGetUsageSummary.mockResolvedValue(PRO_USAGE as any);
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText("Pro")).toBeInTheDocument();
    });
  });

  it("logout calls api.logout and redirects to /login", async () => {
    mockGetRefreshToken.mockReturnValue("ref_tok_abc");
    mockLogout.mockResolvedValueOnce(undefined as any);
    render(<DashboardPage />);
    await waitFor(() => screen.getByText(/sign out/i));

    await userEvent.click(screen.getByText(/sign out/i));
    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledWith("ref_tok_abc");
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });

  it("clears session and redirects on 401 API error", async () => {
    mockGetDashboard.mockRejectedValueOnce(new api.ApiError(401, "Unauthorized"));
    render(<DashboardPage />);
    await waitFor(() => {
      expect(mockClearSession).toHaveBeenCalled();
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });

  it("shows error message on non-401 API error", async () => {
    mockGetDashboard.mockRejectedValueOnce(new api.ApiError(500, "Server error"));
    render(<DashboardPage />);
    await waitFor(() => {
      expect(screen.getByText(/failed to load dashboard/i)).toBeInTheDocument();
    });
  });

  it("deletes dataset after confirm", async () => {
    const datasets = [
      { id: "ds-del", original_filename: "todelete.csv", row_count: 100, job_count: 0, created_at: "2026-03-01T00:00:00Z" },
    ];
    mockGetDashboard.mockResolvedValue({ datasets, total: 1 } as any);
    mockDeleteDataset.mockResolvedValueOnce(undefined as any);
    // Second call for reload after delete
    mockGetDashboard.mockResolvedValueOnce(EMPTY_DASH as any);

    window.confirm = jest.fn().mockReturnValue(true);
    render(<DashboardPage />);

    await waitFor(() => screen.getByText("todelete.csv"));
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));

    await waitFor(() => {
      expect(mockDeleteDataset).toHaveBeenCalledWith("ds-del");
    });
  });

  it("does not delete when user cancels confirm", async () => {
    const datasets = [
      { id: "ds-keep", original_filename: "keep.csv", row_count: 100, job_count: 0, created_at: "2026-03-01T00:00:00Z" },
    ];
    mockGetDashboard.mockResolvedValue({ datasets, total: 1 } as any);
    window.confirm = jest.fn().mockReturnValue(false);

    render(<DashboardPage />);
    await waitFor(() => screen.getByText("keep.csv"));
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));

    expect(mockDeleteDataset).not.toHaveBeenCalled();
  });
});
