#!/usr/bin/env python3
"""インストールする Claude Code CLI のバージョンを選ぶ。

サプライチェーン対策として最新版は避け、「公開から MIN_AGE_DAYS 日以上
経過したバージョンのうち最新のもの」を標準出力に出す。
(公開直後に侵害が発覚して取り下げられるようなバージョンを引かないため)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

MIN_AGE_DAYS = 7
REGISTRY_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code"


def main() -> int:
    req = urllib.request.Request(REGISTRY_URL, headers={"User-Agent": "claude-code-release-watcher"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    available = set(data.get("versions", {}))  # 取り下げ済みバージョンを除外
    cutoff = datetime.now(timezone.utc) - timedelta(days=MIN_AGE_DAYS)

    candidates = []
    for ver, ts in data["time"].items():
        if ver in ("created", "modified") or "-" in ver or ver not in available:
            continue
        published = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if published <= cutoff:
            candidates.append((published, ver))

    if not candidates:
        print("error: 条件を満たすバージョンが見つかりません", file=sys.stderr)
        return 1

    candidates.sort()
    print(candidates[-1][1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
