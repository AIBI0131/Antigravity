"""
Paperspace Notebook 自動再起動 Watchdog

動作:
  1. Paperspace API で Notebook の state を確認
  2. state が Running でない → 再起動
  3. state が Running でも sd_url.json が古すぎる (>5h55m) → stop→start
  4. DRY_RUN=1 の場合は操作をスキップして状態を表示するだけ

必要環境変数 (GitHub Secrets / ローカル .env):
  PAPERSPACE_API_KEY      — Paperspace API キー
  PAPERSPACE_NOTEBOOK_ID  — ノートブック ID (例: rdlpoamf83uqqub)
  GDRIVE_SA_JSON          — Google Service Account JSON 文字列
  GDRIVE_SD_URL_FILE_ID   — Drive 上の sd_url.json の fileId
  DRY_RUN                 — 1 にすると読み取り専用モード（Optional）
"""

import json
import os
import sys
import time

import requests

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
API_KEY = os.environ["PAPERSPACE_API_KEY"]
NOTEBOOK_ID = os.environ["PAPERSPACE_NOTEBOOK_ID"]

STALE_THRESHOLD = 5.9 * 3600  # 5時間54分を超えたら stale とみなす

# ---------------------------------------------------------------------------
# Paperspace API — v1 (legacy) と v2 (DO) の両方を試みる
# ---------------------------------------------------------------------------

ENDPOINTS = [
    {
        "base": "https://api.paperspace.com/v1",
        "header_key": "Authorization",
        "header_val": f"Bearer {API_KEY}",
        "state_key": "state",
    },
    {
        "base": "https://api.paperspace.io",
        "header_key": "x-api-key",
        "header_val": API_KEY,
        "state_key": "state",
    },
]

_active_endpoint = None


def _try_endpoint(ep: dict, path: str, method: str = "GET", **kw):
    url = ep["base"] + path
    headers = {ep["header_key"]: ep["header_val"], "Content-Type": "application/json"}
    r = requests.request(method, url, headers=headers, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def paperspace(path: str, method: str = "GET", **kw):
    global _active_endpoint
    if _active_endpoint:
        return _try_endpoint(_active_endpoint, path, method, **kw)

    last_err = None
    for ep in ENDPOINTS:
        try:
            result = _try_endpoint(ep, f"/notebooks/{NOTEBOOK_ID}", "GET")
            _active_endpoint = ep
            print(f"  API endpoint: {ep['base']}")
            if method == "GET" and path == f"/notebooks/{NOTEBOOK_ID}":
                return result
            return _try_endpoint(ep, path, method, **kw)
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Paperspace API 疎通不可 (両エンドポイント失敗): {last_err}")


def notebook_state() -> str:
    data = paperspace(f"/notebooks/{NOTEBOOK_ID}")
    state = data.get("state", data.get("status", "unknown"))
    return state


# ---------------------------------------------------------------------------
# Google Drive — sd_url.json の freshness チェック
# ---------------------------------------------------------------------------

def read_sd_url_age() -> float:
    """sd_url.json の timestamp から経過秒数を返す。取得失敗時は inf。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_info = json.loads(os.environ["GDRIVE_SA_JSON"])
        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        creds.refresh(Request())
        drive = build("drive", "v3", credentials=creds)
        content = (
            drive.files()
            .get_media(fileId=os.environ["GDRIVE_SD_URL_FILE_ID"])
            .execute()
        )
        data = json.loads(content)
        age = time.time() - data["timestamp"]
        print(f"  sd_url.json timestamp: {data['timestamp']}, age: {age/3600:.2f}h, url: {data.get('url','?')}")
        return age
    except Exception as e:
        print(f"  WARN: sd_url.json 取得失敗: {e}")
        return float("inf")


# ---------------------------------------------------------------------------
# 停止→完全停止 を最大5分待機
# ---------------------------------------------------------------------------

def wait_until_stopped(max_wait: int = 300):
    print("  stop 完了を待機中...")
    for _ in range(max_wait // 10):
        time.sleep(10)
        st = notebook_state()
        print(f"  state = {st}")
        if st.lower() == "stopped":
            return True
    print("  WARN: stop が完了しませんでした。start をスキップします。")
    return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Paperspace Watchdog (DRY_RUN={DRY_RUN}) ===")

    # ── state チェック ───────────────────────────────────────────────────────
    state = notebook_state()
    print(f"Notebook state: {state}")

    # 遷移中は何もしない（二重 stop/start 防止）
    transitional = {"starting", "provisioning", "stopping", "pending", "building"}
    if state.lower() in transitional:
        print(f"→ 遷移中 ({state})。スキップ。")
        sys.exit(0)

    # 停止中 → 再起動
    if state.lower() not in ("running",):
        print(f"→ Notebook が停止中 ({state})。再起動します。")
        if DRY_RUN:
            print("  [DRY_RUN] POST /notebooks/{id}/start をスキップ")
        else:
            paperspace(f"/notebooks/{NOTEBOOK_ID}/start", method="POST")
            print("  start リクエスト送信完了。")
        sys.exit(0)

    # ── running だが URL が古い場合は stop → start ───────────────────────────
    age = read_sd_url_age()
    if age > STALE_THRESHOLD:
        print(f"→ sd_url.json が古すぎます ({age/3600:.2f}h > {STALE_THRESHOLD/3600:.2f}h)。強制再起動します。")
        if DRY_RUN:
            print("  [DRY_RUN] stop/start をスキップ")
            sys.exit(0)

        paperspace(f"/notebooks/{NOTEBOOK_ID}/stop", method="POST")
        print("  stop リクエスト送信完了。")
        if wait_until_stopped():
            paperspace(f"/notebooks/{NOTEBOOK_ID}/start", method="POST")
            print("  start リクエスト送信完了。")
        sys.exit(0)

    print(f"→ Notebook は正常稼働中 (age={age/3600:.2f}h)。何もしません。")


if __name__ == "__main__":
    main()
