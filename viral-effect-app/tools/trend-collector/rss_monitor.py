"""
rss_monitor.py — SNS トレンド RSS を監視し Notion DB に登録する

使い方:
    python rss_monitor.py [--dry-run] [--max-items 20]

必要な環境変数 (.env または OS 環境):
    NOTION_TOKEN  — ntn_xxx 形式の Notion インテグレーショントークン
    NOTION_DB_ID  — 登録先 Notion データベース ID (任意: デフォルトはハードコード値)

監視フィード:
    - X (Twitter) トレンド系 RSS (RSS Bridge 経由)
    - Pixiv デイリーランキング (画像加工カテゴリ)
    - Instagram ハッシュタグ関連 RSS (publicfeed / rss.app 等)
    - Google Trends RSS (JP 地域)

注意:
    - X の公式 RSS は 2023 年廃止。RSS Bridge か非公式ミラーを使用する。
    - Instagram の公式 RSS も廃止済み。サードパーティ RSS サービスを利用する。
    - FEED_SOURCES に独自フィード URL を追加して拡張可能。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv

# ── 設定 ──────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env", override=False)

NOTION_TOKEN  = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID  = os.getenv("NOTION_DB_ID", "34188bba-289a-81fa-b0bd-f572f137c43c")

NOTION_API    = "https://api.notion.com/v1"
NOTION_VER    = "2022-06-28"

# フィード定義: (名前, URL, SNSソース)
# ※ 実際に使用するフィード URL は環境に応じて変更してください
FEED_SOURCES: list[tuple[str, str, str]] = [
    # Google Trends (JP) — 画像・写真系キーワード
    (
        "Google Trends JP",
        "https://trends.google.co.jp/trending/rss?geo=JP",
        "その他",
    ),
    # Pixiv デイリーランキング — イラスト加工カテゴリ
    (
        "Pixiv デイリー (全年齢)",
        "https://www.pixiv.net/rss/illust/daily.xml",
        "その他",
    ),
    # ここに独自 RSS フィードを追加してください
    # (
    #     "X #photoeffect トレンド (RSS Bridge)",
    #     "https://your-rssbridge-instance/...?action=display&bridge=TwitterSearchBridge&q=%23photoeffect",
    #     "X",
    # ),
    # (
    #     "Instagram #photoediting RSS",
    #     "https://rss.app/feeds/your-feed-id.xml",
    #     "Instagram",
    # ),
]

# タイトルに含まれる場合に関心ありとみなすキーワード
PHOTO_KEYWORDS = re.compile(
    r"(filter|effect|edit|加工|エフェクト|フィルター|写真|photo|image|neon|glow|pop|viral|バズ)",
    re.IGNORECASE,
)


# ── Notion 操作 ───────────────────────────────────────────────────────────────

def _notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VER,
    }


def fetch_existing_urls() -> set[str]:
    """Notion DB に登録済みの URL を取得する (重複防止)"""
    existing: set[str] = set()
    cursor = None
    while True:
        payload: dict[str, Any] = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{NOTION_DB_ID}/query",
            headers=_notion_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            url = page["properties"].get("参考URL", {}).get("url") or ""
            if url:
                existing.add(url)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return existing


def register_to_notion(
    title: str,
    url: str,
    source_name: str,
    sns_source: str,
    memo: str,
    dry_run: bool = False,
) -> bool:
    """RSS エントリを Notion DB に登録する"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_title = title[:200]

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "エフェクト名": {
                "title": [{"type": "text", "text": {"content": safe_title}}]
            },
            "参考URL": {"url": url},
            "SNSソース": {"select": {"name": sns_source}},
            "発見日": {"date": {"start": today}},
            "実装難易度": {"select": {"name": "unknown"}},
            "ステータス": {"select": {"name": "未検討"}},
            "メモ": {
                "rich_text": [{"type": "text", "text": {"content": memo[:500]}}]
            },
        },
    }

    if dry_run:
        print(f"  [DRY-RUN] Would register: {safe_title[:60]}")
        return True

    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=_notion_headers(),
        json=payload,
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"  [ERROR] Notion API {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return False
    return True


# ── RSS 処理 ──────────────────────────────────────────────────────────────────

def process_feed(
    feed_name: str,
    feed_url: str,
    sns_source: str,
    existing_urls: set[str],
    max_items: int,
    dry_run: bool,
) -> tuple[int, int]:
    """1 つのフィードを処理して (registered, skipped) を返す"""
    registered = skipped = 0
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        print(f"  [WARN] フィード取得失敗 ({feed_name}): {e}", file=sys.stderr)
        return 0, 0

    if feed.bozo and not feed.entries:
        print(f"  [WARN] フィード解析エラー ({feed_name}): {feed.bozo_exception}", file=sys.stderr)
        return 0, 0

    print(f"  [{feed_name}] {len(feed.entries)} エントリ取得")
    count = 0

    for entry in feed.entries[:max_items]:
        if count >= max_items:
            break

        title = entry.get("title", "").strip()
        url   = entry.get("link", "").strip()

        if not title or not url:
            continue

        # キーワードフィルタ (Google Trends 等は全トレンドが来るので絞り込み)
        if "Google Trends" in feed_name and not PHOTO_KEYWORDS.search(title):
            continue

        if url in existing_urls:
            skipped += 1
            continue

        # メモ: published date + サマリー
        published = entry.get("published", "")
        summary   = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:200]
        memo = f"[{feed_name}] {published}\n{summary}".strip()

        success = register_to_notion(title, url, feed_name, sns_source, memo, dry_run=dry_run)
        if success:
            existing_urls.add(url)
            registered += 1
            print(f"  + {title[:70]}")
        count += 1
        time.sleep(0.3)

    return registered, skipped


# ── メイン ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="RSS トレンドを Notion に登録")
    parser.add_argument("--dry-run", action="store_true", help="Notion には書き込まない")
    parser.add_argument("--max-items", type=int, default=20, help="フィードあたりの最大登録数")
    args = parser.parse_args()

    if not NOTION_TOKEN:
        print("[ERROR] NOTION_TOKEN が未設定", file=sys.stderr)
        sys.exit(1)

    print(f"[rss_monitor] 開始 — feeds: {len(FEED_SOURCES)}, dry_run={args.dry_run}")

    existing_urls = set() if args.dry_run else fetch_existing_urls()
    print(f"  既存 URL 数: {len(existing_urls)}")

    total_registered = total_skipped = 0

    for feed_name, feed_url, sns_source in FEED_SOURCES:
        print(f"\n  フィード処理中: {feed_name}")
        r, s = process_feed(
            feed_name, feed_url, sns_source,
            existing_urls, args.max_items, args.dry_run
        )
        total_registered += r
        total_skipped    += s

    print(f"\n[完了] 登録: {total_registered} 件 / スキップ: {total_skipped} 件")


if __name__ == "__main__":
    main()
