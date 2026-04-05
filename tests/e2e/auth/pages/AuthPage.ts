/**
 * Page Object Model — Auth pages (Login + Register + OAuth callback)
 * SAU-109: Phase 2 QA E2E scaffolding
 */
import { Page, Locator, expect } from "@playwright/test";

export class LoginPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;
  readonly googleButton: Locator;
  readonly registerLink: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.getByLabel("Email");
    this.passwordInput = page.getByLabel("Password");
    this.submitButton = page.getByRole("button", { name: /sign in/i });
    this.errorMessage = page.getByRole("alert").or(
      page.locator(".bg-red-50")
    );
    this.googleButton = page.getByRole("link", { name: /continue with google/i });
    this.registerLink = page.getByRole("link", { name: /register/i });
  }

  async goto() {
    await this.page.goto("/login");
    await expect(this.page.getByRole("heading", { name: /sign in/i })).toBeVisible();
  }

  async login(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async expectError(message?: string) {
    await expect(this.errorMessage).toBeVisible();
    if (message) {
      await expect(this.errorMessage).toContainText(message);
    }
  }
}

export class RegisterPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly errorMessage: Locator;
  readonly loginLink: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.getByLabel("Email");
    this.passwordInput = page.getByLabel("Password");
    this.submitButton = page.getByRole("button", { name: /register|create account|sign up/i });
    this.errorMessage = page.locator(".bg-red-50").or(page.getByRole("alert"));
    this.loginLink = page.getByRole("link", { name: /sign in|login/i });
  }

  async goto() {
    await this.page.goto("/register");
    await expect(
      this.page.getByRole("heading", { name: /register|create account|sign up/i })
    ).toBeVisible();
  }

  async register(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async expectConfirmationMessage() {
    // Registration auto-logs in and redirects to dashboard (no email confirmation step)
    await expect(this.page).toHaveURL(/\/dashboard/, { timeout: 10_000 });
  }
}
