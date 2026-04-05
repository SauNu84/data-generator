/**
 * Page Object Model — API Keys page
 * SAU-109: Phase 2 QA E2E scaffolding
 */
import { Page, Locator, expect } from "@playwright/test";

export class ApiKeysPage {
  readonly page: Page;
  readonly createKeyButton: Locator;
  readonly keyNameInput: Locator;
  readonly confirmCreateButton: Locator;
  readonly keysList: Locator;
  readonly newKeyDisplay: Locator;
  readonly copyKeyButton: Locator;
  readonly revokeButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.createKeyButton = page.getByRole("button", { name: /create.*key|new.*key/i });
    this.keyNameInput = page.getByLabel(/key name|name/i);
    this.confirmCreateButton = page.getByRole("button", { name: /create|confirm/i });
    this.keysList = page.getByRole("table").or(page.getByTestId("api-keys-list"));
    this.newKeyDisplay = page.getByTestId("new-api-key").or(
      page.getByText(/sdg_/)
    );
    this.copyKeyButton = page.getByRole("button", { name: /copy/i });
    this.revokeButton = page.getByRole("button", { name: /revoke/i });
  }

  async goto() {
    await this.page.goto("/dashboard/api-keys");
  }

  async createKey(name: string) {
    await this.createKeyButton.click();
    await this.keyNameInput.fill(name);
    await this.confirmCreateButton.click();
  }

  async expectNewKeyShown() {
    await expect(this.newKeyDisplay).toBeVisible({ timeout: 10_000 });
  }

  async revokeFirstKey() {
    await this.revokeButton.first().click();
    await this.page.getByRole("button", { name: /confirm|yes/i }).click();
  }
}
