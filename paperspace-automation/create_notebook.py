"""
Paperspace Notebook を command 付きで API 作成する。
container 名が不明な場合は候補を順番に試す。

使い方:
    export PAPERSPACE_API_KEY=<your_key>
    python paperspace-automation/create_notebook.py
"""
import json
import os
import sys

import requests

KEY = os.environ.get("PAPERSPACE_API_KEY", "")
if not KEY:
    print("ERROR: PAPERSPACE_API_KEY が未設定です")
    sys.exit(1)

legacy_config = {}
legacy_path = "paperspace-automation/legacy_notebook_config.json"
if os.path.exists(legacy_path):
    with open(legacy_path, encoding="utf-8") as f:
        legacy_config = json.load(f)

PROJECT_ID = os.environ.get("PAPERSPACE_PROJECT_ID") or legacy_config.get("projectId") or legacy_config.get("projectHandle")
CLUSTER_ID = os.environ.get("PAPERSPACE_CLUSTER_ID", "clg07azjl")
MACHINE    = os.environ.get("PAPERSPACE_MACHINE_TYPE", legacy_config.get("machineType", "Free-A4000"))
COMMAND    = (
    "curl -fsSL https://raw.githubusercontent.com/AIBI0131/Antigravity/master/"
    "paperspace-automation/startup.sh -o /tmp/startup.sh && bash /tmp/startup.sh"
)

if not PROJECT_ID:
    print("ERROR: PROJECT_ID が不明です。先に preflight_check.py を実行してください。")
    sys.exit(1)

# 環境情報 (Ubuntu 22.04, Python 3.11.7, PyTorch 2.1.1+cu121) に基づく候補
# 発見次第 break する
CONTAINER_CANDIDATES = [
    "paperspace/gradient-base:pt211-tf215-jax0414-py311-20231116",
    "paperspace/gradient-base:pt211-tf215-jax0414-py311-20240104",
    "paperspace/gradient-base:pt21-tf215-jax0414-py311-20231116",
    "paperspace/gradient-base:pt21-tf29-jax0414-py311-20230201",
    "nvcr.io/nvidia/pytorch:23.11-py3",
    "nvcr.io/nvidia/pytorch:23.12-py3",
    "paperspace/gradient-base:pt112-tf29-jax0414-py39-20220905",
]

OVERRIDE = os.environ.get("PAPERSPACE_CONTAINER", "")
if OVERRIDE:
    CONTAINER_CANDIDATES = [OVERRIDE] + CONTAINER_CANDIDATES


def try_create(container: str) -> dict | None:
    payload = {
        "projectHandle": PROJECT_ID,
        "machineType": MACHINE,
        "clusterId": CLUSTER_ID,
        "container": container,
        "command": COMMAND,
        "name": "automation-webui",
        "isPreemptible": False,
        "shutdownTimeout": 6,
    }
    r = requests.post(
        "https://api.paperspace.io/notebooks/v2/createNotebook",
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    print(f"  [{r.status_code}] container={container}")
    if r.ok:
        return r.json()
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    msg = body.get("error", {}).get("message", r.text[:200]) if isinstance(body, dict) else r.text[:200]
    print(f"    → {msg}")
    return None


print("=== Paperspace Notebook 作成 ===")
print(f"  projectHandle : {PROJECT_ID}")
print(f"  clusterId     : {CLUSTER_ID}")
print(f"  machineType   : {MACHINE}")
print(f"  command       : {COMMAND}")
print()

data = None
for container in CONTAINER_CANDIDATES:
    data = try_create(container)
    if data:
        print(f"\n✓ 成功: container={container}")
        break

if not data:
    print("\nERROR: すべてのコンテナ候補で失敗しました。")
    print("Paperspace コンソールで使用可能なコンテナ名を確認し、")
    print("  PAPERSPACE_CONTAINER=<name> python create_notebook.py")
    print("で再実行してください。")
    sys.exit(1)

new_id = data.get("id", data.get("notebookId", "?"))
with open("paperspace-automation/new_notebook.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"  新 Notebook ID : {new_id}")
print()
print("次のステップ:")
print(f"  1. Paperspace コンソールで新 Notebook が見えることを確認")
print(f"  2. GitHub Secrets の PAPERSPACE_NOTEBOOK_ID を {new_id} に更新")
print(f"     GitHub → Settings → Secrets → Actions → PAPERSPACE_NOTEBOOK_ID")
