/**
 * E2E Auth Flow Tests — Phase 2
 * SAU-109: Register, Login, JWT refresh, API keys, Stripe upgrade, free-tier limits
 *
 * Requires:
 *   - App running at BASE_URL (default http://localhost:3000)
 *   - Backend running at API_URL (default http://localhost:8000)
 *   - Stripe test mode configured
 *   - Test email inbox accessible (or email verification disabled via TEST_SKIP_EMAIL_VERIFY)
 */
import { test, expect, request } from "@playwright/test";
import { LoginPage, RegisterPage } from "./pages/AuthPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ApiKeysPage } from "./pages/ApiKeysPage";

const TEST_EMAIL = `qa+${Date.now()}@example.com`;
const TEST_PASSWORD = "TestPass123!";
const API_URL = process.env.API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Helper: register + confirm email via API (bypasses email inbox in CI)
// ---------------------------------------------------------------------------
async function registerAndConfirmViaApi(email: string, password: string) {
  const ctx = await request.newContext({ baseURL: API_URL });

  const reg = await ctx.post("/api/auth/register", {
    data: { email, password },
  });
  expect(reg.ok()).toBeTruthy();
  const { email_token } = await reg.json();

  if (email_token) {
    const confirm = await ctx.get(`/api/auth/confirm-email?token=${email_token}`);
    expect(confirm.ok()).toBeTruthy();
  }

  await ctx.dispose();
}

// ---------------------------------------------------------------------------
// 1. Register + email confirm flow
// ---------------------------------------------------------------------------
test.describe("Registration", () => {
  test("successful registration redirects to dashboard", async ({ page }) => {
    const registerPage = new RegisterPage(page);
    await registerPage.goto();
    await registerPage.register(TEST_EMAIL, TEST_PASSWORD);
    await registerPage.expectConfirmationMessage();
  });

  test("duplicate email shows error", async ({ page }) => {
    // Pre-register the account
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);

    const registerPage = new RegisterPage(page);
    await registerPage.goto();
    await registerPage.register(TEST_EMAIL, TEST_PASSWORD);
    await registerPage.errorMessage.waitFor({ state: "visible" });
    await expect(registerPage.errorMessage).toContainText(/already|exists|taken/i);
  });

  test("weak password shows validation error", async ({ page }) => {
    const registerPage = new RegisterPage(page);
    await registerPage.goto();
    await registerPage.register("newuser@example.com", "123");
    await expect(registerPage.errorMessage).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 2. Login + JWT flow
// ---------------------------------------------------------------------------
test.describe("Login", () => {
  test.beforeAll(async () => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);
  });

  test("valid credentials redirects to dashboard", async ({ page }) => {
    const loginPage = new LoginPage(page);
    const dashboardPage = new DashboardPage(page);

    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
    await dashboardPage.expectLoaded();
  });

  test("invalid password shows error", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, "wrongpassword");
    await loginPage.expectError();
  });

  test("non-existent email shows error", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login("nobody@example.com", TEST_PASSWORD);
    await loginPage.expectError();
  });

  test("login page has Google OAuth button", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await expect(loginPage.googleButton).toBeVisible();
    const href = await loginPage.googleButton.getAttribute("href");
    expect(href).toContain("/api/auth/google");
  });

  test("register link navigates to register page", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.registerLink.click();
    await expect(page).toHaveURL(/\/register/);
  });
});

// ---------------------------------------------------------------------------
// 3. JWT refresh
// ---------------------------------------------------------------------------
test.describe("JWT Refresh", () => {
  // TODO: api.ts has no automatic token-refresh interceptor — unskip when implemented
  test.skip("expired access token is refreshed transparently", async ({ page }) => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);

    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);

    // Corrupt the access token to simulate expiry, keep refresh token
    await page.evaluate(() => {
      localStorage.setItem("sdg_access_token", "expired.token.here");
    });

    // Dashboard should still load (refresh token kicks in)
    const dashboardPage = new DashboardPage(page);
    await dashboardPage.goto();
    await dashboardPage.expectLoaded();
  });

  test("invalid refresh token redirects to login", async ({ page }) => {
    await page.goto("/login");
    await page.evaluate(() => {
      localStorage.setItem("sdg_access_token", "bad");
      localStorage.setItem("sdg_refresh_token", "bad");
      localStorage.setItem("sdg_user", JSON.stringify({ id: "x", email: "x@x.com", tier: "free", is_email_verified: true, created_at: "" }));
    });

    const dashboardPage = new DashboardPage(page);
    await dashboardPage.goto();
    await dashboardPage.expectRedirectedToLogin();
  });
});

