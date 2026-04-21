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
# PAPERSPACE_NOTEBOOK_ID には Console URL の ID (notebookRepoId) を設定する
# 例: https://console.paperspace.com/xxx/notebook/rdlpoamf83uqqub → rdlpoamf83uqqub
NOTEBOOK_REPO_ID = os.environ["PAPERSPACE_NOTEBOOK_ID"]
NOTEBOOK_ID = NOTEBOOK_REPO_ID  # 初期値（直接アクセス用フォールバック）

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
    if method == "POST" and "json" not in kw and "data" not in kw:
        kw["json"] = {}
    r = requests.request(method, url, headers=headers, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def _discover_endpoint():
    """リストエンドポイントでどの API サーバーが使えるかを発見する。"""
    global _active_endpoint
    last_err = None
    for ep in ENDPOINTS:
        try:
            _try_endpoint(ep, "/notebooks", "GET")
            _active_endpoint = ep
            print(f"  API endpoint: {ep['base']}")
            return
        except Exception as e:
            print(f"  endpoint {ep['base']} 失敗: {e}")
            last_err = e
    raise RuntimeError(f"Paperspace API 疎通不可: {last_err}")


def paperspace(path: str, method: str = "GET", **kw):
    global _active_endpoint
    if not _active_endpoint:
        _discover_endpoint()
    return _try_endpoint(_active_endpoint, path, method, **kw)


def resolve_notebook_id() -> str:
    """notebookRepoId から現在の API id をリスト検索で解決する。"""
    global NOTEBOOK_ID
    data = paperspace("/notebooks")
    items = data if isinstance(data, list) else data.get("items", data.get("notebooks", []))
    safe_keys = {"id", "name", "state", "machineType", "projectId", "notebookRepoId"}
    print("  notebooks:", [{k: v for k, v in nb.items() if k in safe_keys} for nb in items])
    for nb in items:
        if nb.get("notebookRepoId") == NOTEBOOK_REPO_ID or nb.get("id") == NOTEBOOK_REPO_ID:
            NOTEBOOK_ID = nb["id"]
            print(f"  → 解決: repoId={NOTEBOOK_REPO_ID} → id={NOTEBOOK_ID}, state={nb.get('state')}")
            return NOTEBOOK_ID
    raise RuntimeError(f"Notebook が見つかりません (notebookRepoId={NOTEBOOK_REPO_ID})")


def notebook_info() -> dict:
    data = paperspace(f"/notebooks/{NOTEBOOK_ID}")
    safe_keys = {"state", "status", "machineType", "clusterId", "projectId", "name", "id", "notebookRepoId"}
    print("  notebook fields:", {k: v for k, v in data.items() if k in safe_keys})
    return data


def notebook_state() -> str:
    data = notebook_info()
    return data.get("state", data.get("status", "unknown"))


# ---------------------------------------------------------------------------
# Google Drive — sd_url.json の freshness チェック
# ---------------------------------------------------------------------------

def read_sd_url_age() -> float:
    """sd_url.json の timestamp から経過秒数を返す。未設定・取得失敗時は None。"""
    sa_json = os.environ.get("GDRIVE_SA_JSON", "").strip()
    file_id = os.environ.get("GDRIVE_SD_URL_FILE_ID", "").strip()
    if not sa_json or not file_id:
        print("  INFO: GDRIVE 未設定のため sd_url.json チェックをスキップ")
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        creds.refresh(Request())
        drive = build("drive", "v3", credentials=creds)
        content = (
            drive.files()
            .get_media(fileId=file_id)
            .execute()
        )
        data = json.loads(content)
        age = time.time() - data["timestamp"]
        print(f"  sd_url.json age: {age/3600:.2f}h, url: {data.get('url','?')}")
        return age
    except Exception as e:
        print(f"  WARN: sd_url.json 取得失敗: {e}")
        return None


# ---------------------------------------------------------------------------
# Notebook 起動（複数手段を順に試みる）
# ---------------------------------------------------------------------------

MACHINE_TYPE = os.environ.get("PAPERSPACE_MACHINE_TYPE", "Free-A4000")


def _start_notebook():
    errors = []

    # 手段1: 正しい startNotebook エンドポイント（gradient-cli 内部実装と同じ URL）
    # curl で 200 OK 確認済み（2026-04-21）
    try:
        r = requests.post(
            "https://api.paperspace.io/notebooks/v2/startNotebook",
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            json={
                "notebookId": NOTEBOOK_ID,
                "machineType": MACHINE_TYPE,
            },
            timeout=30,
        )
        print(f"  startNotebook response: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        print(f"  ✓ startNotebook 成功 (id={NOTEBOOK_ID}, machineType={MACHINE_TYPE})")
        return
    except Exception as e:
        errors.append(f"startNotebook (internal id): {e}")

    # 手段2: repoId で再試行
    if NOTEBOOK_REPO_ID != NOTEBOOK_ID:
        try:
            r = requests.post(
                "https://api.paperspace.io/notebooks/v2/startNotebook",
                headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                json={"notebookId": NOTEBOOK_REPO_ID, "machineType": MACHINE_TYPE},
                timeout=30,
            )
            print(f"  startNotebook (repoId) response: {r.status_code} {r.text[:200]}")
            r.raise_for_status()
            print(f"  ✓ startNotebook 成功 (repoId={NOTEBOOK_REPO_ID})")
            return
        except Exception as e:
            errors.append(f"startNotebook (repoId): {e}")

    # 手段3: gradient Python SDK v2
    try:
        from gradient.api_sdk.clients.notebook_client import NotebooksClient
        NotebooksClient(api_key=API_KEY).start(id=NOTEBOOK_ID, machine_type=MACHINE_TYPE)
        print(f"  ✓ gradient SDK 成功")
        return
    except Exception as e:
        errors.append(f"gradient SDK: {e}")

    print(f"  WARN: 全手段失敗: {errors}")
    sys.exit(1)


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

    # ── notebook ID を動的解決（repoId → 現在の API id）──────────────────────
    resolve_notebook_id()

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
            print("  [DRY_RUN] start をスキップ")
        else:
            _start_notebook()
        sys.exit(0)

    # ── running だが URL が古い場合は stop → start ───────────────────────────
    age = read_sd_url_age()
    if age is None:
        print("→ Notebook は Running。GDRIVE 未設定のため URL 鮮度チェックをスキップ。正常終了。")
        sys.exit(0)

    if age > STALE_THRESHOLD:
        print(f"→ sd_url.json が古すぎます ({age/3600:.2f}h > {STALE_THRESHOLD/3600:.2f}h)。強制再起動します。")
        if DRY_RUN:
            print("  [DRY_RUN] stop/start をスキップ")
            sys.exit(0)
        try:
            paperspace(f"/notebooks/{NOTEBOOK_ID}/stop", method="POST")
            print("  stop リクエスト送信完了。")
            if wait_until_stopped():
                _start_notebook()
        except Exception as e:
            print(f"  WARN: stop/start 失敗 ({e})。手動での再起動が必要です。")
        sys.exit(0)

    print(f"→ Notebook は正常稼働中 (age={age/3600:.2f}h)。何もしません。")


if __name__ == "__main__":
    main()
