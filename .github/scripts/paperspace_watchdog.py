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
    data = paperspace("/notebooks")
    items = data if isinstance(data, list) else data.get("items", data.get("notebooks", []))
    for nb in items:
        if nb.get("id") == NOTEBOOK_ID:
            safe_keys = {"state", "status", "machineType", "clusterId", "fqdn", "projectId", "name", "id", "notebookRepoId"}
            print("  notebook fields:", {k: v for k, v in nb.items() if k in safe_keys})
            return nb
    raise RuntimeError(f"Notebook {NOTEBOOK_ID} がリストに見つかりません")


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
# 空きがなければ順番に試すフォールバック機種リスト
MACHINE_FALLBACKS = ["Free-RTX5000", "Free-A4000", "Free-P5000", "Free-RTX4000"]


def _start_notebook() -> dict:
    """Start notebook and return the API response dict (contains handle, token)."""
    errors = []
    machines_to_try = [MACHINE_TYPE] + [m for m in MACHINE_FALLBACKS if m != MACHINE_TYPE]

    for machine in machines_to_try:
        try:
            r = requests.post(
                "https://api.paperspace.io/notebooks/v2/startNotebook",
                headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                json={"notebookId": NOTEBOOK_ID, "machineType": machine, "shutdownTimeout": 6},
                timeout=30,
            )
            safe_resp = r.text[:200].replace(r.json().get("token","NOMATCH"), "***") if r.ok else r.text[:200]
            print(f"  startNotebook ({machine}) response: {r.status_code} {safe_resp}")
            if r.status_code == 429:
                print(f"  {machine} は空きなし → 次の機種を試みます")
                errors.append(f"{machine}: 429 空きなし")
                continue
            r.raise_for_status()
            print(f"  ✓ startNotebook 成功 (id={NOTEBOOK_ID}, machineType={machine})")
            return r.json()
        except Exception as e:
            errors.append(f"{machine}: {e}")

    # repoId で最終試行
    if NOTEBOOK_REPO_ID != NOTEBOOK_ID:
        try:
            r = requests.post(
                "https://api.paperspace.io/notebooks/v2/startNotebook",
                headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                json={"notebookId": NOTEBOOK_REPO_ID, "machineType": MACHINE_TYPE, "shutdownTimeout": 6},
                timeout=30,
            )
            safe_resp = r.text[:200].replace(r.json().get("token","NOMATCH"), "***") if r.ok else r.text[:200]
            print(f"  startNotebook (repoId) response: {r.status_code} {safe_resp}")
            if r.status_code != 429:
                r.raise_for_status()
                print(f"  ✓ startNotebook 成功 (repoId={NOTEBOOK_REPO_ID})")
                return r.json()
        except Exception as e:
            errors.append(f"repoId: {e}")

    # 全機種 429 = 一時的な空きなし → 次回 cron に委ねる（exit 0）
    if all("429" in str(e) or "空きなし" in str(e) for e in errors):
        print(f"  INFO: 全機種空きなし。次回の cron で再試行します。")
        sys.exit(0)

    print(f"  WARN: 全手段失敗: {errors}")
    sys.exit(1)