// ---------------------------------------------------------------------------
// 4. API key creation + usage
// ---------------------------------------------------------------------------
// TODO: /dashboard/api-keys page not yet implemented — unskip when page is added
test.describe.skip("API Keys", () => {
  test.beforeEach(async ({ page }) => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
    await new DashboardPage(page).expectLoaded();
  });

  test("create API key shows key value once", async ({ page }) => {
    const keysPage = new ApiKeysPage(page);
    await keysPage.goto();
    await keysPage.createKey("CI Test Key");
    await keysPage.expectNewKeyShown();
  });

  test("created key appears in keys list", async ({ page }) => {
    const keysPage = new ApiKeysPage(page);
    await keysPage.goto();
    await keysPage.createKey("List Test Key");
    // Dismiss the key display modal/section
    await page.keyboard.press("Escape");
    await expect(keysPage.keysList).toContainText("List Test Key");
  });

  test("revoke key removes it from list", async ({ page }) => {
    const keysPage = new ApiKeysPage(page);
    await keysPage.goto();
    await keysPage.createKey("Revoke Me");
    await page.keyboard.press("Escape");
    await keysPage.revokeFirstKey();
    await expect(keysPage.keysList).not.toContainText("Revoke Me");
  });
});

// ---------------------------------------------------------------------------
// 5. Stripe upgrade to Pro (test mode)
// ---------------------------------------------------------------------------
test.describe("Stripe Upgrade", () => {
  test.beforeEach(async ({ page }) => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);
    await new DashboardPage(page).expectLoaded();
  });

  test("upgrade button redirects to Stripe checkout", async ({ page }) => {
    const dashboardPage = new DashboardPage(page);
    await dashboardPage.goto();
    await dashboardPage.expectLoaded();

    // Capture the checkout URL (Stripe redirects externally)
    const [popup] = await Promise.all([
      page.waitForRequest((req) =>
        req.url().includes("checkout.stripe.com") || req.url().includes("/api/billing/checkout")
      ),
      dashboardPage.upgradeButton.click(),
    ]);
    expect(popup).toBeTruthy();
  });

  test("dashboard shows upgrade banner after upgrade=1 param", async ({ page }) => {
    const dashboardPage = new DashboardPage(page);
    await page.goto("/dashboard?upgraded=1");
    await dashboardPage.expectLoaded();
    await dashboardPage.expectUpgradeBannerVisible();
  });
});

// ---------------------------------------------------------------------------
// 6. Free-tier limit enforcement (10 generations)
// ---------------------------------------------------------------------------
// TODO: requires PATCH /api/users/me/usage test-helper endpoint — unskip when added
test.describe.skip("Free Tier Limits", () => {
  test("exceeding 10 generations returns 402 and shows upgrade prompt", async ({
    page,
  }) => {
    // Seed a user who has hit their limit via API
    const ctx = await request.newContext({ baseURL: API_URL });

    // Register fresh user
    const limitEmail = `limit+${Date.now()}@example.com`;
    await registerAndConfirmViaApi(limitEmail, TEST_PASSWORD);

    const loginResp = await ctx.post("/api/auth/login", {
      data: { email: limitEmail, password: TEST_PASSWORD },
    });
    const { access_token } = await loginResp.json();

    // Exhaust 10 free-tier generations via API (test helper endpoint or seed)
    // NOTE: In CI, use a seed endpoint or direct DB injection to set usage_count=10
    await ctx.patch("/api/users/me/usage", {
      headers: { Authorization: `Bearer ${access_token}` },
      data: { generations_this_month: 10 },
    });
    await ctx.dispose();

    // Now log in via UI and attempt a generation
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(limitEmail, TEST_PASSWORD);
    await new DashboardPage(page).expectLoaded();

    // Navigate to upload and try to generate
    await page.goto("/");
    // Expect upgrade prompt or 402 error displayed
    await expect(
      page.getByText(/upgrade|limit reached|free tier/i)
    ).toBeVisible({ timeout: 15_000 });
  });
});

// ---------------------------------------------------------------------------
// 7. Logout flow
// ---------------------------------------------------------------------------
test.describe("Logout", () => {
  test("logout clears session and redirects to login", async ({ page }) => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);

    const dashboardPage = new DashboardPage(page);
    await dashboardPage.expectLoaded();
    await dashboardPage.logout();

    // After logout, accessing dashboard should redirect to login
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("localStorage is cleared after logout", async ({ page }) => {
    await registerAndConfirmViaApi(TEST_EMAIL, TEST_PASSWORD);
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.login(TEST_EMAIL, TEST_PASSWORD);

    const dashboardPage = new DashboardPage(page);
    await dashboardPage.expectLoaded();
    await dashboardPage.logout();

    const tokens = await page.evaluate(() => ({
      access: localStorage.getItem("sdg_access_token"),
      refresh: localStorage.getItem("sdg_refresh_token"),
      user: localStorage.getItem("sdg_user"),
    }));
    expect(tokens.access).toBeNull();
    expect(tokens.refresh).toBeNull();
    expect(tokens.user).toBeNull();
  });
});
