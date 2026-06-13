# rpa-demo/tests/test_frontend_contract.py
# Docker-free contract tests: assert specific markup/JS strings exist in
# mock_site/frontend/index.html. No fixtures, no server, no browser.
from pathlib import Path

INDEX_HTML = Path(__file__).parent.parent / "mock_site" / "frontend" / "index.html"


def _read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_status_toggle_has_aria_switch_attributes():
    html = _read_index()
    assert (
        '<button @click="toggleStatus(row)" role="switch" '
        ':aria-checked="row.status === \'active\' ? \'true\' : \'false\'"'
        in html
    )


def test_accounts_version_counter_initialized():
    html = _read_index()
    assert "accountsVersion: 0," in html


def test_load_accounts_increments_version():
    html = _read_index()
    assert "this.accountsVersion++;" in html


def test_accounts_body_has_sync_attributes():
    html = _read_index()
    assert (
        '<tbody id="accounts-body" :data-search="search" '
        ':data-accounts-version="accountsVersion">'
        in html
    )


def test_edit_button_uses_row_id_for_dom_id():
    html = _read_index()
    assert ":id=\"'perm-btn-' + row.id\"" in html


def test_open_edit_stores_original_email():
    html = _read_index()
    assert "originalEmail: row.email," in html


def test_submit_edit_uses_original_email():
    html = _read_index()
    assert "const origEmail = d.originalEmail || d.email;" in html
    assert "`${API}/accounts/${origEmail}`" in html


def test_toggle_status_uses_row_email():
    html = _read_index()
    assert "`${API}/accounts/${row.email}/status`" in html


def test_confirm_delete_uses_email():
    html = _read_index()
    assert "`${API}/accounts/${a.email}`" in html
    assert "`${API}/accounts/${row.email}`" in html
