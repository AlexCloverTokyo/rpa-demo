# tests/test_selector_chaos.py
# Requires: docker-compose up -d (mock-site on port 8000)
import time
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from rpa.columns import (
    COL_DEPARTMENT,
    COL_EMAIL,
    COL_PERM_APPROVER,
    COL_PERM_EXPORT,
    COL_PERM_REPORT,
    COL_REQUEST_TYPE,
    COL_USERNAME,
    INTERNAL_SP_ID_KEY,
    REQ_CREATE,
)
from rpa.playwright_tasks import create_account, login_browser
from rpa.playwright_tasks_fragile import create_account_fragile

MOCK_URL = "http://localhost:8000"
CHAOS_CONFIG = Path("chaos_config.yaml")


@pytest.fixture(autouse=True)
def selector_chaos_enabled():
    original = CHAOS_CONFIG.read_text(encoding="utf-8")
    # chaos_config.yaml has a trailing comment on the enabled line
    modified = original.replace(
        "selector_chaos:\n  enabled: false",
        "selector_chaos:\n  enabled: true",
    )
    assert "selector_chaos:\n  enabled: true" in modified, \
        "chaos_config.yaml の selector_chaos フォーマットが想定外。fixture を確認してください。"
    CHAOS_CONFIG.write_text(modified, encoding="utf-8")
    yield
    CHAOS_CONFIG.write_text(original, encoding="utf-8")


@pytest.fixture
def fresh_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        login_browser(page, MOCK_URL)
        yield page
        browser.close()


def test_fragile_fails_under_selector_chaos(fresh_page):
    """With selector_chaos enabled, the fragile script (hard-coded #create-btn) raises TimeoutError.
    selector_chaos 有効時、固定 ID (#create-btn) に依存する脆弱スクリプトは TimeoutError になる"""
    with pytest.raises(PlaywrightTimeoutError):
        create_account_fragile(fresh_page, "chaos_fragile_user", "fragile@test.com", "Dev")


def test_robust_succeeds_under_selector_chaos(fresh_page):
    """With selector_chaos enabled, the robust script (role-based selector) still succeeds.
    selector_chaos 有効時でも、role ベースセレクタを使う強靭スクリプトは成功する"""
    username = f"chaos_robust_{int(time.time())}"
    result = create_account(fresh_page, {
        INTERNAL_SP_ID_KEY: "SP-CHAOS-01",
        COL_REQUEST_TYPE:   REQ_CREATE,
        COL_USERNAME:       username,
        COL_EMAIL:          f"{username}@test.com",
        COL_DEPARTMENT:     "開発部",
        COL_PERM_REPORT:    "○",
        COL_PERM_EXPORT:    "",
        COL_PERM_APPROVER:  "",
    })
    assert result["status"] == "success"
