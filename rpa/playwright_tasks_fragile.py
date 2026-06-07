"""
Fragile selector example — hard-coded ID breaks immediately on system updates.
脆弱なセレクタの例 — IDに依存するため、システム更新で即座に壊れる。
Demo for the article section "セレクタが変わる" / "Selectors that change".
記事「要素のセレクタが変わる」のデモ用。
"""
from playwright.sync_api import Page


def create_account_fragile(page: Page, username: str, email: str, department: str) -> None:
    # Wait for chaos middleware to finish loading (if selector_chaos is ON, the ID has already changed).
    # Chaos 初期化完了を待つ（selector_chaos が ON なら ID はここで既に変わっている）
    page.wait_for_selector("body[data-chaos-loaded='true']", timeout=10000)
    # Fragile: hard-coded fixed ID. Fails with TimeoutError when selector_chaos is enabled.
    # 脆弱: 正常時の固定 ID を直書き。selector_chaos 有効時は #create-btn が消えるため TimeoutError になる
    page.click("#create-btn", timeout=5000)