def _wait_for_running(max_wait: int = 300) -> str:
    """Poll until Running. Returns fqdn on success, empty string on failure."""
    print("  Running 状態を待機中...")
    for _ in range(max_wait // 15):
        time.sleep(15)
        data = paperspace("/notebooks")
        items = data if isinstance(data, list) else data.get("items", data.get("notebooks", []))
        nb = next((n for n in items if n.get("notebookRepoId") == NOTEBOOK_REPO_ID), None)
        if not nb:
            print("  (リストに未登場、待機継続)")
            continue
        st = nb.get("state", "unknown")
        fqdn = nb.get("fqdn", "")
        print(f"  state = {st}, fqdn = {fqdn or '(未取得)'}")
        if st.lower() == "running":
            return fqdn
        if st.lower() in ("error", "stopped", "cancelled"):
            print(f"  WARN: 予期しない状態 ({st})。startup トリガーをスキップ。")
            return ""
    print("  WARN: Running にならなかった。startup トリガーをスキップ。")
    return ""


def _trigger_startup(handle: str, token: str, fqdn: str):
    """Jupyter WebSocket 経由で startup.sh をバックグラウンド実行する。"""
    import uuid
    import websocket

    jupyter_url = f"https://{fqdn}" if fqdn else f"https://{handle}.clg07azjl.paperspacegradient.com"
    headers = {"Authorization": f"Token {token}"}
    print(f"  Jupyter URL: {jupyter_url}")

    # Jupyter が応答するまで最大 3 分待機
    for i in range(18):
        try:
            r = requests.get(f"{jupyter_url}/api/kernels", headers=headers, timeout=10)
            if r.ok:
                print(f"  Jupyter 応答確認 (試行 {i+1})")
                break
        except Exception:
            pass
        time.sleep(10)
    else:
        print("  WARN: Jupyter が応答しない。startup トリガーをスキップ。")
        return

    # セッション（Python カーネル）を作成
    r = requests.post(
        f"{jupyter_url}/api/sessions",
        headers=headers,
        json={"kernel": {"name": "python3"}, "name": "watchdog", "path": "watchdog.ipynb", "type": "notebook"},
        timeout=30,
    )
    if not r.ok:
        print(f"  WARN: セッション作成失敗: {r.text[:200]}")
        return
    session = r.json()
    kernel_id = session["kernel"]["id"]

    # WebSocket でコードを実行（startup.sh をバックグラウンド起動）
    ws_url = jupyter_url.replace("https", "wss") + f"/api/kernels/{kernel_id}/channels?token={token}"
    code = (
        "import subprocess; "
        "r=subprocess.run(['pgrep','-f','/storage/paperspace-automation/startup.sh'],capture_output=True); "
        "subprocess.Popen(['bash','/storage/paperspace-automation/startup.sh'],"
        "start_new_session=True,"
        "stdout=open('/tmp/startup_out.log','a'),stderr=subprocess.STDOUT) "
        "if r.returncode != 0 else print('startup.sh already running, skip')"
    )
    msg = {
        "header": {"msg_id": uuid.uuid4().hex, "username": "watchdog",
                   "session": uuid.uuid4().hex, "msg_type": "execute_request", "version": "5.2"},
        "parent_header": {},
        "metadata": {},
        "content": {"code": code, "silent": True, "store_history": False,
                    "user_expressions": {}, "allow_stdin": False},
    }
    try:
        ws = websocket.create_connection(ws_url, timeout=30)
        ws.send(json.dumps(msg))
        time.sleep(3)
        ws.close()
        print("  ✓ startup.sh 実行リクエスト送信完了")
    except Exception as e:
        print(f"  WARN: WebSocket 接続失敗: {e}")

    # セッション後片付け
    requests.delete(f"{jupyter_url}/api/sessions/{session['id']}", headers=headers, timeout=10)


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
            sys.exit(0)
        nb_data = _start_notebook()
        handle = nb_data.get("handle", "")
        token = nb_data.get("token", "")
        if handle and token:
            fqdn = _wait_for_running()
            if fqdn:
                _trigger_startup(handle, token, fqdn)
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
                nb_data = _start_notebook()
                handle = nb_data.get("handle", "")
                token = nb_data.get("token", "")
                if handle and token:
                    fqdn = _wait_for_running()
                    if fqdn:
                        _trigger_startup(handle, token, fqdn)
        except Exception as e:
            print(f"  WARN: stop/start 失敗 ({e})。手動での再起動が必要です。")
        sys.exit(0)

    print(f"→ Notebook は正常稼働中 (age={age/3600:.2f}h)。何もしません。")


if __name__ == "__main__":
    main()
