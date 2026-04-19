"""
Notion キューをポーリングして WebUI に txt2img リクエストを投げ、
結果を Google Drive へ保存し Notion レコードを更新する。

環境変数 (startup.sh で /notebooks/.env から source される):
  NOTION_TOKEN          — Notion Internal Integration トークン
  NOTION_QUEUE_DB_ID    — 生成キューの Notion Database ID
"""

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DB_ID = os.environ.get("NOTION_QUEUE_DB_ID")
WEBUI_API = "http://127.0.0.1:7860"
OUT_DIR = "/storage/generated"
POLL_SEC = 30

if not NOTION_TOKEN or not DB_ID:
    print("FATAL: NOTION_TOKEN / NOTION_QUEUE_DB_ID が未設定です。", file=sys.stderr)
    sys.exit(1)

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("FATAL: notion-client 未インストール。venv に pip install notion-client してください。", file=sys.stderr)
    sys.exit(1)

notion = Client(auth=NOTION_TOKEN)
os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def wait_webui(timeout: int = 600):
    """WebUI が /sdapi/v1/sd-models に応答するまで待つ（最大 timeout 秒）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{WEBUI_API}/sdapi/v1/sd-models", timeout=5)
            if r.ok and isinstance(r.json(), list) and r.json():
                print("✅ WebUI 準備完了")
                return
        except Exception:
            pass
        time.sleep(10)
    raise RuntimeError(f"WebUI が {timeout}秒 経っても応答しません。")


def update_notion(page_id: str, **props):
    notion.pages.update(page_id=page_id, properties=props)


def get_text(prop) -> str:
    rt = prop.get("rich_text", [])
    return rt[0]["plain_text"] if rt else ""


def get_number(prop, default=None):
    return prop.get("number") if prop.get("number") is not None else default


def get_select(prop, default=None) -> str:
    sel = prop.get("select")
    return sel["name"] if sel else default


def fetch_pending():
    """FIFO (created_time 昇順) で pending を1件取得。"""
    res = notion.databases.query(
        database_id=DB_ID,
        filter={"property": "Status", "select": {"equals": "pending"}},
        sorts=[{"timestamp": "created_time", "direction": "ascending"}],
        page_size=1,
    )
    results = res.get("results", [])
    return results[0] if results else None


def rclone_upload(local_path: str, remote_path: str) -> str:
    """rclone で Drive にアップロードし共有リンクを返す。"""
    subprocess.run(
        ["rclone", "copyto", local_path, remote_path],
        check=True,
        timeout=120,
    )
    result = subprocess.run(
        ["rclone", "link", remote_path],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_one(page: dict):
    pid = page["id"]
    p = page["properties"]

    prompt = get_text(p.get("Prompt", {}))
    if not prompt.strip():
        update_notion(
            pid,
            Status={"select": {"name": "failed"}},
            Error={"rich_text": [{"text": {"content": "Prompt が空です。"}}]},
        )
        print(f"[{pid[-6:]}] SKIP: Prompt 空")
        return

    negative = get_text(p.get("Negative", {}))
    steps    = int(get_number(p.get("Steps", {}), 25))
    cfg      = float(get_number(p.get("CFG", {}), 7.0))
    w        = int(get_number(p.get("Width", {}), 512))
    h        = int(get_number(p.get("Height", {}), 768))
    sampler  = get_select(p.get("Sampler", {}), "DPM++ 2M Karras")
    seed     = int(get_number(p.get("Seed", {}), -1))

    print(f"[{pid[-6:]}] 生成開始: {prompt[:60]}...")
    update_notion(pid, Status={"select": {"name": "running"}})

    try:
        r = requests.post(
            f"{WEBUI_API}/sdapi/v1/txt2img",
            timeout=600,
            json={
                "prompt": prompt,
                "negative_prompt": negative,
                "steps": steps,
                "cfg_scale": cfg,
                "width": w,
                "height": h,
                "sampler_name": sampler,
                "seed": seed,
            },
        )
        r.raise_for_status()
        body = r.json()

        img_b64 = body["images"][0]

        # レスポンスの info から実際の seed を取得
        info = {}
        try:
            info = json.loads(body.get("info", "{}"))
        except Exception:
            pass
        used_seed = info.get("seed", seed)

        # ファイル名に seed を含める（上書き衝突防止）
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        fname = f"{ts}_{pid[-6:]}_s{used_seed}.png"
        local = os.path.join(OUT_DIR, fname)

        with open(local, "wb") as f:
            f.write(base64.b64decode(img_b64))
        print(f"  保存: {local}")

        remote = f"gdrive:Antigravity/generated/{fname}"
        link = rclone_upload(local, remote)
        print(f"  Drive: {link}")

        update_notion(
            pid,
            Status={"select": {"name": "done"}},
            **{"Result URL": {"url": link}},
        )
        print(f"[{pid[-6:]}] 完了")

    except Exception as e:
        err_msg = str(e)[:1800]
        print(f"[{pid[-6:]}] ERROR: {err_msg}", file=sys.stderr)
        update_notion(
            pid,
            Status={"select": {"name": "failed"}},
            Error={"rich_text": [{"text": {"content": err_msg}}]},
        )


def main():
    print("=== auto_gen_worker 起動 ===")
    wait_webui()

    while True:
        try:
            page = fetch_pending()
            if page:
                run_one(page)
            else:
                time.sleep(POLL_SEC)
        except APIResponseError as e:
            # Notion API レート超過の場合は exponential backoff
            if e.status == 429:
                wait = 60
                print(f"Notion 429 — {wait}s 待機")
                time.sleep(wait)
            else:
                print(f"Notion API エラー: {e}", file=sys.stderr)
                time.sleep(POLL_SEC)
        except Exception as e:
            print(f"予期しないエラー: {e}", file=sys.stderr)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
