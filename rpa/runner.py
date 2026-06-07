import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

import httpx
import pandas as pd
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from .columns import (
    COL_EMAIL,
    COL_REQUEST_TYPE,
    INTERNAL_SP_ID_KEY,
    REQ_ADD,
    REQ_CREATE,
    REQ_REMOVE,
)
from .mock_notifier import notify_error
from .playwright_tasks import add_permission, create_account, login_browser, remove_permission

load_dotenv()

# ---------------------------------------------------------------------------
# Directory layout / ディレクトリ構成
# ---------------------------------------------------------------------------
INBOX_DIR     = Path("rpa/inbox")      # Drop zone for incoming CSVs / CSVの投入先
PROCESSED_DIR = Path("rpa/processed")  # Successfully processed CSVs / 正常処理済みCSV
ERROR_DIR     = Path("rpa/error")      # Failed CSVs + notification logs / 失敗CSV＋通知ログ
RESULTS_DIR   = Path("rpa/results")    # Per-row JSON results + error screenshots / 行別結果JSON＋エラースクリーンショット

# ---------------------------------------------------------------------------
# In-progress file name prefixes / 処理中ファイルのプレフィックス
#
# Renaming the file atomically "claims" it, preventing double-processing when
# multiple runner instances are running simultaneously.
# ファイルをリネームすることでアトミックにクレームし、
# 複数インスタンスの同時実行による二重処理を防ぐ。
# ---------------------------------------------------------------------------
PROCESSING_START_PREFIX = "Processing_start_"
PROCESSING_END_PREFIX   = "Processing_end_"

# ---------------------------------------------------------------------------
# Runtime configuration from environment / 環境変数による実行時設定
# ---------------------------------------------------------------------------
MOCK_URL = os.environ.get("MOCK_SITE_URL", "http://localhost:8000")
# RPA_HEADLESS=false to show browser during development / 開発時はfalseでブラウザ表示
HEADLESS = os.environ.get("RPA_HEADLESS", "true").lower() != "false"

# ---------------------------------------------------------------------------
# Tunable parameters / 調整可能パラメータ
# All can be overridden via environment variables / すべて環境変数で上書き可能
# ---------------------------------------------------------------------------

# watchdog polling interval (seconds) / watchdogポーリング間隔(秒)
# PollingObserver is used instead of inotify-based Observer for Windows reliability.
# Windowsの信頼性のためinotify非依存のPollingObserverを使用。
POLL_INTERVAL_SEC = int(os.environ.get("RPA_POLL_INTERVAL", "2"))

# Wait after file-created event to ensure write is complete / ファイル書き込み完了を待つ時間(秒)
FILE_SETTLE_SEC = float(os.environ.get("RPA_FILE_SETTLE", "0.5"))

# Interval for observer.join() loop — must be short enough to catch Ctrl+C on Windows
# Ctrl+CをWindowsで確実に捕捉するためのjoinループ間隔(秒)
SHUTDOWN_CHECK_INTERVAL = 1

# /health endpoint timeout (seconds) / ヘルスチェックタイムアウト(秒)
HEALTH_CHECK_TIMEOUT = 5

# Log/result file retention period (days) / ログ・結果ファイルの保持日数
RETENTION_DAYS = int(os.environ.get("RPA_RETENTION_DAYS", "7"))

# ---------------------------------------------------------------------------
# Inbox filename rules / 受信ファイル名ルール
#
# Power Automate outputs exactly this pattern: SP-{SharePoint item ID}_request.csv
# SP IDはゼロ埋めなし（例: SP-1, SP-101）
# Any other filename in inbox/ is silently ignored.
# inbox/内のその他のファイル名は無視される。
# ---------------------------------------------------------------------------
INBOX_FILENAME_PATTERN = re.compile(r"^SP-(\d+)_request\.csv$")

# Thread-safety guard against duplicate processing from rapid watchdog events
# watchdogの連続イベントによる重複処理を防ぐスレッドセーフガード
_processing_lock = Lock()
_processing_set: set[str] = set()


