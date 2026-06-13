# Requires: docker-compose up -d (mock-site on port 8000)
import httpx
import pytest
from playwright.sync_api import sync_playwright

from rpa.playwright_tasks import _read_account_row, _refresh_accounts, _ui_find_account, _wait_chaos_loaded, login_browser

MOCK_URL = "http://localhost:8000"

TEST_USERNAME_HELPER = "ui_helper_test_user"
TEST_EMAIL_HELPER = "ui_helper_test@test.com"


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        login_browser(page, MOCK_URL)
        yield page
        browser.close()


@pytest.fixture(scope="module", autouse=True)
def helper_test_account():
    """Create a known account so _read_account_row has a fixed row to read."""
    httpx.delete(f"{MOCK_URL}/accounts/{TEST_EMAIL_HELPER}", timeout=5.0)
    resp = httpx.post(f"{MOCK_URL}/accounts", json={
        "username": TEST_USERNAME_HELPER,
        "email": TEST_EMAIL_HELPER,
        "department": "開発部",
        "permissions": ["report", "approver"],
    }, timeout=5.0)
    assert resp.status_code == 201, f"setup failed: {resp.text}"
    yield
    httpx.delete(f"{MOCK_URL}/accounts/{TEST_EMAIL_HELPER}", timeout=5.0)


def test_refresh_accounts_changes_version(browser_page):
    _wait_chaos_loaded(browser_page)
    body = browser_page.locator("#accounts-body")
    before = body.get_attribute("data-accounts-version")
    _refresh_accounts(browser_page)
    after = body.get_attribute("data-accounts-version")
    assert after != before


def test_read_account_row_reads_fields(browser_page):
    _wait_chaos_loaded(browser_page)
    _refresh_accounts(browser_page)
    browser_page.fill("#account-search", TEST_EMAIL_HELPER)
    browser_page.wait_for_function(
        """(email) => document.querySelector('#accounts-body').dataset.search === email""",
        arg=TEST_EMAIL_HELPER,
        timeout=10000,
    )
    row = browser_page.locator(
        f'#accounts-body tr:has(td:nth-child(4) div[title="{TEST_EMAIL_HELPER}"])'
    )
    assert row.count() == 1

    data = _read_account_row(row)
    assert data["username"] == TEST_USERNAME_HELPER
    assert data["department"] == "開発部"
    assert set(data["permissions"]) == {"report", "approver"}
    assert data["status"] == "active"

    browser_page.fill("#account-search", "")


def test_ui_find_account_found(browser_page):
    result = _ui_find_account(browser_page, TEST_EMAIL_HELPER)
    assert result is not None
    assert result["email"] == TEST_EMAIL_HELPER
    assert result["username"] == TEST_USERNAME_HELPER
    assert set(result["permissions"]) == {"report", "approver"}
    assert result["status"] == "active"


def test_ui_find_account_not_found(browser_page):
    result = _ui_find_account(browser_page, "definitely_not_in_db@test.com")
    assert result is None


def test_ui_find_account_partial_match_no_false_positive(browser_page):
    """Search term that partially matches another account's email but has no exact
    match must return None (regression test for the v4 'found || empty' timeout bug)."""
    partial = TEST_EMAIL_HELPER[:-1]  # "ui_helper_test@test.co" — substring match only
    result = _ui_find_account(browser_page, partial)
    assert result is None


def test_ui_find_account_email_with_quote(browser_page):
    """Email containing '"' must not break the CSS attribute selector (_css_escape_attr)."""
    quote_email = 'quote"user@test.com'
    resp = httpx.post(f"{MOCK_URL}/accounts", json={
        "username": "quote_test_user",
        "email": quote_email,
        "department": "開発部",
        "permissions": [],
    }, timeout=5.0)
    assert resp.status_code == 201, f"setup failed: {resp.text}"
    try:
        result = _ui_find_account(browser_page, quote_email)
        assert result is not None
        assert result["email"] == quote_email
    finally:
        httpx.delete(f"{MOCK_URL}/accounts/{quote_email}", timeout=5.0)
