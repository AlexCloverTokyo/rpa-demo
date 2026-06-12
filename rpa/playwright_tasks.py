import os
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import Locator, Page

from .columns import (
    COL_DEPARTMENT,
    COL_EMAIL,
    COL_USERNAME,
    INTERNAL_SP_ID_KEY,
    PERM_COLUMNS,
    REQ_ADD,
    REQ_CREATE,
    REQ_REMOVE,
    REQUIRED_FIELDS,
)

# ---------------------------------------------------------------------------
# Output directory / 出力ディレクトリ
# ---------------------------------------------------------------------------
RESULTS_DIR = Path("rpa/results")

# ---------------------------------------------------------------------------
# Timeouts / タイムアウト設定 (milliseconds)
# ---------------------------------------------------------------------------
CHAOS_LOAD_TIMEOUT_MS    = int(os.environ.get("RPA_CHAOS_LOAD_TIMEOUT_MS",    "10000"))
MODAL_VISIBLE_TIMEOUT_MS = int(os.environ.get("RPA_MODAL_VISIBLE_TIMEOUT_MS", "5000"))
STATUS_MSG_TIMEOUT_MS    = int(os.environ.get("RPA_STATUS_MSG_TIMEOUT_MS",    "15000"))
VERIFY_TIMEOUT_MS        = int(os.environ.get("RPA_VERIFY_TIMEOUT_MS",        "10000"))

# httpx timeout for pre-flight API checks (seconds) / 事前APIチェック用タイムアウト(秒)
API_REQUEST_TIMEOUT = float(os.environ.get("RPA_API_REQUEST_TIMEOUT", "15.0"))

# ---------------------------------------------------------------------------
# Backend field length limits — must mirror mock_site/backend/main.py Pydantic models.
# バックエンドのフィールド長制限。main.py の Pydantic モデルと一致させること。
# ---------------------------------------------------------------------------
MAX_USERNAME_LEN = 100  # AccountCreate / AccountUpdate: username max_length=100
MAX_EMAIL_LEN    = 200  # AccountCreate / AccountUpdate: email    max_length=200


# ---------------------------------------------------------------------------
# Internal helpers / 内部ユーティリティ
# ---------------------------------------------------------------------------

def _parse_permissions(row: dict) -> set[str]:
    """Extract granted permissions from the per-column ○/blank CSV format.
    / 列ごとの○/空白形式CSVから付与権限セットを抽出する。"""
    return {perm for col, perm in PERM_COLUMNS.items() if str(row.get(col, "")).strip() == "○"}


def _sp_id(row: dict) -> str:
    return str(row.get(INTERNAL_SP_ID_KEY, "UNKNOWN"))


def _validate_required(row: dict, req_type: str) -> list[str]:
    """Return a list of field names that are empty or missing."""
    fields = REQUIRED_FIELDS.get(req_type, ())
    return [f for f in fields if not str(row.get(f, "")).strip()]


def _validate_lengths(username: str = "", email: str = "") -> list[str]:
    """Return error strings for fields that exceed backend max_length constraints.

    Called before UI interaction so we fail fast instead of waiting for a backend
    422 → Playwright timeout (STATUS_MSG_TIMEOUT_MS ≈ 15 s).
    Pass only the fields relevant to the current operation (username is not
    applicable for add/remove_permission).

    / バックエンドのmax_length制限を超えるフィールドのエラー文字列を返す。
    UIインタラクション前に呼び出し、バックエンドの422エラー→Playwrightタイムアウト
    （約15秒）を回避する。操作に関係しないフィールドは省略可能。
    """
    errors: list[str] = []
    if username and len(username) > MAX_USERNAME_LEN:
        errors.append(f"ユーザー名が長すぎます（{len(username)}/{MAX_USERNAME_LEN}文字）")
    if email and len(email) > MAX_EMAIL_LEN:
        errors.append(f"メールアドレスが長すぎます（{len(email)}/{MAX_EMAIL_LEN}文字）")
    return errors


def _save_screenshot(page: Page, row_id: str, screenshot_dir: Path | None = None) -> str:
    """Save a screenshot to screenshot_dir (error recovery dir) or RESULTS_DIR as fallback.
    / スクリーンショットをscreenshot_dir（エラーリカバリ用）またはRESULTS_DIRに保存する。"""
    out_dir = screenshot_dir if screenshot_dir is not None else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"error_{row_id}_{ts}.png"
    page.screenshot(path=str(path))
    return str(path)


