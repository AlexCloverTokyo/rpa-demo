# RPA × Microsoft 365 全チェーン自動化 / RPA Self-Healing Agent

製造業クライアントのアカウント管理業務を、Power Apps + Power Automate + Playwright + Teams で全自動化したプロジェクトの記録と、その先のAI自愈層（LangGraph + MCP）への発展。

A record of automating account-management operations for a manufacturing client — chaining Power Apps, Power Automate, Playwright, and Teams — and its evolution toward an AI self-healing layer (LangGraph + MCP).

---

## 概要 / Overview

JIRAチケット経由で外部ベンダーへ外注していたアカウント作成・権限変更業務（1件数万円規模・月120〜200件）を、Microsoft 365 とブラウザ自動化で内製化しました。

By automating account creation and permission changes that were previously outsourced to an external vendor (tens of thousands of yen/case, 120–200 cases/month), this project replaced the manual workflow with an in-house Microsoft 365 + browser automation pipeline.

### 主な成果 / Key Results

| 指標 / Metric | 改善前 / Before | 改善後 / After |
|---|---|---|
| 外注費 / Outsourcing cost | 年間数千万円規模 / tens of millions ¥/year | ゼロ（内製化）/ Zero (in-house) |
| 処理時間 / Processing time | 30〜60分（手作業）/ 30–60 min (manual) | 約1.5分（自動）/ ~1.5 min (automated) |
| スケーラビリティ / Scalability | 処理追いつかず / bottlenecked | サーバー増設で対応 / horizontal scale |

> ※ 改善後はサーバー稼働費・Power Automateライセンス費・内製保守工数がかかります。外注費と比較した大幅削減という意味です。
>
> Note: Post-automation costs include server operation, Power Automate licensing, and in-house maintenance. The comparison is against the previous outsourcing spend.

---

## アーキテクチャ / Architecture

```
① 申請層 / Request    Power Apps → SharePoint List → Power Automate（承認 / Approval）
② 伝達層 / Dispatch   承認通過 → CSV生成 → 空きサーバーへ配分 → OneDrive同期
                       Approved → CSV generated → dispatched to available server → OneDrive sync
③ 実行層 / Execution  watchdog監視 → Playwright実行（headless、複数サーバーで分散可）
                       watchdog → Playwright (headless, horizontally scalable)
④ 通知層 / Notify     実行結果 → Power Automate → Teams通知
                       Result → Power Automate → Teams notification
```

---

## Quick Start

**必要なもの / Prerequisites：** Docker Desktop + Python 3.13 + uv

### Step 1 — uv のインストール（初回のみ）/ Install uv (first time only)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows（PowerShell）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Step 2 — クローンとセットアップ / Clone and setup

```bash
git clone https://github.com/AlexCloverTokyo/rpa-demo
cd rpa-demo

# macOS/Linux
cp .env.example .env
# Windows (PowerShell)
Copy-Item .env.example .env
```

```bash
uv sync                            # 依存パッケージをインストール / Install dependencies
uv run playwright install chromium # Chromium をインストール（必須）/ Install Chromium (required)
```

### Step 3 — mock-site を起動 / Start mock-site

```bash
docker compose up -d
```

コンテナが healthy になるまで待ちます（初回ビルドは 1〜2 分）。
Wait until the container is healthy (first build takes 1–2 minutes).

```bash
docker compose ps   # "healthy" になるまで待つ / Wait until Status shows "healthy"
```

### Step 4 — RPA runner を起動（Terminal 1）/ Start RPA runner (Terminal 1)

```bash
uv run python -m rpa.runner
```

> `Watching .../rpa/inbox ...` のようにフルパスが表示されれば準備完了。このターミナルは起動したままにします（Ctrl+C で停止）。
>
> Ready when you see `Watching .../rpa/inbox ...` (absolute path). Keep this terminal running (Ctrl+C to stop).

### Step 5 — CSV を投入（Terminal 2）/ Inject CSV (Terminal 2)

```bash
# macOS/Linux
cp tests/sample_requests/SP-101_request.csv rpa/inbox/

# Windows（PowerShell）
Copy-Item tests\sample_requests\SP-101_request.csv rpa\inbox\
```

