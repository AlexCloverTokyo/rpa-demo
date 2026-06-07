# tests/test_playwright.py
# Requires: docker-compose up -d (mock-site on port 8000)
import httpx
import pytest
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
    REQ_ADD,
    REQ_CREATE,
    REQ_REMOVE,
)
from rpa.playwright_tasks import (
    MAX_EMAIL_LEN,
    MAX_USERNAME_LEN,
    _validate_lengths,
    add_permission,
    create_account,
    login_browser,
    remove_permission,
)

MOCK_URL      = "http://localhost:8000"
TEST_USERNAME = "playwright_test_user_001"
TEST_EMAIL    = "pt001@test.com"

# Separate user for the partial-grant test (A-5) / 部分付与テスト(A-5)専用ユーザー
TEST_USERNAME_A5 = "playwright_test_a5_user"
TEST_EMAIL_A5    = "pt_a5@test.com"

# Separate user for remove_permission tests (R-*) / 権限削除テスト(R-*)専用ユーザー
TEST_USERNAME_R = "playwright_test_remove_user"
TEST_EMAIL_R    = "pt_remove@test.com"

# New user created without any permissions (tests zero-perms add flow) / 権限なし新規ユーザー（初回権限付与フローの検証用）
TEST_USERNAME_FRESH = "playwright_test_fresh_user"
TEST_EMAIL_FRESH    = "pt_fresh@test.com"

# Inactive account tests (add→error, remove→allowed) / 非アクティブアカウントテスト（追加→エラー、削除→成功）
TEST_USERNAME_INACTIVE = "playwright_test_inactive_user"
TEST_EMAIL_INACTIVE    = "pt_inactive@test.com"

# New user with no permissions for create test / 権限なしアカウント作成テスト用ユーザー
TEST_USERNAME_NOPERMS = "playwright_test_noperms_user"
TEST_EMAIL_NOPERMS    = "pt_noperms@test.com"


@pytest.fixture(scope="module", autouse=True)
def clean_test_users():
    """Delete all test users before the module runs to ensure a clean state."""
    for uname in (
        TEST_USERNAME, TEST_USERNAME_A5, TEST_USERNAME_R,
        TEST_USERNAME_FRESH, TEST_USERNAME_INACTIVE, TEST_USERNAME_NOPERMS,
    ):
        httpx.delete(f"{MOCK_URL}/accounts/{uname}", timeout=5.0)
    yield


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        login_browser(page, MOCK_URL)
        yield page
        browser.close()


@pytest.fixture(scope="module")
def fresh_user_no_perms():
    """Create a user with zero permissions via API (tests the zero-perms add flow)."""
    resp = httpx.post(f"{MOCK_URL}/accounts", json={
        "username": TEST_USERNAME_FRESH,
        "email":    TEST_EMAIL_FRESH,
        "department": "開発部",
        "permissions": [],
    }, timeout=5.0)
    assert resp.status_code == 201, f"fresh_user setup failed: {resp.text}"
    yield TEST_EMAIL_FRESH
    httpx.delete(f"{MOCK_URL}/accounts/{TEST_USERNAME_FRESH}", timeout=5.0)


@pytest.fixture(scope="module")
def inactive_test_user():
    """Create a user with [report] permission, then mark inactive.
    Used for: add_permission → blocked; remove_permission → allowed."""
    resp = httpx.post(f"{MOCK_URL}/accounts", json={
        "username": TEST_USERNAME_INACTIVE,
        "email":    TEST_EMAIL_INACTIVE,
        "department": "開発部",
        "permissions": ["report"],
    }, timeout=5.0)
    assert resp.status_code == 201, f"inactive_user setup failed: {resp.text}"
    httpx.patch(
        f"{MOCK_URL}/accounts/{TEST_USERNAME_INACTIVE}/status",
        json={"status": "inactive"}, timeout=5.0,
    )
    yield TEST_EMAIL_INACTIVE
    httpx.delete(f"{MOCK_URL}/accounts/{TEST_USERNAME_INACTIVE}", timeout=5.0)


# ---------------------------------------------------------------------------
# Row builders / 行ビルダー
# ---------------------------------------------------------------------------

def _row_create(sp_id: str, username: str, email: str, dept: str,
                report="", export="", approver="") -> dict:
    return {
        INTERNAL_SP_ID_KEY: sp_id,
        COL_REQUEST_TYPE:   REQ_CREATE,
        COL_USERNAME:       username,
        COL_EMAIL:          email,
        COL_DEPARTMENT:     dept,
        COL_PERM_REPORT:    report,
        COL_PERM_EXPORT:    export,
        COL_PERM_APPROVER:  approver,
    }