def _wait_chaos_loaded(page: Page) -> None:
    """Wait for chaos middleware to finish loading — skip if already done.
    / chaosミドルウェアの初期化完了を待つ。既に完了していればスキップ。"""
    if not page.query_selector("body[data-chaos-loaded='true']"):
        page.wait_for_selector("body[data-chaos-loaded='true']", timeout=CHAOS_LOAD_TIMEOUT_MS)


def _css_escape_attr(value: str) -> str:
    """Escape a value for safe interpolation into a CSS attribute selector (e.g. [title="..."]).
    / CSS属性セレクタ（[title="..."]等）へ安全に埋め込むためのエスケープ。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# UI synchronization & reading helpers
# ---------------------------------------------------------------------------

def _refresh_accounts(page: Page) -> None:
    """Click the refresh button and wait for loadAccounts() to finish updating the table.
    / 更新ボタンをクリックし、一覧データの再読み込み（loadAccounts()完了）を待つ。"""
    body = page.locator("#accounts-body")
    before = body.get_attribute("data-accounts-version")
    page.click("#refresh-btn")
    page.wait_for_function(
        """({ before }) => document.querySelector('#accounts-body').dataset.accountsVersion !== before""",
        arg={"before": before},
        timeout=VERIFY_TIMEOUT_MS,
    )


def _read_account_row(row: Locator) -> dict:
    """Read username/department/permissions/status from a table row.
    / テーブル行からユーザー名・部署・権限・ステータスを読み取る。"""
    username = row.locator("td:nth-child(3) div").get_attribute("title")
    department = row.locator("td:nth-child(5)").inner_text()
    labels = [l.strip() for l in row.locator("td:nth-child(6) span").all_inner_texts()]
    permissions = [PERM_COLUMNS[l] for l in labels if l in PERM_COLUMNS]
    is_active = row.locator("td:nth-child(8) button").get_attribute("aria-checked") == "true"
    return {
        "username": username,
        "department": department,
        "permissions": permissions,
        "status": "active" if is_active else "inactive",
    }


# ---------------------------------------------------------------------------
# Pre-flight API lookup — exact email match
# ---------------------------------------------------------------------------

def _get_account_by_email(email: str, mock_url: str) -> dict | None:
    """Return the account dict for *email* via exact API lookup, or None.
    / メールアドレスでAPIを検索し、アカウント情報を返す。存在しない場合はNone。"""
    try:
        resp = httpx.get(
            f"{mock_url}/accounts/by-email/{email}",
            timeout=API_REQUEST_TIMEOUT,
        )
        return resp.json() if resp.status_code == 200 else None
    except (httpx.RequestError, ValueError):
        return None


def _ui_find_account(page: Page, email: str) -> dict | None:
    """Search the accounts list via the search box and read the matching row.
    / 検索バーでメール完全一致のアカウントを探し、テーブルから読み取る。見つからない場合はNone。"""
    _refresh_accounts(page)
    page.fill("#account-search", email)

    page.wait_for_function(
        """(email) => document.querySelector('#accounts-body').dataset.search === email""",
        arg=email,
        timeout=VERIFY_TIMEOUT_MS,
    )

    row = page.locator(f'#accounts-body tr:has(td:nth-child(4) div[title="{_css_escape_attr(email)}"])')
    if row.count() == 0:
        page.fill("#account-search", "")
        return None

    result = _read_account_row(row)
    result["email"] = email

    page.fill("#account-search", "")
    return result


# ---------------------------------------------------------------------------
# UI verification after RPA operations
# ---------------------------------------------------------------------------

def _verify_account_in_ui(
    page: Page,
    email: str,
    *,
    expected_username: str | None = None,
    expected_department: str | None = None,
    expected_permissions: set[str] | None = None,
) -> None:
    """Search the accounts list by exact email and verify all specified fields.

    Strategy:
    1. Click the refresh button and wait for loadAccounts() to finish
       (this guarantees the table reflects the just-completed operation).
    2. Search by exact email via the search box; wait until the search has
       been applied (data-search === email), then look up the row.
    3. Compare username/department/permissions against the row.
    4. Clear the search bar so the next operation starts clean.

    Raises RuntimeError with a descriptive message on any mismatch.

    / 操作後のUI確認。最新データへの更新を待つ → 検索バーでメール完全一致を
    適用 → 該当行をユーザー名・部署・権限について比較。
    不一致があれば RuntimeError を送出する。
    """
    _refresh_accounts(page)
    page.fill("#account-search", email)

    page.wait_for_function(
        """(email) => document.querySelector('#accounts-body').dataset.search === email""",
        arg=email,
        timeout=VERIFY_TIMEOUT_MS,
    )

    row = page.locator(f'#accounts-body tr:has(td:nth-child(4) div[title="{_css_escape_attr(email)}"])')
    if row.count() == 0:
        page.fill("#account-search", "")
        raise RuntimeError(f"UI確認失敗: {email!r} が一覧に存在しません")

    actual = _read_account_row(row)
    page.fill("#account-search", "")

    mismatches: list[str] = []
    if expected_username is not None and actual["username"] != expected_username:
        mismatches.append(f"ユーザー名: 期待={expected_username!r} / 実際={actual['username']!r}")
    if expected_department is not None and actual["department"] != expected_department:
        mismatches.append(f"部署: 期待={expected_department!r} / 実際={actual['department']!r}")
    if expected_permissions is not None and set(actual["permissions"]) != expected_permissions:
        mismatches.append(
            f"権限: 期待={sorted(expected_permissions)} / 実際={sorted(actual['permissions'])}"
        )

    if mismatches:
        raise RuntimeError("UI確認失敗: " + " / ".join(mismatches))


# ---------------------------------------------------------------------------
# RPA tasks / RPAタスク
# ---------------------------------------------------------------------------

def login_browser(page: Page, mock_url: str) -> None:
    """Navigate to the mock-site and log in."""
    page.goto(mock_url)
    page.fill("#username", os.environ.get("MOCK_SITE_USER", "admin"))
    page.fill("#password", os.environ.get("MOCK_SITE_PASSWORD", "admin"))
    page.get_by_role("button", name="ログイン").click()
    page.wait_for_selector("#main-content", state="visible", timeout=MODAL_VISIBLE_TIMEOUT_MS)


def create_account(
    page: Page,
    row: dict,
    mock_url: str | None = None,
    screenshot_dir: Path | None = None,
) -> dict:
    """Create a user account via the mock-site UI, then verify the result in the UI.

    Post-creation verification:
    - Searches the accounts list by email.
    - Confirms exactly 1 row with that exact email exists.
    - Confirms username, department, and permissions match the CSV.
    If verification fails, returns status=error with a screenshot.

    / アカウント作成後、検索バーでメール完全一致を確認。
    ユーザー名・部署・権限がCSVと一致することを検証する。
    """
    if mock_url is None:
        mock_url = os.environ.get("MOCK_SITE_URL", "http://localhost:8000")

    missing = _validate_required(row, REQ_CREATE)
    if missing:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   str(row.get(COL_EMAIL, "")),
            "message": f"必須項目未入力: {', '.join(missing)}",
        }

    username   = str(row.get(COL_USERNAME,   "")).strip()
    email      = str(row.get(COL_EMAIL,      "")).strip()
    department = str(row.get(COL_DEPARTMENT, "")).strip()
    permissions = _parse_permissions(row)

    # Fail fast on length violations before opening the browser.
    # Without this check, Playwright fills the field bypassing HTML maxlength,
    # the backend returns 422, and we wait the full STATUS_MSG_TIMEOUT_MS (~15 s).
    # / 事前長さチェック。なければバックエンド422→Playwrightタイムアウト（最大15秒）になる。
    length_errors = _validate_lengths(username=username, email=email)
    if length_errors:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   email,
            "message": "入力値が長すぎます: " + " / ".join(length_errors),
        }

    # Idempotency: exact-match lookup before opening the browser form.
    if _get_account_by_email(email, mock_url) is not None:
        return {"status": "skipped", "reason": "already_exists", "id": _sp_id(row), "email": email}

    try:
        _wait_chaos_loaded(page)

        page.evaluate("showView('create')")
        page.wait_for_selector("#new-username", state="visible", timeout=MODAL_VISIBLE_TIMEOUT_MS)

        page.fill("#new-username", username)
        page.fill("#new-email",    email)
        page.evaluate(
            "dept => { window._app.modal.data.department = dept;"
            " delete (window._app.modal.errors || {}).department; }",
            department,
        )

        # Reset permissions array in Alpine.js state, then check each via click events.
        # Directly manipulating Alpine.js state is more reliable than toggling DOM properties.
        # Alpine.js state リセット後に各チェックボックスをクリックして権限を設定する。
        page.evaluate("() => { window._app.modal.data.permissions = []; }")
        for perm in permissions:
            page.check(f"input.permission-checkbox[value='{perm}']")

        # Clear stale status-msg before action so we wait for THIS operation's result.
        # 操作前にstatus-msgをクリアし、今回の操作結果のみを待つ。
        # 操作前にstatus-msgをクリアし、今回の操作結果のみを待つ。
        page.evaluate("() => { document.getElementById('status-msg').textContent = ''; }")
        page.get_by_role("button", name="アカウント作成").click()
        page.locator("#status-msg").filter(has_text="作成完了").wait_for(
            state="attached", timeout=STATUS_MSG_TIMEOUT_MS
        )

        # UI verification: confirm the created account appears in the list with correct data.
        # UI確認：作成したアカウントが一覧に正しく反映されていることを検証する。
        _verify_account_in_ui(
            page, email,
            expected_username=username,
            expected_department=department,
            expected_permissions=permissions,
        )

        return {"status": "success", "id": _sp_id(row), "email": email}

    except Exception as e:
        sp = _sp_id(row)
        screenshot = _save_screenshot(page, sp, screenshot_dir)
        return {"status": "error", "id": sp, "email": email, "message": str(e), "screenshot": screenshot}


def add_permission(
    page: Page,
    row: dict,
    mock_url: str | None = None,
    screenshot_dir: Path | None = None,
) -> dict:
    """Grant additional permissions (union / grant-only), then verify in the UI.

    Post-operation verification:
    - Searches the accounts list by email.
    - Confirms the permission set equals target_perms (current ∪ new).

    / 権限追加後、検索バーでメール完全一致を確認。
    権限がtarget_perms（現状∪追加分）と一致することを検証する。
    """
    if mock_url is None:
        mock_url = os.environ.get("MOCK_SITE_URL", "http://localhost:8000")

    missing = _validate_required(row, REQ_ADD)
    if missing:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   str(row.get(COL_EMAIL, "")),
            "message": f"必須項目未入力: {', '.join(missing)}",
        }

    email = str(row.get(COL_EMAIL, "")).strip()

    length_errors = _validate_lengths(email=email)
    if length_errors:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   email,
            "message": "入力値が長すぎます: " + " / ".join(length_errors),
        }

    new_perms = _parse_permissions(row)
    if not new_perms:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   email,
            "message": "権限列がすべて空です",
        }

    existing = _get_account_by_email(email, mock_url)
    if existing is None:
        return {"status": "error", "id": _sp_id(row), "email": email, "message": "account_not_found"}

    if existing.get("status") == "inactive":
        return {"status": "error", "id": _sp_id(row), "email": email, "message": "アカウントが無効です"}

    current_perms = set(existing.get("permissions") or [])

    if new_perms.issubset(current_perms):
        return {
            "status":       "skipped",
            "reason":       "already_granted",
            "id":           _sp_id(row),
            "email":        email,
            "perms_before": sorted(current_perms),
            "perms_after":  sorted(current_perms),
        }

    target_perms = current_perms | new_perms

    try:
        _wait_chaos_loaded(page)

        page.evaluate("row => window._app.openPermission(row)", existing)
        page.wait_for_selector("#target-username", state="visible", timeout=MODAL_VISIBLE_TIMEOUT_MS)

        page.evaluate("perms => { window._app.modal.data.permissions = perms }", list(target_perms))

        # Clear stale status-msg before action so we wait for THIS operation's result.
        # 操作前にstatus-msgをクリアし、今回の操作結果のみを待つ。
        page.evaluate("() => { document.getElementById('status-msg').textContent = ''; }")
        page.wait_for_selector("#change-btn:not([disabled])", timeout=MODAL_VISIBLE_TIMEOUT_MS)
        page.click("#change-btn")
        page.locator("#status-msg").filter(has_text="権限変更完了").wait_for(
            state="attached", timeout=STATUS_MSG_TIMEOUT_MS
        )

        # UI verification: confirm the permission update is reflected in the list.
        # UI確認：権限追加が一覧に正しく反映されていることを検証する。
        _verify_account_in_ui(page, email, expected_permissions=target_perms)

        return {
            "status":       "success",
            "id":           _sp_id(row),
            "email":        email,
            "perms_before": sorted(current_perms),
            "perms_after":  sorted(target_perms),
        }

    except Exception as e:
        sp = _sp_id(row)
        screenshot = _save_screenshot(page, sp, screenshot_dir)
        return {"status": "error", "id": sp, "email": email, "message": str(e), "screenshot": screenshot}


def remove_permission(
    page: Page,
    row: dict,
    mock_url: str | None = None,
    screenshot_dir: Path | None = None,
) -> dict:
    """Remove specific permissions (subtraction), then verify in the UI.

    Post-operation verification:
    - Searches the accounts list by email.
    - Confirms the permission set equals target_perms (current − to_remove).
    - Empty result permissions ([]) are valid and verified correctly.

    / 権限削除後、検索バーでメール完全一致を確認。
    権限がtarget_perms（現状−削除分）と一致することを検証する。
    空配列も正常として検証する。
    """
    if mock_url is None:
        mock_url = os.environ.get("MOCK_SITE_URL", "http://localhost:8000")

    missing = _validate_required(row, REQ_REMOVE)
    if missing:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   str(row.get(COL_EMAIL, "")),
            "message": f"必須項目未入力: {', '.join(missing)}",
        }

    email = str(row.get(COL_EMAIL, "")).strip()

    length_errors = _validate_lengths(email=email)
    if length_errors:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   email,
            "message": "入力値が長すぎます: " + " / ".join(length_errors),
        }

    to_remove = _parse_permissions(row)
    if not to_remove:
        return {
            "status":  "error",
            "id":      _sp_id(row),
            "email":   email,
            "message": "権限列がすべて空です",
        }

    existing = _get_account_by_email(email, mock_url)
    if existing is None:
        return {"status": "error", "id": _sp_id(row), "email": email, "message": "account_not_found"}

    # Inactive accounts allowed — clearing permissions on disabled accounts is safe.
    # 非アクティブアカウントでも権限削除は許可する。
    current_perms = set(existing.get("permissions") or [])

    if to_remove.isdisjoint(current_perms):
        return {
            "status":       "skipped",
            "reason":       "already_removed",
            "id":           _sp_id(row),
            "email":        email,
            "perms_before": sorted(current_perms),
            "perms_after":  sorted(current_perms),
        }

    target_perms = current_perms - to_remove

    try:
        _wait_chaos_loaded(page)

        page.evaluate("row => window._app.openPermission(row)", existing)
        page.wait_for_selector("#target-username", state="visible", timeout=MODAL_VISIBLE_TIMEOUT_MS)

        page.evaluate("perms => { window._app.modal.data.permissions = perms }", list(target_perms))

        # Clear stale status-msg before action so we wait for THIS operation's result.
        # 操作前にstatus-msgをクリアし、今回の操作結果のみを待つ。
        page.evaluate("() => { document.getElementById('status-msg').textContent = ''; }")
        page.wait_for_selector("#change-btn:not([disabled])", timeout=MODAL_VISIBLE_TIMEOUT_MS)
        page.click("#change-btn")
        page.locator("#status-msg").filter(has_text="権限変更完了").wait_for(
            state="attached", timeout=STATUS_MSG_TIMEOUT_MS
        )

        # UI verification: confirm the permission removal is reflected in the list.
        # UI確認：権限削除が一覧に正しく反映されていることを検証する。
        _verify_account_in_ui(page, email, expected_permissions=target_perms)

        return {
            "status":       "success",
            "id":           _sp_id(row),
            "email":        email,
            "perms_before": sorted(current_perms),
            "perms_after":  sorted(target_perms),
        }

    except Exception as e:
        sp = _sp_id(row)
        screenshot = _save_screenshot(page, sp, screenshot_dir)
        return {"status": "error", "id": sp, "email": email, "message": str(e), "screenshot": screenshot}
