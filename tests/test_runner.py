# tests/test_runner.py
# Unit and integration tests for runner.py CSV lifecycle.
#
# ── Unit tests (no Docker needed) ────────────────────────────────────────────
#   TestIsTargetFile      B-12: filename pattern matching
#   TestExtractSpId       B-13: SP-ID extraction from filename
#   TestProcessRowType    B-14: unknown 申請種別 → error dict
#
# ── Structural CSV tests (no Docker, fail at pd.read_csv) ────────────────────
#   TestCsvStructural     B-3:  empty file → error/
#                         B-4:  invalid encoding → error/
#
# ── Integration tests (require: docker-compose up -d) ────────────────────────
#   TestCsvLifecycle      B-1:  header-only → processed/
#                         B-2:  missing column → error/
#                         B-6:  multi-row mixed (success + error) → error/
#                         B-7:  multi-row all success → processed/
#                         B-8:  create then add perm same user → processed/
#                         B-9:  all rows skipped → processed/ (skipped ≠ error)
#                         B-10: create + remove perm same file → processed/

import csv
import shutil
from pathlib import Path

import httpx

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
from rpa.runner import (
    ERROR_DIR,
    INBOX_DIR,
    PROCESSED_DIR,
    _process_row,
    extract_sp_id,
    is_target_file,
    process_csv,
)

