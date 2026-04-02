"""Page Object Model for the CSV upload page."""

from playwright.sync_api import Page, expect


class UploadPage:
    def __init__(self, page: Page, base_url: str = "http://localhost:3000"):
        self.page = page
        self.base_url = base_url

    def navigate(self):
        self.page.goto(self.base_url)

    def upload_file(self, file_path: str):
        self.page.get_by_role("button", name="Upload").click()
        self.page.get_by_label("CSV file").set_input_files(file_path)

    def set_row_count(self, rows: int):
        self.page.get_by_label("Number of rows").fill(str(rows))

    def submit(self):
        self.page.get_by_role("button", name="Generate").click()

    def expect_error_banner(self, text: str):
        expect(self.page.get_by_role("alert")).to_contain_text(text)

    def expect_redirect_to_job_page(self):
        self.page.wait_for_url("**/jobs/**")
