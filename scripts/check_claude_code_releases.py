#!/usr/bin/env python3
"""Claude Code リリースノート監視スクリプト。

公式 anthropics/claude-code の CHANGELOG.md を取得し、新しいバージョンを検知したら:
  1. Claude Code CLI (Pro/Max サブスク認証) で日本語要約を生成
     (CLAUDE_CODE_OAUTH_TOKEN があれば。API の従量課金は使わない)
  2. Discord / Slack の Webhook に通知 (それぞれ URL が設定されていれば)
  3. docs/index.html (GitHub Pages 用サイト) を再生成

状態は state/releases.json に保存し、GitHub Actions がコミットして永続化する。
初回実行時は直近 INITIAL_ENTRIES 件をサイトに登録するだけで、通知は送らない。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
CHANGELOG_HTML_URL = "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md"

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "state" / "releases.json"
SITE_FILE = REPO_ROOT / "docs" / "index.html"

INITIAL_ENTRIES = 10    # 初回実行でサイトに載せる件数
MAX_NOTIFY_PER_RUN = 5  # 1回の実行で要約・通知する最大バージョン数(暴発防止)
MAX_SITE_ENTRIES = 50   # サイトに表示する最大件数 (全履歴は state/releases.json に残る)

SUMMARY_MODEL = "sonnet"  # claude CLI の --model に渡すエイリアス


# ---------------------------------------------------------------- changelog

def fetch_changelog() -> str:
    req = urllib.request.Request(CHANGELOG_URL, headers={"User-Agent": "claude-code-release-watcher"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_changelog(text: str) -> list[dict]:
    """CHANGELOG.md を [{version, notes(list[str])}] に変換。新しい順で返る。"""
    releases: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\d+\.\d+\.\d+)\s*$", line)
        if m:
            current = {"version": m.group(1), "notes": []}
            releases.append(current)
            continue
        if current is not None:
            m = re.match(r"^[-*]\s+(.*)$", line)
            if m:
                current["notes"].append(m.group(1).strip())
    return releases


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


# ---------------------------------------------------------------- summarize

def summarize_ja(version: str, notes: list[str]) -> str | None:
    """Claude Code CLI (サブスク認証) で日本語要約を生成。トークン未設定/失敗時は None。

    API キー課金ではなく Pro/Max サブスクリプションの利用枠を使う。
    CI ではトークン、ローカル実行ではログイン済みの claude コマンドをそのまま使える。
    """
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or shutil.which("claude")):
        return None

    notes_text = "\n".join(f"- {n}" for n in notes)
    prompt = (
        "以下は開発ツール Claude Code の新バージョンのリリースノートです。"
        "開発者向けに、重要な変更から順に簡潔な日本語の箇条書き(3〜6項目)で要約してください。"
        "各項目は「新機能:」「修正:」「変更:」のいずれかで始め、影響の大きい変更には末尾に 🔥 を付け、"
        "細かいバグ修正はまとめて1項目にしてください。"
        "前置きや結びの文は不要で、箇条書きだけを出力してください。\n\n"
        f"# Claude Code v{version}\n{notes_text}"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", SUMMARY_MODEL],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"warning: 要約に失敗 (v{version}): {result.stderr[:300]}", file=sys.stderr)
            return None
        return result.stdout.strip() or None
    except FileNotFoundError:
        print("warning: claude コマンドが見つからないため要約をスキップ", file=sys.stderr)
        return None
    except Exception as e:  # 要約失敗は致命的でないので原文にフォールバック
        print(f"warning: 要約に失敗 (v{version}): {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- notify

def _post_json(url: str, payload: dict, label: str) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "claude-code-release-watcher"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"{label}: 通知送信 OK (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"warning: {label} への通知に失敗: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
    except Exception as e:
        print(f"warning: {label} への通知に失敗: {e}", file=sys.stderr)


def notify_discord(version: str, body: str, pages_url: str | None) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    footer = f"\n\n[CHANGELOG]({CHANGELOG_HTML_URL})"
    if pages_url:
        footer += f" | [まとめサイト]({pages_url})"
    _post_json(url, {
        "embeds": [{
            "title": f"Claude Code v{version} リリース",
            "url": CHANGELOG_HTML_URL,
            "description": (body[:3800] + footer),
            "color": 0xD97706,
        }],
    }, "Discord")


def notify_slack(version: str, body: str, pages_url: str | None) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    footer = f"\n\n<{CHANGELOG_HTML_URL}|CHANGELOG>"
    if pages_url:
        footer += f" | <{pages_url}|まとめサイト>"
    _post_json(url, {
        "text": f":sparkles: *Claude Code v{version} リリース*\n\n{body[:3000]}{footer}",
    }, "Slack")


# ---------------------------------------------------------------- site

SITE_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code リリースノート</title>
<style>
  :root {{
    --bg: #faf9f5; --card: #ffffff; --text: #1f1e1d; --muted: #6b6a67;
    --accent: #d97706; --border: #e8e5df;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1a1915; --card: #24221d; --text: #eceae4; --muted: #a3a09a;
      --accent: #f59e0b; --border: #38352e;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: "Hiragino Sans", "Noto Sans JP", system-ui, sans-serif;
    line-height: 1.75; padding: 2rem 1rem 4rem;
  }}
  main {{ max-width: 760px; margin: 0 auto; }}
  header h1 {{ font-size: 1.6rem; margin-bottom: .25rem; }}
  header p {{ color: var(--muted); font-size: .9rem; margin-bottom: 2rem; }}
  header a {{ color: var(--accent); }}
  article {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.25rem;
  }}
  article h2 {{ font-size: 1.15rem; display: flex; align-items: baseline; gap: .6rem; }}
  article h2 .date {{ font-size: .8rem; font-weight: normal; color: var(--muted); }}
  .summary {{ margin-top: .75rem; white-space: pre-wrap; }}
  details {{ margin-top: .75rem; }}
  summary {{ cursor: pointer; color: var(--muted); font-size: .85rem; }}
  details ul {{ margin: .5rem 0 0 1.25rem; font-size: .85rem; color: var(--muted); }}
  .badge {{
    font-size: .7rem; background: var(--accent); color: #fff;
    border-radius: 999px; padding: .1rem .55rem; font-weight: 600;
  }}
</style>
</head>
<body>
<main>
  <header>
    <h1>Claude Code リリースノート</h1>
    <p>最終更新: {updated} ・ <a href="{changelog_url}">公式 CHANGELOG</a> を自動監視して日本語要約を掲載しています</p>
  </header>
{articles}
</main>
</body>
</html>
"""


