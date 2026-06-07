import os
import re
from datetime import datetime
from pathlib import Path

from .columns import COL_EMAIL

# ---------------------------------------------------------------------------
# Default output directory / デフォルト出力ディレクトリ
# Notification .log files are written here so Power Automate can detect them.
# Power Automateが検知できるよう通知.logファイルをここに書き出す。
# ---------------------------------------------------------------------------
ERROR_DIR = Path("rpa/error")

# Log file suffix / ログファイルの拡張子
LOG_SUFFIX = "_error.log"

# Filename pattern used to extract SP ID when not explicitly provided.
# SP IDが明示されない場合にファイル名から抽出するパターン。
_SP_ID_PATTERN = re.compile(r"(SP-\d+)")


def _extract_sp_id(csv_path: Path) -> str:
    """Extract SP-{n} from a filename. Falls back to 'UNKNOWN'.
    / ファイル名からSP-{n}を抽出する。見つからない場合は'UNKNOWN'。"""
    match = _SP_ID_PATTERN.search(csv_path.name)
    return match.group(1) if match else "UNKNOWN"


def notify_error(
    csv_path: Path,
    row: dict,
    error: str,
    sp_id: str = "",
    log_dir: Path = ERROR_DIR,
) -> None:
    """Write an error notification log and print it to stdout.
    / エラー通知ログを書き込みstdoutに出力する。

    In production this .log file would be detected by Power Automate,
    which would then send a Teams notification.
    / 本番環境では、この.logファイルをPower Automateが検知してTeams通知を送信する想定。
    """
    if not sp_id:
        sp_id = _extract_sp_id(csv_path)

    notify_to = os.environ.get("NOTIFY_TO", "admin@example.com")
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")

    message = (
        f"[ERROR NOTIFICATION] {datetime.now().isoformat()}\n"
        f"  SharePoint ID : {sp_id}\n"
        f"  CSV           : {csv_path}\n"
        f"  対象          : {row.get(COL_EMAIL, '?')}\n"
        f"  エラー        : {error}\n"
        f"  担当者        : {notify_to}\n"
        f"  リカバリ手順  : {csv_path} を rpa/inbox/ へ移動して再実行\n"
        f"  → 実環境では Power Automate が {LOG_SUFFIX} 新規作成を検知 → Teams 通知\n"
    )

    print(message)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{ts}_{sp_id}{LOG_SUFFIX}"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(message)
