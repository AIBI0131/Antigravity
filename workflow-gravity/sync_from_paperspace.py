"""
Paperspace から生成画像をダウンロードして output/ に保存し、Paperspace 側から削除する。
sd_url.json から WebUI URL を自動取得。
使い方: python sync_from_paperspace.py [--loop] [--interval 60]
"""
import argparse
import json
import time
from pathlib import Path

import requests

SD_URL_PATH = Path(__file__).parent.parent / "sd_url.json"
LOCAL_OUTPUT = Path(__file__).parent / "output" / "raw"


def get_base_url() -> str:
    if SD_URL_PATH.exists():
        try:
            data = json.loads(SD_URL_PATH.read_text(encoding="utf-8"))
            url = data.get("url", "").rstrip("/")
            if url:
                return url
        except Exception:
            pass
    raise RuntimeError(f"sd_url.json が見つかりません: {SD_URL_PATH}")


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
