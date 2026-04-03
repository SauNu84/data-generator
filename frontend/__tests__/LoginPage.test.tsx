/**
 * Component tests for app/login/page.tsx — SAU-118
 * Coverage target: ≥80%
 *
 * Scenarios:
 *   - Renders form fields and Google OAuth button
 *   - Successful login redirects to /dashboard
 *   - API error displayed to user
 *   - Loading state disables submit button
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "@/app/login/page";
import * as api from "@/lib/api";

const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  login: jest.fn(),
  ApiError: jest.requireActual("@/lib/api").ApiError,
}));

const mockLogin = api.login as jest.MockedFunction<typeof api.login>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe("LoginPage", () => {
  it("renders email and password inputs", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("renders the Google OAuth button", () => {
    render(<LoginPage />);
    expect(screen.getByText(/continue with google/i)).toBeInTheDocument();
  });

  it("renders link to register page", () => {
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /register/i })).toBeInTheDocument();
  });

  it("calls login and redirects to /dashboard on success", async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValueOnce({
      access_token: "tok",
      refresh_token: "ref",
      token_type: "bearer",
      user: { id: "u1", email: "test@example.com", tier: "free", is_email_verified: false, created_at: "2026-01-01" },
    } as any);

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("test@example.com", "password123");
      expect(mockPush).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("shows error message on ApiError", async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValueOnce(new api.ApiError(401, "Invalid credentials."));

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "wrong@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrongpass");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials.")).toBeInTheDocument();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("shows generic error for non-ApiError exceptions", async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValueOnce(new Error("Network error"));

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText("Login failed.")).toBeInTheDocument();
    });
  });

  it("disables submit button while loading", async () => {
    const user = userEvent.setup();
    let resolveLogin!: () => void;
    mockLogin.mockReturnValueOnce(
      new Promise((res) => {
        resolveLogin = () => res({} as any);
      })
    );

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled();

    resolveLogin();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /signing in/i })).not.toBeInTheDocument();
    });
  });
});
