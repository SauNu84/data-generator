"""
Playwright E2E conftest.

Requirements before running:
  - SAU-100 frontend must be deployed (Next.js app)
  - SAU-101 Docker Compose stack must be running: docker compose up
  - Install browsers: playwright install chromium

Run E2E tests: pytest tests/e2e/ -m e2e
"""

import os
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:3000")

# All E2E tests require the full stack to be running.
# If E2E_BASE_URL is not set or the app isn't reachable, skip gracefully.
pytestmark = pytest.mark.e2e
