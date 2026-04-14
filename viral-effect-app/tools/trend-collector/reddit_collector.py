"""
reddit_collector.py — Reddit からバズ画像投稿を収集し Notion DB に登録する

使い方:
    python reddit_collector.py [--dry-run] [--limit 50]

必要な環境変数 (.env または OS 環境):
    REDDIT_CLIENT_ID      — Reddit API クライアント ID
    REDDIT_CLIENT_SECRET  — Reddit API クライアントシークレット
    REDDIT_USER_AGENT     — 任意の User-Agent 文字列
    NOTION_TOKEN          — ntn_xxx 形式の Notion インテグレーショントークン
    NOTION_DB_ID          — 登録先 Notion データベース ID

対象 subreddit:
    r/pics, r/interestingasfuck, r/photoshopbattles, r/BeAmazed

条件:
    - upvote 数 >= MIN_UPVOTES (デフォルト 1000)
    - 画像付き投稿 (url が .jpg/.png/.gif/.webp で終わる、または i.redd.it / imgur)
    - 過去 7 日以内
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# ── 設定 ──────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env", override=False)

REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "viral-effect-collector/1.0")
NOTION_TOKEN         = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID         = os.getenv("NOTION_DB_ID", "34188bba-289a-81fa-b0bd-f572f137c43c")

SUBREDDITS   = ["pics", "interestingasfuck", "photoshopbattles", "BeAmazed"]
MIN_UPVOTES  = 1000
MAX_AGE_DAYS = 7
POSTS_LIMIT  = 50   # per subreddit

IMAGE_EXTS   = re.compile(r"\.(jpe?g|png|gif|webp)$", re.IGNORECASE)
IMAGE_HOSTS  = ("i.redd.it", "i.imgur.com", "imgur.com")

NOTION_API   = "https://api.notion.com/v1"
NOTION_VER   = "2022-06-28"

# ── Reddit 認証 ───────────────────────────────────────────────────────────────

def get_reddit_token() -> str:
    """Reddit OAuth2 アクセストークンを取得する (application-only)"""
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": REDDIT_USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Reddit 投稿取得 ────────────────────────────────────────────────────────────

def is_image_post(post: dict[str, Any]) -> bool:
    url = post.get("url", "")
    domain = post.get("domain", "")
    return bool(IMAGE_EXTS.search(url)) or any(h in domain for h in IMAGE_HOSTS)


def fetch_top_posts(subreddit: str, token: str, limit: int = POSTS_LIMIT) -> list[dict[str, Any]]:
    """指定 subreddit から週間 top 投稿を取得する"""
    url = f"https://oauth.reddit.com/r/{subreddit}/top"
    resp = requests.get(
        url,
        params={"t": "week", "limit": limit},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": REDDIT_USER_AGENT,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return [p["data"] for p in resp.json()["data"]["children"]]


def filter_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """画像付き・高 upvote・新しい投稿に絞り込む"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    result = []
    for p in posts:
        if p.get("score", 0) < MIN_UPVOTES:
            continue
        if not is_image_post(p):
            continue
        created = datetime.fromtimestamp(p.get("created_utc", 0), tz=timezone.utc)
        if created < cutoff:
            continue
        result.append(p)
    return result


# ── Notion 操作 ───────────────────────────────────────────────────────────────

def _notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VER,
    }


def fetch_existing_urls() -> set[str]:
    """Notion DB にすでに登録されている参考URL一覧を取得する (重複防止)"""
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


def register_to_notion(post: dict[str, Any], subreddit: str, dry_run: bool = False) -> bool:
    """投稿を Notion DB に 1 件登録する。成功したら True を返す。"""
    post_url = f"https://www.reddit.com{post['permalink']}"
    title = post.get("title", "")[:200]  # Notion title 上限
    score = post.get("score", 0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    memo = f"r/{subreddit} • {score:,} upvotes • {post.get('num_comments', 0):,} comments"

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "エフェクト名": {
                "title": [{"type": "text", "text": {"content": title}}]
            },
            "参考URL": {"url": post_url},
            "SNSソース": {"select": {"name": "Reddit"}},
            "発見日": {"date": {"start": today}},
            "実装難易度": {"select": {"name": "unknown"}},
            "ステータス": {"select": {"name": "未検討"}},
            "メモ": {
                "rich_text": [{"type": "text", "text": {"content": memo}}]
            },
        },
    }

    if dry_run:
        print(f"  [DRY-RUN] Would register: {title[:60]}")
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


# ── メイン ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Reddit バズ投稿を Notion に登録")
    parser.add_argument("--dry-run", action="store_true", help="Notion には書き込まない")
    parser.add_argument("--limit", type=int, default=POSTS_LIMIT, help="subreddit あたりの取得数")
    args = parser.parse_args()

    # 設定チェック
    missing = [k for k, v in {
        "REDDIT_CLIENT_ID": REDDIT_CLIENT_ID,
        "REDDIT_CLIENT_SECRET": REDDIT_CLIENT_SECRET,
        "NOTION_TOKEN": NOTION_TOKEN,
    }.items() if not v]
    if missing:
        print(f"[ERROR] 環境変数が未設定: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    print(f"[reddit_collector] 開始 — subreddits: {SUBREDDITS}")
    print(f"  MIN_UPVOTES={MIN_UPVOTES}, MAX_AGE_DAYS={MAX_AGE_DAYS}, dry_run={args.dry_run}")

    token = get_reddit_token()
    existing_urls = set() if args.dry_run else fetch_existing_urls()
    print(f"  既存 URL 数: {len(existing_urls)}")

    total_registered = 0
    total_skipped = 0

    for subreddit in SUBREDDITS:
        print(f"\n  [r/{subreddit}] 投稿を取得中...")
        try:
            posts = fetch_top_posts(subreddit, token, limit=args.limit)
        except Exception as e:
            print(f"  [WARN] r/{subreddit} 取得失敗: {e}", file=sys.stderr)
            continue

        filtered = filter_posts(posts)
        print(f"  → {len(posts)} 件取得 / {len(filtered)} 件フィルタ通過")

        for post in filtered:
            post_url = f"https://www.reddit.com{post['permalink']}"
            if post_url in existing_urls:
                total_skipped += 1
                continue

            success = register_to_notion(post, subreddit, dry_run=args.dry_run)
            if success:
                existing_urls.add(post_url)
                total_registered += 1
                print(f"  + [{post.get('score', 0):,} pts] {post.get('title', '')[:60]}")
            time.sleep(0.3)  # Notion API レート制限対策

    print(f"\n[完了] 登録: {total_registered} 件 / スキップ: {total_skipped} 件")


if __name__ == "__main__":
    main()