# ---------------------------------------------------------------------------
# File helpers / ファイルユーティリティ
# ---------------------------------------------------------------------------

def is_target_file(filename: str) -> bool:
    """Return True only for files matching the Power Automate output pattern.
    / Power Automateの出力パターンに一致するファイルのみTrue。"""
    return bool(INBOX_FILENAME_PATTERN.match(filename))


def extract_sp_id(filepath: Path) -> str:
    """Extract SP-{n} (no leading zeros) from a filename like SP-101_request.csv.
    / SP-101_request.csv などからゼロ埋めなしのSP-{n}を取り出す。"""
    m = INBOX_FILENAME_PATTERN.match(filepath.name)
    return f"SP-{int(m.group(1))}" if m else "UNKNOWN"


# ---------------------------------------------------------------------------
# Health check / ヘルスチェック
# ---------------------------------------------------------------------------

def check_mock_site() -> bool:
    """Return True if the mock-site is reachable. / mock-siteが到達可能かチェック。"""
    try:
        resp = httpx.get(f"{MOCK_URL}/health", timeout=HEALTH_CHECK_TIMEOUT)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Result logging / 結果ログ
# ---------------------------------------------------------------------------

def write_result_log(result: dict) -> None:
    """Write a per-row processing result to results/ as timestamped JSON.
    / 行ごとの処理結果をタイムスタンプ付きJSONでresults/に書き出す。"""
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    row_id = result.get("id", "UNKNOWN")
    log_path = RESULTS_DIR / f"{ts}_{row_id}.json"
    log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    status = result.get("status", "?")
    print(f"  [{status.upper()}] id={row_id} email={result.get('email', '?')}")


# ---------------------------------------------------------------------------
# Error routing / エラー振り分け
# ---------------------------------------------------------------------------

def _move_to_error(
    in_progress: Path,
    original_path: Path,
    last_row: dict,
    sp_id: str,
    ts: str,
    reason: str,
) -> None:
    """Move an in-progress CSV to error/recovery/{sp_id}/{ts}/ and write a notification log.
    / 処理中CSVをerror/recovery/{sp_id}/{ts}/に移動し通知ログを書き込む。"""
    error_subdir = ERROR_DIR / "recovery" / sp_id / ts
    error_subdir.mkdir(parents=True, exist_ok=True)
    error_dest = error_subdir / original_path.name
    if error_dest.exists():
        error_dest.unlink()
    in_progress.rename(error_dest)
    notify_error(error_dest, last_row, reason, sp_id=sp_id)
    print(f"[ERROR] {original_path.name} → error/recovery/{sp_id}/{ts}/")


# ---------------------------------------------------------------------------
# CSV processing / CSV処理
# ---------------------------------------------------------------------------

def _process_row(page, row: dict, screenshot_dir=None) -> dict:
    """Dispatch a single CSV row to the appropriate RPA task.
    / CSV1行を対応するRPAタスクにディスパッチする。"""
    req_type = row.get(COL_REQUEST_TYPE, "")
    if req_type == REQ_CREATE:
        return create_account(page, row, mock_url=MOCK_URL, screenshot_dir=screenshot_dir)
    elif req_type == REQ_ADD:
        return add_permission(page, row, mock_url=MOCK_URL, screenshot_dir=screenshot_dir)
    elif req_type == REQ_REMOVE:
        return remove_permission(page, row, mock_url=MOCK_URL, screenshot_dir=screenshot_dir)
    # Unknown request type — treated as an error row
    # 不明な申請種別はエラー扱い
    return {
        "status":  "error",
        "id":      row.get(INTERNAL_SP_ID_KEY, "?"),
        "email":   row.get(COL_EMAIL),
        "message": f"unknown 申請種別: {req_type}",
    }