def _row_add(sp_id: str, email: str, report="", export="", approver="") -> dict:
    return {
        INTERNAL_SP_ID_KEY: sp_id,
        COL_REQUEST_TYPE:   REQ_ADD,
        COL_USERNAME:       "",
        COL_EMAIL:          email,
        COL_DEPARTMENT:     "",
        COL_PERM_REPORT:    report,
        COL_PERM_EXPORT:    export,
        COL_PERM_APPROVER:  approver,
    }


def _row_remove(sp_id: str, email: str, report="", export="", approver="") -> dict:
    return {
        INTERNAL_SP_ID_KEY: sp_id,
        COL_REQUEST_TYPE:   REQ_REMOVE,
        COL_USERNAME:       "",
        COL_EMAIL:          email,
        COL_DEPARTMENT:     "",
        COL_PERM_REPORT:    report,
        COL_PERM_EXPORT:    export,
        COL_PERM_APPROVER:  approver,
    }


# ===========================================================================
# create_account tests / アカウント作成テスト
# ===========================================================================

def test_create_account_success(browser_page):
    result = create_account(browser_page, _row_create(
        "SP-TEST-01", TEST_USERNAME, TEST_EMAIL, "開発部", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    assert result["id"] == "SP-TEST-01"


def test_create_account_idempotent(browser_page):
    """User already exists → skipped regardless of permissions."""
    result = create_account(browser_page, _row_create(
        "SP-TEST-02", TEST_USERNAME, TEST_EMAIL, "開発部", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_exists"


def test_create_account_missing_fields(browser_page):
    """Missing required fields → error before any browser interaction."""
    result = create_account(browser_page, _row_create(
        "SP-TEST-07", "missing_fields_user", "", "開発部"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "メールアドレス" in result["message"]


# ---------------------------------------------------------------------------
# A-1: create — missing ユーザー名
# ---------------------------------------------------------------------------
def test_create_account_missing_username(browser_page):
    result = create_account(browser_page, _row_create(
        "SP-TEST-A1", "", "missing_name@test.com", "開発部"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "ユーザー名" in result["message"]


# ---------------------------------------------------------------------------
# A-2: create — missing 部署
# ---------------------------------------------------------------------------
def test_create_account_missing_department(browser_page):
    result = create_account(browser_page, _row_create(
        "SP-TEST-A2", "missing_dept_user", "missing_dept@test.com", ""
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "部署" in result["message"]


# ---------------------------------------------------------------------------
# A-3: create — all required fields empty / 全必須項目が空
# ---------------------------------------------------------------------------
def test_create_account_all_required_empty(browser_page):
    result = create_account(browser_page, _row_create(
        "SP-TEST-A3", "", "", ""
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "ユーザー名"     in result["message"]
    assert "メールアドレス" in result["message"]
    assert "部署"           in result["message"]


# ---------------------------------------------------------------------------
# A-N1: create — no permissions (all perm cols empty) → success
# Permissions are optional for new accounts; this is the basic new-user flow.
# 権限なし（全権限列が空）でも成功する。権限は新規アカウントには任意項目。
# ---------------------------------------------------------------------------
def test_create_account_no_permissions(browser_page):
    """Create a brand-new user with no permissions — permissions are optional."""
    result = create_account(browser_page, _row_create(
        "SP-TEST-AN1", TEST_USERNAME_NOPERMS, TEST_EMAIL_NOPERMS, "総務部"
        # all perm cols default to "" (no ○)
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    # Verify via API that user exists with empty permissions
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_NOPERMS}", timeout=5.0)
    assert resp.status_code == 200
    assert resp.json()["permissions"] == []


# ===========================================================================
# add_permission tests / 権限追加テスト
# ===========================================================================

def test_add_permission_success(browser_page):
    result = add_permission(browser_page, _row_add(
        "SP-TEST-03", TEST_EMAIL, approver="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    assert result["id"] == "SP-TEST-03"
    assert "perms_before" in result
    assert "perms_after"  in result
    assert "approver" in result["perms_after"]


def test_add_permission_already_granted(browser_page):
    """All requested permissions already present → skipped."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-04", TEST_EMAIL, approver="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_granted"
    assert result["perms_before"] == result["perms_after"]


def test_add_permission_user_not_found(browser_page):
    """Non-existent email → error with account_not_found."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-05", "nonexistent_xyz@test.com", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert result["message"] == "account_not_found"


def test_add_permission_multiple(browser_page):
    """Grant multiple permissions at once."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-06", TEST_EMAIL, report="○", export="○", approver="○"
    ), mock_url=MOCK_URL)
    assert result["status"] in ("success", "skipped")


def test_add_permission_missing_email(browser_page):
    """Empty email → error before any browser interaction."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-08", "", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "メールアドレス" in result["message"]


# ---------------------------------------------------------------------------
# A-4: add — no permissions marked → error (not skipped) / 権限列がすべて空 → error（skippedではない）
# ---------------------------------------------------------------------------
def test_add_permission_no_perms_error(browser_page):
    """No permissions in CSV row → error with '権限列がすべて空です'."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-A4", TEST_EMAIL  # all perm cols empty
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "権限列がすべて空です" in result["message"]


# ---------------------------------------------------------------------------
# A-5: add — partial add; existing permission must be preserved (grant-only)
# 部分追加：既存権限は保持されること（grant-only、上書きしない）
# ---------------------------------------------------------------------------
def test_add_permission_partial_add(browser_page):
    """Add one permission while keeping the existing one (grant-only)."""
    res = create_account(browser_page, _row_create(
        "SP-TEST-A5-0", TEST_USERNAME_A5, TEST_EMAIL_A5, "開発部", report="○"
    ), mock_url=MOCK_URL)
    assert res["status"] == "success", f"Setup failed: {res}"

    result = add_permission(browser_page, _row_add(
        "SP-TEST-A5-1", TEST_EMAIL_A5, approver="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"

    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_A5}", timeout=5.0)
    assert resp.status_code == 200
    perms = set(resp.json()["permissions"])
    assert "report"   in perms, f"'report' should be preserved, got {perms}"
    assert "approver" in perms, f"'approver' should be added, got {perms}"


# ---------------------------------------------------------------------------
# A-N2: add — fresh user with zero existing permissions (新規ユーザー初回権限付与)
# perms_before must be [], perms_after must include the new permission.
# ---------------------------------------------------------------------------
def test_add_permission_fresh_user_zero_perms(browser_page, fresh_user_no_perms):
    """Add first-ever permission to a user who has none (new-user onboarding flow)."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-AN2", fresh_user_no_perms, export="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    assert result["perms_before"] == [], f"new user should have no prior perms, got {result['perms_before']}"
    assert "export" in result["perms_after"]
    # Cross-verify via API
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_FRESH}", timeout=5.0)
    assert resp.status_code == 200
    assert "export" in resp.json()["permissions"]


# ---------------------------------------------------------------------------
# A-N3: add — inactive account → error (cannot grant perms to disabled account)
# 非アクティブアカウントへの権限追加 → error（無効アカウントへの付与は不可）
# ---------------------------------------------------------------------------
def test_add_permission_inactive_account(browser_page, inactive_test_user):
    """add_permission to an inactive account must be rejected."""
    result = add_permission(browser_page, _row_add(
        "SP-TEST-AN3", inactive_test_user, export="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "無効" in result["message"], f"Expected inactive error, got: {result['message']}"
    # Confirm the account's permissions are unchanged (still [report])
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_INACTIVE}", timeout=5.0)
    assert "report" in resp.json()["permissions"]


# ===========================================================================
# remove_permission tests / 権限削除テスト
# State of remove_test_user (TEST_EMAIL_R) through the suite:
# remove_test_user (TEST_EMAIL_R) の権限状態推移:
#   fixture setup : [report, export]
#   after R1      : [export]          (report removed)
#   after R2      : [export]          (skipped)
#   after R3 setup: [export, approver]
#   after R3      : [export]          (approver removed via partial overlap)
#   after R-N1    : []                (export removed → all perms gone)
# ===========================================================================

@pytest.fixture(scope="module")
def remove_test_user(browser_page):
    """Create a user with report+export permissions for remove tests."""
    res = create_account(browser_page, _row_create(
        "SP-TEST-R0", TEST_USERNAME_R, TEST_EMAIL_R, "開発部",
        report="○", export="○"
    ), mock_url=MOCK_URL)
    assert res["status"] == "success", f"Remove-user setup failed: {res}"
    return TEST_EMAIL_R


def test_remove_permission_success(browser_page, remove_test_user):
    """Remove one permission; remaining permission must be preserved.
    State: [report, export] → [export]"""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R1", remove_test_user, report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    assert "perms_before" in result
    assert "perms_after"  in result
    assert "report" not in result["perms_after"]
    assert "export" in     result["perms_after"], "export must be preserved"
    # Cross-verify via API
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_R}", timeout=5.0)
    assert resp.status_code == 200
    api_perms = set(resp.json()["permissions"])
    assert "report" not in api_perms, f"API still has 'report': {api_perms}"
    assert "export" in     api_perms, f"API lost 'export': {api_perms}"


def test_remove_permission_already_removed(browser_page, remove_test_user):
    """Permission already absent → skipped/already_removed.
    State: [export] (report was removed in R1)"""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R2", remove_test_user, report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "skipped"
    assert result["reason"] == "already_removed"
    assert result["perms_before"] == result["perms_after"]


def test_remove_permission_partial_overlap(browser_page, remove_test_user):
    """Remove [report, approver]; report already gone → removes only approver.
    State before: [export, approver]; after: [export]"""
    # grant approver first so we have something to remove
    add_permission(browser_page, _row_add(
        "SP-TEST-R3-setup", remove_test_user, approver="○"
    ), mock_url=MOCK_URL)

    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R3", remove_test_user, report="○", approver="○"
    ), mock_url=MOCK_URL)
    # report is already gone; approver is present → success, removes approver
    assert result["status"] == "success"
    assert "approver" not in result["perms_after"]
    assert "export"   in     result["perms_after"], "export must survive"


# ---------------------------------------------------------------------------
# R-N1: remove — strip ALL permissions → perms_after must be []
# Design specifies: empty permission list is valid (空配列も正常な結果として許可).
# State before: [export]; after: []
# ---------------------------------------------------------------------------
def test_remove_permission_remove_all(browser_page, remove_test_user):
    """Remove the last remaining permission; result must be an empty list."""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-RN1", remove_test_user, export="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success"
    assert result["perms_after"] == [], f"Expected [], got {result['perms_after']}"
    # Cross-verify via API
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_R}", timeout=5.0)
    assert resp.status_code == 200
    assert resp.json()["permissions"] == [], \
        f"API perms should be empty, got {resp.json()['permissions']}"


def test_remove_permission_user_not_found(browser_page):
    """Non-existent email → error with account_not_found."""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R4", "nonexistent_xyz@test.com", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert result["message"] == "account_not_found"


def test_remove_permission_missing_email(browser_page):
    """Empty email → error before any browser interaction."""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R5", "", report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "メールアドレス" in result["message"]


def test_remove_permission_no_perms_error(browser_page):
    """No permissions marked → error with '権限列がすべて空です'."""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-R6", TEST_EMAIL  # all perm cols empty
    ), mock_url=MOCK_URL)
    assert result["status"] == "error"
    assert "権限列がすべて空です" in result["message"]


# ---------------------------------------------------------------------------
# R-N2: remove — inactive account → must succeed
# Design: removing permissions from an inactive account is allowed.
# 設計: 「非アクティブアカウントの権限削除は許可する」
# Uses inactive_test_user which has [report] and status=inactive.
# Note: run AFTER test_add_permission_inactive_account (which doesn't change state).
# ---------------------------------------------------------------------------
def test_remove_permission_inactive_allowed(browser_page, inactive_test_user):
    """remove_permission on an inactive account must succeed (unlike add)."""
    result = remove_permission(browser_page, _row_remove(
        "SP-TEST-RN2", inactive_test_user, report="○"
    ), mock_url=MOCK_URL)
    assert result["status"] == "success", \
        f"Expected success for inactive remove, got: {result}"
    assert "report" not in result["perms_after"]
    # Cross-verify via API
    resp = httpx.get(f"{MOCK_URL}/accounts/{TEST_USERNAME_INACTIVE}", timeout=5.0)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "inactive"
    assert "report" not in data["permissions"], \
        f"report should be removed from inactive account, got {data['permissions']}"


# ---------------------------------------------------------------------------
# Length validation unit tests — no browser / Docker required.
# Validation fires before any network or UI interaction.
# 長さバリデーション単体テスト。ブラウザ・Docker不要。
# ---------------------------------------------------------------------------

class TestLengthValidation:
    """Unit tests for _validate_lengths and fail-fast behavior in RPA functions."""

    # --- _validate_lengths helper ---

    def test_at_limit_no_errors(self):
        """Exactly at max lengths → no errors."""
        assert _validate_lengths(
            username="a" * MAX_USERNAME_LEN,
            email="b" * (MAX_EMAIL_LEN - 12) + "@example.com",
        ) == []

    def test_one_over_username_limit(self):
        """username = MAX+1 → 1 error containing the character counts."""
        errors = _validate_lengths(username="a" * (MAX_USERNAME_LEN + 1), email="ok@example.com")
        assert len(errors) == 1
        assert str(MAX_USERNAME_LEN + 1) in errors[0]
        assert str(MAX_USERNAME_LEN) in errors[0]

    def test_one_over_email_limit(self):
        """email = MAX+1 → 1 error containing the character counts."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"  # 201 chars total
        errors = _validate_lengths(email=long_email)
        assert len(errors) == 1
        assert str(MAX_EMAIL_LEN + 1) in errors[0]
        assert str(MAX_EMAIL_LEN) in errors[0]

    def test_both_over_limit(self):
        """Both fields over limit → 2 errors."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"
        errors = _validate_lengths(
            username="a" * (MAX_USERNAME_LEN + 1),
            email=long_email,
        )
        assert len(errors) == 2

    def test_empty_username_skipped(self):
        """Empty username (default) is skipped → no username error."""
        assert _validate_lengths(email="ok@example.com") == []

    def test_empty_email_skipped(self):
        """Empty email (default) is skipped → no email error."""
        assert _validate_lengths(username="validuser") == []

    # --- create_account fail-fast ---

    def _row_create(self, sp_id: str = "SP-LEN-TEST", **overrides) -> dict:
        base = {
            INTERNAL_SP_ID_KEY: sp_id,
            COL_REQUEST_TYPE:   REQ_CREATE,
            COL_USERNAME:       "validuser",
            COL_EMAIL:          "valid@example.com",
            COL_DEPARTMENT:     "開発部",
        }
        base.update(overrides)
        return base

    def test_create_username_too_long(self):
        """create_account with 101-char username returns error before browser opens."""
        result = create_account(None, self._row_create(**{COL_USERNAME: "a" * (MAX_USERNAME_LEN + 1)}),
                                mock_url="http://not-used")
        assert result["status"] == "error"
        assert "長すぎます" in result["message"]
        assert "ユーザー名" in result["message"]

    def test_create_email_too_long(self):
        """create_account with 201-char email returns error before browser opens."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"
        result = create_account(None, self._row_create(**{COL_EMAIL: long_email}),
                                mock_url="http://not-used")
        assert result["status"] == "error"
        assert "長すぎます" in result["message"]
        assert "メールアドレス" in result["message"]

    def test_create_both_too_long(self):
        """create_account with both fields over limit → single error message covering both."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"
        result = create_account(None, self._row_create(**{
            COL_USERNAME: "a" * (MAX_USERNAME_LEN + 1),
            COL_EMAIL:    long_email,
        }), mock_url="http://not-used")
        assert result["status"] == "error"
        assert "ユーザー名" in result["message"]
        assert "メールアドレス" in result["message"]

    # --- add_permission fail-fast ---

    def test_add_permission_email_too_long(self):
        """add_permission with 201-char email returns error before browser opens."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"
        row = {
            INTERNAL_SP_ID_KEY: "SP-LEN-TEST",
            COL_REQUEST_TYPE:   REQ_ADD,
            COL_EMAIL:          long_email,
            COL_PERM_REPORT:    "○",
        }
        result = add_permission(None, row, mock_url="http://not-used")
        assert result["status"] == "error"
        assert "長すぎます" in result["message"]
        assert "メールアドレス" in result["message"]

    # --- remove_permission fail-fast ---

    def test_remove_permission_email_too_long(self):
        """remove_permission with 201-char email returns error before browser opens."""
        long_email = "b" * (MAX_EMAIL_LEN - 11) + "@example.com"
        row = {
            INTERNAL_SP_ID_KEY: "SP-LEN-TEST",
            COL_REQUEST_TYPE:   REQ_REMOVE,
            COL_EMAIL:          long_email,
            COL_PERM_REPORT:    "○",
        }
        result = remove_permission(None, row, mock_url="http://not-used")
        assert result["status"] == "error"
        assert "長すぎます" in result["message"]
        assert "メールアドレス" in result["message"]