`rpa/results/` に `"status": "success"` の JSON が出れば動作確認完了です。
Check `rpa/results/` for a JSON file with `"status": "success"`.

---

### ファイルライフサイクル / File lifecycle

```
rpa/inbox/SP-101_request.csv                      ← 投入 / dropped
rpa/inbox/Processing_start_SP-101_request.csv     ← 処理中（状態可視化・二重検知防止）/ in-progress (visibility & dedup)
rpa/processed/Processing_end_SP-101_request.csv   ← 完了 / done
rpa/error/recovery/{SP-ID}/{timestamp}/           ← エラー時 / on error
```

> `rpa/inbox/` は実システムの「SharePoint → OneDrive 同期 → サーバー監視フォルダ」に対応します。
>
> In production, `rpa/inbox/` maps to the server's watched folder synced from SharePoint via OneDrive.

---

### サンプル CSV / Sample CSVs

`tests/sample_requests/` に以下のシナリオが含まれます。
The following scenarios are included in `tests/sample_requests/`:

| ファイル / File | 申請種別 / Type | 内容 / Description |
|---|---|---|
| SP-101_request.csv | アカウント作成 / Create account | 正常系 / Happy path |
| SP-102_request.csv | 権限追加 / Add permission | 正常系 / Happy path |
| SP-103_request.csv | アカウント作成 / Create account | 冪等性確認（既存ユーザー → skipped）/ Idempotency check (existing user → skipped) |
| SP-201_request.csv | アカウント作成 / Create account | ユーザー名欠損 / Missing username |
| SP-202_request.csv | アカウント作成 / Create account | メール欠損 / Missing email |
| SP-203_request.csv | アカウント作成 / Create account | 部署欠損 / Missing department |
| SP-204_request.csv | 権限追加 / Add permission | メール欠損 / Missing email |
| SP-205_request.csv | 権限追加 / Add permission | 対象ユーザー不在 / User not found |
| SP-206_request.csv | 発注処理（不明種別）/ Unknown type | 不明申請種別 → error / Unknown request type → error |
| SP-301_request.csv | 権限追加 / Add permission | 正常系 / Happy path |
| SP-302_request.csv | 権限追加 / Add permission | 権限列がすべて空 → error / All permission columns empty → error |
| SP-401_request.csv | 権限削除 / Remove permission | 正常系 / Happy path |
| SP-402_request.csv | 権限削除 / Remove permission | 正常系（別権限）/ Happy path (different permission) |
| SP-403_request.csv | 権限削除 / Remove permission | 対象ユーザー不在 / User not found |
| SP-404_request.csv | 権限削除 / Remove permission | メール欠損 / Missing email |
| SP-405_request.csv | 権限削除 / Remove permission | 権限列がすべて空 → error / All permission columns empty → error |
| SP-501_request.csv | 複数行 / Multi-row | 作成＋権限追加×2 / Create + add permission ×2 |
| SP-502_request.csv | 複数行 / Multi-row | 正常行＋不在ユーザー混在 / Mixed success and not-found |
| SP-503_request.csv | 複数行 / Multi-row | 作成＋権限削除 / Create + remove permission |

---

### chaos（故障注入）について / About chaos injection

> **⚠️ chaos 機能は Phase B（AI 自愈層）のデモ用に実装されています。**
> 現フェーズでは「RPA が意図的に失敗するシナリオ」を確認するために使用します。
> Phase B では、これらの失敗を LangGraph + MCP の AI エージェントが自動診断・復旧するレイヤーを追加予定です。
>
> **⚠️ The chaos features are implemented for Phase B (AI self-healing layer) demonstration.**
> In the current phase they let you observe failure scenarios. In Phase B, an AI agent built with LangGraph + MCP will auto-diagnose and recover from these failures.

`chaos_config.yaml` に2種類の故障注入モードがあります。
There are two chaos injection modes in `chaos_config.yaml`.

**① API chaos — ネットワーク / サーバー障害シミュレーション**
**① API chaos — network / server failure simulation**