def render_site(releases: list[dict]) -> str:
    articles = []
    for i, rel in enumerate(releases[:MAX_SITE_ENTRIES]):
        version = html.escape(rel["version"])
        date = html.escape(rel.get("detected_at", "")[:10])
        badge = ' <span class="badge">NEW</span>' if i == 0 else ""
        if rel.get("summary_ja"):
            body = f'<div class="summary">{html.escape(rel["summary_ja"])}</div>'
        else:
            body = ""
        notes_items = "\n".join(f"<li>{html.escape(n)}</li>" for n in rel.get("notes", []))
        details = (
            f"<details><summary>原文リリースノート ({len(rel.get('notes', []))}件)</summary>"
            f"<ul>{notes_items}</ul></details>"
        )
        articles.append(
            f'<article>\n<h2>v{version}{badge} <span class="date">{date}</span></h2>\n{body}\n{details}\n</article>'
        )
    return SITE_TEMPLATE.format(
        updated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        changelog_url=CHANGELOG_HTML_URL,
        articles="\n".join(articles),
    )


# ---------------------------------------------------------------- main

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_version": None, "releases": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_site(state: dict) -> None:
    SITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_FILE.write_text(render_site(state["releases"]), encoding="utf-8")


def backfill() -> int:
    """要約が未生成の既存エントリ (初期登録分など) に日本語要約を後付けする。"""
    state = load_state()
    pending = [r for r in state["releases"] if not r.get("summary_ja")]
    if not pending:
        print("要約が未生成のエントリはありません")
        return 0

    print(f"{len(pending)} 件のエントリを要約します")
    ok = 0
    for r in pending:
        summary = summarize_ja(r["version"], r["notes"])
        if summary:
            r["summary_ja"] = summary
            ok += 1
            print(f"v{r['version']}: 要約完了")
        else:
            print(f"v{r['version']}: 要約失敗 (原文のまま)", file=sys.stderr)

    save_state(state)
    write_site(state)
    print(f"完了: {ok}/{len(pending)} 件に要約を追加し、サイトを再生成しました")
    return 0 if ok else 1