def process_csv(original_path: Path) -> None:
    """Process a single inbox CSV file end-to-end.
    / 受信CSVを1ファイルまるごと処理する。

    File lifecycle / ファイルライフサイクル:
      inbox/SP-N_request.csv
        → inbox/Processing_start_SP-N_request.csv   (claimed / クレーム中)
        → processed/Processing_end_SP-N_request.csv (all rows ok / 全行正常)
        → error/recovery/{SP-N}/{ts}/SP-N_request.csv (any row failed / 1行以上失敗)
    """
    # Atomic claim: rename before any processing so other runner instances skip this file.
    # アトミッククレーム：処理前にリネームして他のインスタンスが同じファイルを処理しないようにする。
    in_progress = original_path.parent / f"{PROCESSING_START_PREFIX}{original_path.name}"
    original_path.rename(in_progress)

    ts     = datetime.now().strftime("%Y%m%dT%H%M%S")
    sp_id  = extract_sp_id(original_path)
    # Pre-compute the error recovery subdir so screenshots land next to the CSV.
    # スクリーンショットがCSVと同じディレクトリに入るよう、事前にパスを計算する。
    error_subdir = ERROR_DIR / "recovery" / sp_id / ts

    last_row: dict = {}
    # Tracks whether any row returned status=error (business-logic errors do not raise exceptions).
    # ビジネスロジックエラーは例外を発生させないため、フラグで追跡する。
    has_error = False

    try:
        df = pd.read_csv(in_progress)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            try:
                page = browser.new_page()
                login_browser(page, MOCK_URL)
                for _, row in df.iterrows():
                    last_row = row.to_dict()
                    # SP ID injected from filename; CSV rows themselves carry no ID column.
                    # SP IDはファイル名から注入。CSVのID列は存在しない。
                    last_row[INTERNAL_SP_ID_KEY] = sp_id
                    result = _process_row(page, last_row, screenshot_dir=error_subdir)
                    write_result_log(result)
                    if result.get("status") == "error":
                        has_error = True
            finally:
                browser.close()

        if has_error:
            # At least one row had a business-logic error (e.g. missing required field,
            # user not found). Move to error/ so operators can find it easily.
            # 1行以上でビジネスロジックエラー（必須項目不足・ユーザー不存在など）。
            # 運用担当者が確認しやすいようerror/に移動する。
            _move_to_error(
                in_progress, original_path, last_row, sp_id, ts,
                "business-logic error — see result JSON / 結果JSONを参照"
            )
        else:
            dest = PROCESSED_DIR / f"{PROCESSING_END_PREFIX}{original_path.name}"
            if dest.exists():
                dest.unlink()
            in_progress.rename(dest)
            print(f"[DONE] {original_path.name} → processed/")

    except Exception as e:
        # System-level failure (browser crash, network error, file I/O error, etc.)
        # システムレベル障害（ブラウザクラッシュ、ネットワークエラー、ファイルI/Oエラー等）
        _move_to_error(in_progress, original_path, last_row, sp_id, ts, str(e))


# ---------------------------------------------------------------------------
# Watchdog event handler / watchdogイベントハンドラ
# ---------------------------------------------------------------------------

class CSVHandler(FileSystemEventHandler):
    def on_created(self, event):
        filename = Path(event.src_path).name

        # Ignore files that do not match the expected Power Automate naming pattern.
        # Power Automateの命名パターンに一致しないファイルは無視する。
        if not is_target_file(filename):
            return

        # Deduplicate: PollingObserver may fire on_created multiple times for the same file.
        # 重複排除：PollingObserverは同一ファイルでon_createdを複数回発火することがある。
        abs_path = str(Path(event.src_path).resolve())
        with _processing_lock:
            if abs_path in _processing_set:
                return
            _processing_set.add(abs_path)

        try:
            path = Path(event.src_path)
            # Brief pause to let the writer finish flushing the file before reading.
            # ファイルの書き込みが完了するまで少し待つ。
            time.sleep(FILE_SETTLE_SEC)
            print(f"\n[DETECTED] {path.name}")
            process_csv(path)
        except FileNotFoundError:
            # File was claimed by another runner instance between detection and rename.
            # 検知からリネームの間に別インスタンスがクレーム済み。
            print(f"[SKIP] {Path(event.src_path).name} already claimed by another process")
        finally:
            with _processing_lock:
                _processing_set.discard(abs_path)