`chaos.enabled: true` にすると、POST /accounts に確率的な遅延・500エラーを注入します。
Set `chaos.enabled: true` to inject probabilistic timeouts and 500 errors into POST /accounts.

```yaml
chaos:
  enabled: true
  rules:
    - path: "/accounts"
      method: "POST"
      fault: "timeout"      # 10秒遅延 / 10s delay
      probability: 0.3      # 30% の確率 / 30% chance
    - path: "/accounts"
      method: "POST"
      fault: "error_500"    # 500エラー / 500 error
      probability: 0.2      # 20% の確率 / 20% chance
```

**② Selector chaos — UI変更シミュレーション**
**② Selector chaos — UI change simulation**

`selector_chaos.enabled: true` にすると、アカウント作成フォームの送信ボタン ID が動的に変化します。
Set `selector_chaos.enabled: true` to dynamically change the submit button's ID on the account creation form.

- `rpa/playwright_tasks.py`（堅牢版）— `data-chaos-loaded` sentinel を待ってから操作するため、ID 変化に対応できます / Waits for the `data-chaos-loaded` sentinel before interacting — survives ID changes
- `rpa/playwright_tasks_fragile.py`（脆弱版）— 固定 ID（`#create-btn`）に直書きで依存するため、selector_chaos ON で即座に失敗します / Hard-codes `#create-btn` — fails immediately when selector_chaos is ON

```bash
uv run pytest tests/test_selector_chaos.py -v
# test_fragile_fails_under_selector_chaos   PASSED  ← 脆弱版は失敗 / fragile fails
# test_robust_succeeds_under_selector_chaos PASSED  ← 堅牢版は成功 / robust succeeds
```

設定変更は次のリクエスト時に自動で反映されます（コンテナ再起動不要）。
Changes take effect automatically on the next request — no container restart needed.

- `chaos.enabled` 変更 → 次の API リクエストで即反映 / `chaos.enabled` change → reflected on next API request
- `selector_chaos.enabled` 変更 → ブラウザをリロードすると反映 / `selector_chaos.enabled` change → reload the browser page

---

## 記事 / Articles

このプロジェクトについて2つの記事を近日公開予定です。
Two articles about this project are coming soon.

- **Qiita**（[年間数千万円の外注費をゼロに、Power Apps申請からPlaywright自動実行までの全チェーン設計](https://qiita.com/AlexClover/items/2e3809d836484409bbdb)）— 技術実装の詳細 / Implementation details（Power Apps + Power Automate + Playwright + Teams 全チェーン / full chain）
- **Zenn**（[年間数千万円の外注費をゼロに——なぜPlaywrightをMicrosoft 365の後段に置いたのか](https://zenn.dev/alexclover/articles/7a2fffa4fa2459)）— 設計判断のストーリー / Design decisions（なぜこの構成か・実運用で気づいたこと / why this architecture · lessons from production）

---

## ロードマップ / Roadmap

| フェーズ / Phase | 内容 / Description | 状態 / Status |
|---|---|---|
| Phase A | Microsoft 365 + Playwright 全チェーン自動化 / Full-chain automation | ✅ 完了 / Done |
| Phase B | AI 自愈層（LangGraph + MCP）/ AI self-healing layer | 🔧 実装中 / In progress |

**Phase B の予定 / Phase B plan：**

1. **OneDrive同期 → AWS SQS** — ファイル監視の遅延をメッセージキューで解消 / Replace file polling with message queue
2. **HTTP直接呼び出し** — 結果通知を OneDrive 経由から Power Automate HTTP トリガーに変更 / Direct HTTP callback instead of file-based notification
3. **AI自愈層** — Playwright 失敗時に AI が自動診断・復旧する Agent Harness を追加 / Add Agent Harness that auto-diagnoses and recovers from Playwright failures

---

## コンプライアンス / Compliance

本リポジトリは技術的な設計・実装の記録です。クライアント企業の固有名詞、実システムのURL・スクリーンショット、認証情報は一切含みません。コード例は説明用に再構成したものです。

This repository documents technical design and implementation only. It contains no client company names, real system URLs/screenshots, or credentials. Code samples are reconstructed for illustration.