def test_notify() -> int:
    """Discord / Slack への疎通確認用にテスト通知を送る。state は変更しない。"""
    if not (os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL")):
        print("error: DISCORD_WEBHOOK_URL / SLACK_WEBHOOK_URL のどちらも設定されていません",
              file=sys.stderr)
        return 1

    state = load_state()
    pages_url = os.environ.get("PAGES_URL") or None
    if state["releases"]:
        rel = state["releases"][0]
        body = rel.get("summary_ja") or "\n".join(f"- {n}" for n in rel["notes"])
        version = f"{rel['version']} (テスト)"
    else:
        version, body = "0.0.0 (テスト)", ""
    body = "【テスト送信】通知の疎通確認です。実際のリリースではありません。\n\n" + body

    notify_discord(version, body, pages_url)
    notify_slack(version, body, pages_url)
    return 0


def check() -> int:
    changelog = parse_changelog(fetch_changelog())
    if not changelog:
        print("error: CHANGELOG のパースに失敗", file=sys.stderr)
        return 1

    state = load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pages_url = os.environ.get("PAGES_URL") or None

    if state["last_version"] is None:
        # 初回: 直近の数件をサイトに登録するだけ。通知はしない。
        print(f"初回実行: 最新 {INITIAL_ENTRIES} 件を登録 (通知なし)")
        state["releases"] = [
            {"version": r["version"], "notes": r["notes"], "summary_ja": None, "detected_at": now}
            for r in changelog[:INITIAL_ENTRIES]
        ]
        state["last_version"] = changelog[0]["version"]
    else:
        last_key = version_key(state["last_version"])
        new_releases = [r for r in changelog if version_key(r["version"]) > last_key]
        if not new_releases:
            print(f"新しいリリースなし (最新: v{state['last_version']})")
            # サイトの「最終更新」だけは更新せず、変更なしで終了
            return 0

        print(f"新しいリリースを {len(new_releases)} 件検知: "
              + ", ".join("v" + r["version"] for r in new_releases))
        # 古い方から時系列順に処理。件数が多すぎる場合は新しい方を優先して要約・通知する。
        chronological = list(reversed(new_releases))
        targets = chronological[-MAX_NOTIFY_PER_RUN:]
        skipped = chronological[:-MAX_NOTIFY_PER_RUN] if len(chronological) > MAX_NOTIFY_PER_RUN else []
        for r in skipped:
            state["releases"].insert(0, {
                "version": r["version"], "notes": r["notes"],
                "summary_ja": None, "detected_at": now,
            })
        for r in targets:
            summary = summarize_ja(r["version"], r["notes"])
            body = summary or "\n".join(f"- {n}" for n in r["notes"])
            notify_discord(r["version"], body, pages_url)
            notify_slack(r["version"], body, pages_url)
            state["releases"].insert(0, {
                "version": r["version"], "notes": r["notes"],
                "summary_ja": summary, "detected_at": now,
            })
        state["last_version"] = changelog[0]["version"]

    save_state(state)
    write_site(state)
    print(f"state と docs/index.html を更新 (最新: v{state['last_version']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code リリースノート監視")
    parser.add_argument(
        "--mode", choices=["check", "backfill", "test-notify"], default="check",
        help="check=通常の監視 / backfill=既存エントリを日本語要約 / test-notify=通知の疎通確認",
    )
    args = parser.parse_args()
    if args.mode == "backfill":
        return backfill()
    if args.mode == "test-notify":
        return test_notify()
    return check()


if __name__ == "__main__":
    sys.exit(main())
