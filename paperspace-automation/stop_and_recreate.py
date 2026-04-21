"""
既存 Notebook を停止してから command 付き新 Notebook を作成する。

使い方:
    export PAPERSPACE_API_KEY=<your_key>
    python paperspace-automation/stop_and_recreate.py

注意: 既存 Notebook を停止するため、実行中の WebUI は終了します。
"""
import json
import os
import sys
import time

import requests

KEY = os.environ.get("PAPERSPACE_API_KEY", "")
if not KEY:
    print("ERROR: PAPERSPACE_API_KEY が未設定です")
    sys.exit(1)

H_V1 = {"Authorization": f"Bearer {KEY}"}
H_IO = {"x-api-key": KEY, "Content-Type": "application/json"}

legacy_config = {}
if os.path.exists("paperspace-automation/legacy_notebook_config.json"):
    with open("paperspace-automation/legacy_notebook_config.json", encoding="utf-8") as f:
        legacy_config = json.load(f)

OLD_ID     = legacy_config.get("id", os.environ.get("PAPERSPACE_NOTEBOOK_ID", ""))
PROJECT_ID = legacy_config.get("projectId") or legacy_config.get("projectHandle")
CLUSTER_ID = "clg07azjl"
MACHINE    = legacy_config.get("machineType", "Free-A4000")
COMMAND    = (
    "curl -fsSL https://raw.githubusercontent.com/AIBI0131/Antigravity/master/"
    "paperspace-automation/startup.sh -o /tmp/startup.sh && bash /tmp/startup.sh"
)
CONTAINER  = os.environ.get("PAPERSPACE_CONTAINER", "paperspace/gradient-base:pt211-tf215-jax0414-py311-20231116")

if not OLD_ID or not PROJECT_ID:
    print("ERROR: preflight_check.py を先に実行してください")
    sys.exit(1)

print(f"=== 既存 Notebook 停止 → 新規作成 ===")
print(f"  停止対象 ID : {OLD_ID}")
print(f"  新コンテナ  : {CONTAINER}")
print(f"  command     : {COMMAND}")
print()

# ── Step 1: 既存 Notebook を停止 ──────────────────────────────────────────────
print("1. 既存 Notebook を停止中...")
r = requests.post(
    "https://api.paperspace.io/notebooks/v2/stopNotebook",
    headers=H_IO,
    json={"notebookId": OLD_ID},
    timeout=30,
)
print(f"   stopNotebook → {r.status_code} {r.text[:200]}")
if not r.ok and r.status_code != 409:  # 409=already stopped は無視
    print("   ERROR: 停止に失敗しました")
    sys.exit(1)

# ── Step 2: Stopped になるまで待機 ────────────────────────────────────────────
print("2. 停止完了を待機中（最大5分）...")
stopped = False
for i in range(30):
    time.sleep(10)
    r2 = requests.get("https://api.paperspace.com/v1/notebooks", headers=H_V1, timeout=15)
    if not r2.ok:
        print(f"   [{i+1}/30] API エラー: {r2.status_code}")
        continue
    items = r2.json()
    if isinstance(items, dict):
        items = items.get("items", items.get("notebooks", []))
    for nb in items:
        if nb.get("id") == OLD_ID or nb.get("notebookRepoId") == legacy_config.get("notebookRepoId"):
            state = nb.get("state", "?")
            print(f"   [{i+1}/30] state = {state}")
            if state.lower() in ("stopped", "cancelled", "off"):
                stopped = True
            break
    else:
        # リストから消えた = 停止完了
        print(f"   [{i+1}/30] Notebook がリストから消えました（停止完了）")
        stopped = True
    if stopped:
        print("   ✓ 停止確認")
        break

if not stopped:
    print("   WARN: 停止確認タイムアウト — 15秒追加待機して続行します")
    time.sleep(15)

# ── Step 3: 新 Notebook 作成 ───────────────────────────────────────────────────
print("3. 新 Notebook を作成中...")
payload = {
    "projectHandle": PROJECT_ID,
    "machineType": MACHINE,
    "clusterId": CLUSTER_ID,
    "container": CONTAINER,
    "command": COMMAND,
    "name": "automation-webui",
    "isPreemptible": False,
    "shutdownTimeout": 6,
}
r3 = requests.post(
    "https://api.paperspace.io/notebooks/v2/createNotebook",
    headers=H_IO,
    json=payload,
    timeout=60,
)
print(f"   createNotebook → {r3.status_code} {r3.text[:400]}")

if not r3.ok:
    print("\nERROR: 新 Notebook 作成失敗")
    print("既存 Notebook は停止済みです。Paperspace コンソールから手動で再起動してください。")
    sys.exit(1)

data = r3.json()
new_id = data.get("id", "?")
with open("paperspace-automation/new_notebook.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\n✓ 新 Notebook 作成成功!")
print(f"  新 Notebook ID : {new_id}")
print()
print("次のステップ:")
print(f"  1. GitHub Secrets の PAPERSPACE_NOTEBOOK_ID を {new_id} に更新")
print(f"     GitHub → Settings → Secrets → Actions → PAPERSPACE_NOTEBOOK_ID")
print(f"  2. git push してコード変更を反映")
print(f"  3. Watchdog を手動 Run して動作確認")
