/**
 * Page Object Model — Dashboard page
 * SAU-109: Phase 2 QA E2E scaffolding
 */
import { Page, Locator, expect } from "@playwright/test";

export class DashboardPage {
  readonly page: Page;
  readonly heading: Locator;
  readonly usageBadge: Locator;
  readonly upgradeButton: Locator;
  readonly logoutButton: Locator;
  readonly datasetsList: Locator;
  readonly upgradeBanner: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.getByRole("heading", { name: /your datasets|synthetic data generator/i });
    this.usageBadge = page.getByTestId("usage-badge").or(
      page.getByText(/generations this month/i)
    );
    this.upgradeButton = page.getByRole("button", { name: /upgrade to pro/i });
    this.logoutButton = page.getByRole("button", { name: /log out|sign out/i });
    this.datasetsList = page.getByRole("list").or(page.locator("table tbody"));
    this.upgradeBanner = page.getByText(/upgrade|pro plan/i);
  }

  async goto() {
    await this.page.goto("/dashboard");
  }

  async expectLoaded() {
    await expect(this.page).toHaveURL(/\/dashboard/);
    await expect(this.heading).toBeVisible({ timeout: 10_000 });
  }

  async expectRedirectedToLogin() {
    await expect(this.page).toHaveURL(/\/login/, { timeout: 5_000 });
  }

  async logout() {
    await this.logoutButton.click();
    await expect(this.page).toHaveURL(/\/login/, { timeout: 5_000 });
  }

  async expectUpgradeBannerVisible() {
    await expect(this.upgradeBanner).toBeVisible();
  }
}
