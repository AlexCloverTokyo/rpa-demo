from pathlib import Path

from rpa.mock_notifier import notify_error


def test_notify_error_writes_to_log(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_TO", "ops@example.com")

    from rpa.columns import COL_EMAIL
    notify_error(
        csv_path=Path("rpa/error/SP-00101/SP-00101_create_account.csv"),
        row={COL_EMAIL: "new_user_001@example.com", "_sp_id": "SP-00101"},
        error="TimeoutError: element not found",
        sp_id="SP-00101",
        log_dir=tmp_path,
    )

    log_files = list(tmp_path.glob("*_SP-00101_error.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "SP-00101" in content
    assert "new_user_001@example.com" in content
    assert "ops@example.com" in content
    assert "TimeoutError" in content


def test_notify_error_extracts_sharepoint_id():
    from rpa.mock_notifier import _extract_sp_id
    csv_path = Path("rpa/error/SP-00999/SP-00999_change_permission.csv")
    assert _extract_sp_id(csv_path) == "SP-00999"