MOCK_URL     = "http://localhost:8000"
CSV_HEADERS  = [
    COL_REQUEST_TYPE, COL_USERNAME, COL_EMAIL, COL_DEPARTMENT,
    COL_PERM_REPORT, COL_PERM_EXPORT, COL_PERM_APPROVER,
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_csv(
    sp_id_num: int,
    rows: list[dict],
    *,
    headers: list[str] | None = None,
    encoding: str = "utf-8",
    raw_bytes: bytes | None = None,
) -> Path:
    """Create a CSV in inbox/ and return its path.

    Pass *raw_bytes* to write arbitrary bytes (encoding/CSV tests).
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"SP-{sp_id_num}_request.csv"
    path = INBOX_DIR / filename
    if raw_bytes is not None:
        path.write_bytes(raw_bytes)
        return path
    if headers is None:
        headers = CSV_HEADERS
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _ensure_dirs() -> None:
    """Create output directories so process_csv doesn't fail on mkdir."""
    for d in [INBOX_DIR, PROCESSED_DIR, ERROR_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _cleanup_sp(sp_id_num: int) -> None:
    """Remove processed/error artefacts for a given SP-ID number."""
    sp_id = f"SP-{sp_id_num}"
    # processed/Processing_end_SP-{N}_request.csv
    for f in PROCESSED_DIR.glob(f"*SP-{sp_id_num}_request.csv"):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
    # error/recovery/SP-{N}/
    err_dir = ERROR_DIR / "recovery" / sp_id
    if err_dir.exists():
        shutil.rmtree(err_dir, ignore_errors=True)


def _delete_account(username: str) -> None:
    try:
        httpx.delete(f"{MOCK_URL}/accounts/{username}", timeout=5.0)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# B-12  is_target_file — unit tests, no Docker
# ─────────────────────────────────────────────────────────────────────────────

class TestIsTargetFile:
    def test_valid(self):
        assert is_target_file("SP-101_request.csv")   is True
        assert is_target_file("SP-1_request.csv")     is True
        assert is_target_file("SP-99999_request.csv") is True

    def test_processing_prefix_rejected(self):
        assert is_target_file("Processing_start_SP-101_request.csv") is False
        assert is_target_file("Processing_end_SP-101_request.csv")   is False

    def test_wrong_extension(self):
        assert is_target_file("SP-101.csv")     is False
        assert is_target_file("SP-101_request") is False

    def test_non_numeric_id(self):
        assert is_target_file("SP-ABC_request.csv") is False

    def test_empty_and_other(self):
        assert is_target_file("")                     is False
        assert is_target_file("request.csv")          is False
        assert is_target_file("sp-101_request.csv")   is False   # lowercase


# ─────────────────────────────────────────────────────────────────────────────
# B-13  extract_sp_id — unit tests, no Docker
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractSpId:
    def test_normal(self):
        assert extract_sp_id(Path("SP-101_request.csv")) == "SP-101"

    def test_single_digit(self):
        assert extract_sp_id(Path("SP-1_request.csv")) == "SP-1"

    def test_leading_zeros_stripped(self):
        assert extract_sp_id(Path("SP-00101_request.csv")) == "SP-101"

    def test_large_number(self):
        assert extract_sp_id(Path("SP-99999_request.csv")) == "SP-99999"


# ─────────────────────────────────────────────────────────────────────────────
# B-14  _process_row unknown type — unit tests, no Docker
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessRowUnknownType:
    def _row(self, req_type: str) -> dict:
        return {INTERNAL_SP_ID_KEY: "SP-TEST", COL_REQUEST_TYPE: req_type}

    def test_unknown_returns_error(self):
        result = _process_row(None, self._row("発注処理"))
        assert result["status"] == "error"
        assert result["id"] == "SP-TEST"
        assert "発注処理" in result["message"]

    def test_old_req_change_returns_error(self):
        """'権限変更' is no longer a valid type — must be 権限追加 or 権限削除."""
        result = _process_row(None, self._row("権限変更"))
        assert result["status"] == "error"
        assert "権限変更" in result["message"]

    def test_empty_type_returns_error(self):
        result = _process_row(None, self._row(""))
        assert result["status"] == "error"

    def test_whitespace_type_returns_error(self):
        result = _process_row(None, self._row("  "))
        assert result["status"] == "error"


# ─────────────────────────────────────────────────────────────────────────────
# B-3 / B-4  Structural CSV errors — fail at pd.read_csv, no Docker needed
# ─────────────────────────────────────────────────────────────────────────────

class TestCsvStructural:
    """process_csv raises before opening a browser → Docker not required."""

    def test_empty_file_goes_to_error(self):
        """B-3: zero-byte file → EmptyDataError → error/."""
        _ensure_dirs()
        path = _write_csv(950, [], raw_bytes=b"")
        try:
            process_csv(path)
            err_dir = ERROR_DIR / "recovery" / "SP-950"
            assert err_dir.exists() and any(err_dir.iterdir()), \
                "zero-byte file should be moved to error/"
        finally:
            _cleanup_sp(950)

    def test_invalid_encoding_goes_to_error(self):
        """B-4: UTF-16 BOM makes the file unreadable as UTF-8 → error/."""
        _ensure_dirs()
        # UTF-16 LE BOM followed by UTF-16-encoded content — not valid UTF-8
        content = "申請種別,ユーザー名\nアカウント作成,テスト\n".encode("utf-16")
        path = _write_csv(952, [], raw_bytes=content)
        try:
            process_csv(path)
            err_dir = ERROR_DIR / "recovery" / "SP-952"
            assert err_dir.exists() and any(err_dir.iterdir()), \
                "bad-encoding file should be moved to error/"
        finally:
            _cleanup_sp(952)


# ─────────────────────────────────────────────────────────────────────────────
# B-1 / B-2 / B-6 / B-7 / B-8  Full lifecycle — require Docker
# ─────────────────────────────────────────────────────────────────────────────

class TestCsvLifecycle:
    """Integration tests: browser is opened, mock-site must be running."""

    def test_header_only_goes_to_processed(self):
        """B-1: CSV with headers but zero data rows → processed/ (no errors)."""
        _ensure_dirs()
        path = _write_csv(960, [])
        try:
            process_csv(path)
            dest = PROCESSED_DIR / "Processing_end_SP-960_request.csv"
            assert dest.exists(), "header-only CSV should go to processed/"
        finally:
            _cleanup_sp(960)

    def test_missing_column_goes_to_error(self):
        """B-2: CSV without '申請種別' column → unknown type → error/."""
        _ensure_dirs()
        path = _write_csv(
            961,
            [{"ユーザー名": "col_missing_user", "メールアドレス": "col-missing@test.com"}],
            headers=["ユーザー名", "メールアドレス"],
        )
        try:
            process_csv(path)
            err_dir = ERROR_DIR / "recovery" / "SP-961"
            assert err_dir.exists() and any(err_dir.iterdir()), \
                "missing-column CSV should go to error/"
        finally:
            _cleanup_sp(961)

    def test_multi_row_mixed_goes_to_error(self):
        """B-6: row1 success (create) + row2 error (user not found) → error/."""
        _ensure_dirs()
        rows = [
            {
                COL_REQUEST_TYPE: REQ_CREATE,
                COL_USERNAME:     "runner_b6_user",
                COL_EMAIL:        "runner-b6@test.com",
                COL_DEPARTMENT:   "開発部",
                COL_PERM_REPORT:  "○",
            },
            {
                COL_REQUEST_TYPE:  REQ_ADD,
                COL_USERNAME:      "",
                COL_EMAIL:         "runner-b6-nonexist@test.com",
                COL_DEPARTMENT:    "",
                COL_PERM_REPORT:   "○",
            },
        ]
        path = _write_csv(970, rows)
        try:
            process_csv(path)
            err_dir = ERROR_DIR / "recovery" / "SP-970"
            assert err_dir.exists() and any(err_dir.iterdir()), \
                "mixed-result CSV (has error row) should go to error/"
        finally:
            _cleanup_sp(970)
            _delete_account("runner_b6_user")

    def test_multi_row_all_success_goes_to_processed(self):
        """B-7: create user then change permission — both succeed → processed/."""
        _ensure_dirs()
        rows = [
            {
                COL_REQUEST_TYPE: REQ_CREATE,
                COL_USERNAME:     "runner_b7_user",
                COL_EMAIL:        "runner-b7@test.com",
                COL_DEPARTMENT:   "営業部",
                COL_PERM_REPORT:  "",
            },
            {
                COL_REQUEST_TYPE:  REQ_ADD,
                COL_EMAIL:         "runner-b7@test.com",
                COL_PERM_REPORT:   "○",
            },
        ]
        path = _write_csv(971, rows)
        try:
            process_csv(path)
            dest = PROCESSED_DIR / "Processing_end_SP-971_request.csv"
            assert dest.exists(), "all-success CSV should go to processed/"
        finally:
            _cleanup_sp(971)
            _delete_account("runner_b7_user")

    def test_create_then_change_same_file(self):
        """B-8: create + change in same CSV → processed/, permission applied."""
        _ensure_dirs()
        rows = [
            {
                COL_REQUEST_TYPE: REQ_CREATE,
                COL_USERNAME:     "runner_b8_user",
                COL_EMAIL:        "runner-b8@test.com",
                COL_DEPARTMENT:   "開発部",
            },
            {
                COL_REQUEST_TYPE:    REQ_ADD,
                COL_EMAIL:           "runner-b8@test.com",
                COL_PERM_APPROVER:   "○",
            },
        ]
        path = _write_csv(972, rows)
        try:
            process_csv(path)
            dest = PROCESSED_DIR / "Processing_end_SP-972_request.csv"
            assert dest.exists(), "create+change CSV should go to processed/"
            # Verify permission was applied
            resp = httpx.get(f"{MOCK_URL}/accounts/runner_b8_user", timeout=5.0)
            assert resp.status_code == 200
            assert "approver" in resp.json()["permissions"]
        finally:
            _cleanup_sp(972)
            _delete_account("runner_b8_user")

    def test_all_rows_skipped_goes_to_processed(self):
        """B-9: Every row returns skipped (not error) → processed/.

        Key semantics: skipped ≠ error. A real-world scenario is an operator
        re-dropping a file that was already fully processed — idempotent checks
        skip each row and the file should land in processed/, not error/.
        """
        _ensure_dirs()
        # Pre-create the user via API so the CSV create will be skipped.
        _delete_account("runner_b9_user")
        httpx.post(f"{MOCK_URL}/accounts", json={
            "username":   "runner_b9_user",
            "email":      "runner-b9@test.com",
            "department": "開発部",
            "permissions": ["report"],
        }, timeout=5.0)
        # CSV row 1: create → skipped (user already exists)
        # CSV row 2: add report → skipped (already_granted)
        rows = [
            {
                COL_REQUEST_TYPE: REQ_CREATE,
                COL_USERNAME:     "runner_b9_user",
                COL_EMAIL:        "runner-b9@test.com",
                COL_DEPARTMENT:   "開発部",
                COL_PERM_REPORT:  "○",
            },
            {
                COL_REQUEST_TYPE: REQ_ADD,
                COL_EMAIL:        "runner-b9@test.com",
                COL_PERM_REPORT:  "○",
            },
        ]
        path = _write_csv(973, rows)
        try:
            process_csv(path)
            dest = PROCESSED_DIR / "Processing_end_SP-973_request.csv"
            assert dest.exists(), \
                "all-skipped CSV should go to processed/ — skipped is NOT an error"
            err_dir = ERROR_DIR / "recovery" / "SP-973"
            assert not err_dir.exists(), \
                "all-skipped CSV must NOT go to error/"
        finally:
            _cleanup_sp(973)
            _delete_account("runner_b9_user")

    def test_create_then_remove_same_file(self):
        """B-10: create new user + remove a permission in same CSV → processed/.

        New-user scenario: user is created WITH report+export in one row,
        then report is removed in the next row → processed/, only export remains.
        """
        _ensure_dirs()
        rows = [
            {
                COL_REQUEST_TYPE: REQ_CREATE,
                COL_USERNAME:     "runner_b10_user",
                COL_EMAIL:        "runner-b10@test.com",
                COL_DEPARTMENT:   "開発部",
                COL_PERM_REPORT:  "○",
                COL_PERM_EXPORT:  "○",
            },
            {
                COL_REQUEST_TYPE: REQ_REMOVE,
                COL_EMAIL:        "runner-b10@test.com",
                COL_PERM_REPORT:  "○",
            },
        ]
        path = _write_csv(974, rows)
        try:
            process_csv(path)
            dest = PROCESSED_DIR / "Processing_end_SP-974_request.csv"
            assert dest.exists(), "create+remove CSV should go to processed/"
            # Verify the API reflects the net result: export only, no report
            resp = httpx.get(f"{MOCK_URL}/accounts/runner_b10_user", timeout=5.0)
            assert resp.status_code == 200
            perms = set(resp.json()["permissions"])
            assert "export" in     perms, f"export should remain, got {perms}"
            assert "report" not in perms, f"report should be removed, got {perms}"
        finally:
            _cleanup_sp(974)
            _delete_account("runner_b10_user")
