# ---------------------------------------------------------------------------
# CSV schema constants / CSVスキーマ定数
#
# These names must match the Power Apps / SharePoint list column headers exactly.
# Power Automate exports the list to CSV with these headers verbatim.
# これらの名前は Power Apps / SharePoint のリスト列ヘッダーと完全に一致させること。
# Power Automate がリストをCSVエクスポートする際にそのまま使用される。
# ---------------------------------------------------------------------------

# Row key injected by runner at runtime (not a CSV column).
# runnerが実行時に注入する内部キー（CSV列ではない）。
INTERNAL_SP_ID_KEY = "_sp_id"

# CSV column headers / CSV列ヘッダー
COL_REQUEST_TYPE = "申請種別"
COL_USERNAME     = "ユーザー名"
COL_EMAIL        = "メールアドレス"
COL_DEPARTMENT   = "部署"
COL_PERM_REPORT  = "レポート"
COL_PERM_EXPORT  = "エクスポート"
COL_PERM_APPROVER = "承認者"

# Request type values / 申請種別の値
REQ_CREATE = "アカウント作成"
REQ_ADD    = "権限追加"
REQ_REMOVE = "権限削除"

# Permission column → API value mapping / 権限列名 → API値のマッピング
PERM_COLUMNS: dict[str, str] = {
    COL_PERM_REPORT:   "report",
    COL_PERM_EXPORT:   "export",
    COL_PERM_APPROVER: "approver",
}

# Required fields per request type / 申請種別ごとの必須項目
# Email is the unique identifier; usernames may repeat.
# メールアドレスが一意識別子。ユーザー名の重複は許可。
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    REQ_CREATE: (COL_USERNAME, COL_EMAIL, COL_DEPARTMENT),
    REQ_ADD:    (COL_EMAIL,),
    REQ_REMOVE: (COL_EMAIL,),
}
