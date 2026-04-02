"""Page Object Model for the job results/polling page."""

from playwright.sync_api import Page, expect


class ResultsPage:
    def __init__(self, page: Page):
        self.page = page

    def wait_for_completion(self, timeout_ms: int = 120_000):
        """Poll until the job status is no longer 'Queued' or 'Running'."""
        self.page.wait_for_function(
            """() => {
                const status = document.querySelector('[data-testid="job-status"]');
                return status && !['Queued', 'Running'].includes(status.textContent.trim());
            }""",
            timeout=timeout_ms,
        )

    def expect_quality_score_visible(self):
        expect(self.page.get_by_test_id("quality-score")).to_be_visible()

    def expect_quality_score_range(self):
        score_el = self.page.get_by_test_id("quality-score")
        score_text = score_el.inner_text()
        score = float(score_text.strip("%"))
        assert 0 <= score <= 100, f"Quality score out of range: {score}"

    def expect_per_column_bars_visible(self):
        bars = self.page.get_by_test_id("column-quality-bar")
        expect(bars.first).to_be_visible()

    def click_download(self):
        self.page.get_by_role("link", name="Download").click()

    def get_current_url(self) -> str:
        return self.page.url

    def expect_status(self, status: str):
        expect(self.page.get_by_test_id("job-status")).to_have_text(status)