# ---------------------------------------------------------------------------
# Startup routines / 起動時処理
# ---------------------------------------------------------------------------

def cleanup_old_files() -> None:
    """Delete results/processed/error files older than RETENTION_DAYS at startup.
    / 起動時にRETENTION_DAYS日より古いresults/processed/errorファイルを削除する。"""
    cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
    cleaned = 0

    for directory in [RESULTS_DIR, PROCESSED_DIR]:
        if not directory.exists():
            continue
        for f in directory.iterdir():
            if f.is_file() and f.name != ".gitkeep" and f.stat().st_mtime < cutoff:
                f.unlink()
                cleaned += 1

    # Remove .log files at the error/ root
    # error/直下の.logファイルを削除
    if ERROR_DIR.exists():
        for f in ERROR_DIR.iterdir():
            if f.is_file() and f.suffix == ".log" and f.stat().st_mtime < cutoff:
                f.unlink()
                cleaned += 1

    # Remove recovery subdirs older than retention period; prune empty SP-ID dirs after.
    # 保持期間を超えたrecoveryサブディレクトリを削除し、空のSP-IDディレクトリを整理する。
    error_recovery = ERROR_DIR / "recovery"
    if error_recovery.exists():
        for sp_dir in error_recovery.iterdir():
            if not sp_dir.is_dir():
                continue
            for ts_dir in sp_dir.iterdir():
                if ts_dir.is_dir() and ts_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(ts_dir)
                    cleaned += 1
            if not any(sp_dir.iterdir()):
                sp_dir.rmdir()

    if cleaned:
        print(f"[CLEANUP] Removed {cleaned} file(s) older than {RETENTION_DAYS} days.")


def process_existing_files() -> None:
    """Process any SP-{ID}_request.csv files already sitting in inbox/ at startup.
    / 起動時にinbox/に残っているCSVを処理する（runner停止中に投入されたファイル対応）。"""
    existing = sorted(f for f in INBOX_DIR.glob("*.csv") if is_target_file(f.name))
    if not existing:
        return
    print(f"[STARTUP] Found {len(existing)} existing file(s) in inbox — processing now...")
    for path in existing:
        print(f"\n[STARTUP] {path.name}")
        process_csv(path)


# ---------------------------------------------------------------------------
# Entry point / エントリーポイント
# ---------------------------------------------------------------------------

def main():
    for d in [INBOX_DIR, PROCESSED_DIR, ERROR_DIR, RESULTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    if not check_mock_site():
        print(f"ERROR: mock-site ({MOCK_URL}) is not running. Start with: docker compose up -d")
        sys.exit(1)

    print(f"RPA Runner started. Watching {INBOX_DIR.resolve()} ...")
    print(f"  headless={HEADLESS}  poll={POLL_INTERVAL_SEC}s  settle={FILE_SETTLE_SEC}s  retention={RETENTION_DAYS}d")
    print("Drop SP-{{ID}}_request.csv into rpa/inbox/ to process.")

    cleanup_old_files()
    process_existing_files()

    handler = CSVHandler()
    # PollingObserver polls the filesystem every POLL_INTERVAL_SEC seconds.
    # inotify/FSEventsを使わないため、Windowsのネットワークドライブやコピー操作でも確実に動作する。
    observer = PollingObserver(timeout=POLL_INTERVAL_SEC)
    observer.schedule(handler, path=str(INBOX_DIR), recursive=False)
    observer.start()
    try:
        # Loop with short timeout so KeyboardInterrupt (Ctrl+C) is caught on Windows.
        # タイムアウト付きループにすることでWindowsでもCtrl+Cが確実に捕捉される。
        while observer.is_alive():
            observer.join(timeout=SHUTDOWN_CHECK_INTERVAL)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("\nStopped.")


if __name__ == "__main__":
    main()
