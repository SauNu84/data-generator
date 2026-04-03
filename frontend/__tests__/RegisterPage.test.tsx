/**
 * Component tests for app/register/page.tsx — SAU-118
 * Coverage target: ≥80%
 *
 * Scenarios:
 *   - Renders form fields and labels
 *   - Client-side validation for short password
 *   - Successful registration redirects to /dashboard
 *   - API error displayed to user
 *   - Loading state disables submit button
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RegisterPage from "@/app/register/page";
import * as api from "@/lib/api";

const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  register: jest.fn(),
  ApiError: jest.requireActual("@/lib/api").ApiError,
}));

const mockRegister = api.register as jest.MockedFunction<typeof api.register>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe("RegisterPage", () => {
  it("renders email and password fields", () => {
    render(<RegisterPage />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("renders link to sign-in page", () => {
    render(<RegisterPage />);
    expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
  });

  it("shows client-side validation error for short password", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "short");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    });
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it("calls register and redirects to /dashboard on success", async () => {
    const user = userEvent.setup();
    mockRegister.mockResolvedValueOnce({
      access_token: "tok",
      refresh_token: "ref",
      token_type: "bearer",
      user: { id: "u1", email: "user@example.com", tier: "free", is_email_verified: false, created_at: "2026-01-01" },
    } as any);

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "longenoughpassword");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith("user@example.com", "longenoughpassword");
      expect(mockPush).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("shows ApiError message on failure", async () => {
    const user = userEvent.setup();
    mockRegister.mockRejectedValueOnce(new api.ApiError(409, "Email already registered."));

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/email/i), "dup@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByText("Email already registered.")).toBeInTheDocument();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("shows generic error for non-ApiError exceptions", async () => {
    const user = userEvent.setup();
    mockRegister.mockRejectedValueOnce(new Error("Network error"));

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByText("Registration failed.")).toBeInTheDocument();
    });
  });

  it("disables submit button while loading", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    mockRegister.mockReturnValueOnce(
      new Promise((res) => {
        resolve = () => res({} as any);
      })
    );

    render(<RegisterPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "password123");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(screen.getByRole("button", { name: /creating account/i })).toBeDisabled();

    resolve();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /creating account/i })).not.toBeInTheDocument();
    });
  });
});
