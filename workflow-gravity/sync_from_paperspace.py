"""
Paperspace から生成画像をダウンロードして output/ に保存し、Paperspace 側から削除する。
URL は Notion から自動取得（NOTION_TOKEN + NOTION_URL_PAGE_ID が設定されている場合）。
フォールバックとして sd_url.json も参照する。
使い方: python sync_from_paperspace.py [--loop] [--interval 60]
"""
import argparse
import json
import os
import time
from pathlib import Path

import requests

# ローカル .env 読み込み（Antigravity/.env）
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_URL_PAGE_ID = os.environ.get("NOTION_URL_PAGE_ID")
SD_URL_PATH = Path(__file__).parent.parent / "sd_url.json"
LOCAL_OUTPUT = Path(__file__).parent / "output" / "raw"


def get_url_from_notion() -> str | None:
    if not (NOTION_TOKEN and NOTION_URL_PAGE_ID):
        return None
    try:
        r = requests.get(
            f"https://api.notion.com/v1/pages/{NOTION_URL_PAGE_ID}",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        r.raise_for_status()
        title = r.json()["properties"]["title"]["title"]
        if title:
            url = title[0]["plain_text"].rstrip("/")
            if url.startswith("https://"):
                return url
    except Exception as e:
        print(f"[Notion] URL 取得失敗: {e}")
    return None


def get_base_url() -> str:
    url = get_url_from_notion()
    if url:
        return url
    if SD_URL_PATH.exists():
        try:
            data = json.loads(SD_URL_PATH.read_text(encoding="utf-8"))
            url = data.get("url", "").rstrip("/")
            if url:
                return url
        except Exception:
            pass
    raise RuntimeError("URL が取得できません（Notion 未設定 & sd_url.json なし）")


def sync_once(base_url: str) -> int:
    resp = requests.get(f"{base_url}/gravity/list_outputs", timeout=15)
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if not files:
        print("  新しい画像なし")
        return 0

    LOCAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for f in files:
        path = f["path"]
        local_path = LOCAL_OUTPUT / Path(path).name
        if local_path.exists():
            continue
        try:
            dl = requests.get(f"{base_url}/gravity/download/{path}", timeout=60)
            dl.raise_for_status()
            local_path.write_bytes(dl.content)
            requests.delete(f"{base_url}/gravity/delete/{path}", timeout=15)
            print(f"  ✅ {path} → {local_path.name} (削除済み)")
            downloaded += 1
        except Exception as e:
            print(f"  ⚠️ {path} スキップ: {e}")
    return downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="繰り返し実行")
    parser.add_argument("--interval", type=int, default=60, help="ループ間隔(秒)")
    args = parser.parse_args()

    while True:
        try:
            base_url = get_base_url()
            print(f"[sync] {base_url}")
            count = sync_once(base_url)
            print(f"[sync] {count} 枚ダウンロード完了")
        except Exception as e:
            print(f"[sync] エラー: {e}")

        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
