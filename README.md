# Claude Code リリースノート通知

公式 [anthropics/claude-code](https://github.com/anthropics/claude-code) の CHANGELOG.md を GitHub Actions で定期監視し、新しいバージョンがリリースされたら:

1. **Claude Code CLI で日本語要約**を生成(Pro/Max サブスク認証。**API の従量課金なし**。未設定なら原文のまま)
2. **Discord / Slack に通知**(Webhook URL を設定したものだけ)
3. **専用サイト(GitHub Pages)** `docs/index.html` を自動更新

## 仕組み

```
GitHub Actions (3時間おき / 手動実行)
  └─ scripts/check_claude_code_releases.py
       ├─ CHANGELOG.md を取得・パース
       ├─ state/releases.json と比較して新バージョンを検知
       ├─ Claude Code CLI (claude -p, サブスク認証) で日本語要約
       ├─ Discord / Slack Webhook へ通知
       └─ docs/index.html を再生成 → コミット & プッシュ
```

- 初回実行は直近 10 件をサイトに登録するだけで、通知は送りません(通知の暴発防止)。
- 1 回の実行で要約・通知するのは最大 5 バージョンまでです。

## セットアップ

### 1. Secrets の設定(すべて任意)

リポジトリの **Settings → Secrets and variables → Actions → Secrets** に追加します。
Secret は 1 つずつ独立して登録・上書きできます(登録済みの値は再表示できないので手元に控えを推奨)。

| Secret 名 | 用途 | 取得方法 |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | 日本語要約(**Pro/Max サブスク枠を使用、API 課金なし**) | 手元のターミナルで `claude setup-token` を実行し、表示されたトークンを登録 |
| `DISCORD_WEBHOOK_URL` | Discord 通知 | Discord のチャンネル設定 → 連携サービス → Webhook を作成 |
| `SLACK_WEBHOOK_URL` | Slack 通知 | Slack アプリの Incoming Webhooks を有効化して URL を取得 |

何も設定しなくても動作します(その場合はサイト更新のみ・要約なし)。

`claude setup-token` の補足:

- Claude Pro / Max などのサブスクリプションが必要です
- トークンの有効期限は **1 年**。失効したら再実行して Secret を上書きしてください
- 要約の使用量はサブスクのレート制限と共有されます(1 バージョンあたり小さな 1 リクエストなので影響は僅少)
- Actions 上の CLI はサプライチェーン対策として最新版ではなく**公開から 7 日以上経過した最新バージョン**を自動選択してインストールします(`scripts/pick_cli_version.py`)

### 2. GitHub Pages の有効化(専用サイトを使う場合)

> GitHub Free プランでは Public リポジトリのみ Pages を利用できます。
> また Pages で公開したサイトは(リポジトリの公開設定に関わらず)URL を知っていれば誰でも閲覧できます。

**Settings → Pages** で:

- Source: `Deploy from a branch`
- Branch: `main` / フォルダ: `/docs`

公開 URL(例: `https://<ユーザー名>.github.io/<リポジトリ名>/`)が決まったら、
**Settings → Secrets and variables → Actions → Variables** に `PAGES_URL` として登録すると、Discord / Slack 通知にサイトへのリンクが付きます。

### 3. 動作確認・手動実行

**Actions → Claude Code リリース監視 → Run workflow** で手動実行できます。実行モードを選べます:

| モード | 動作 |
|---|---|
| `check` | 通常の監視(スケジュール実行と同じ) |
| `backfill` | 要約が付いていない既存エントリに日本語要約を後付けしてサイトを再生成(通知は送らない) |
| `backfill-all` | 全エントリの要約を作り直してサイトを再生成(要約形式を変えたときに使う) |
| `test-notify` | Discord / Slack にテスト通知を送って疎通確認(データは変更しない) |

要約の形式: 通知には「概要」と「重要な変更」だけの短い版が届き、サイトには全項目を日本語訳した「詳細」まで掲載されます。

## スケジュール変更

`.github/workflows/claude-code-release-watch.yml` の `cron` を編集してください(現在は 3 時間おき)。
