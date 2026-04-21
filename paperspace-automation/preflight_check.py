"""
Pre-flight Check — 現行 Notebook の詳細取得と /storage/ 共有確認

使い方:
    export PAPERSPACE_API_KEY=<your_key>
    export PAPERSPACE_NOTEBOOK_ID=<repo_id_or_internal_id>   # 例: rdlpoamf83uqqub
    python paperspace-automation/preflight_check.py

出力: paperspace-automation/legacy_notebook_config.json に保存し、
      createNotebook に必要な値を stdout に表示する。
"""
import json
import os
import sys

import requests

KEY = os.environ.get("PAPERSPACE_API_KEY", "")
REPO_ID = os.environ.get("PAPERSPACE_NOTEBOOK_ID", "")

if not KEY or not REPO_ID:
    print("ERROR: PAPERSPACE_API_KEY と PAPERSPACE_NOTEBOOK_ID を環境変数に設定してください")
    sys.exit(1)

HEADERS = {"x-api-key": KEY, "Content-Type": "application/json"}


def find_notebook():
    """notebooks リストから対象を検索して internal id と詳細を返す。"""
    endpoints = [
        ("https://api.paperspace.com/v1/notebooks", {"Authorization": f"Bearer {KEY}"}),
        ("https://api.paperspace.io/notebooks",     {"x-api-key": KEY}),
    ]
    for url, headers in endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            print(f"  GET {url} → {r.status_code}")
            if not r.ok:
                continue
            items = r.json()
            if isinstance(items, dict):
                items = items.get("items", items.get("notebooks", []))
            print(f"  取得件数: {len(items)}")
            for nb in items:
                if nb.get("notebookRepoId") == REPO_ID or nb.get("id") == REPO_ID:
                    return nb
        except Exception as e:
            print(f"  エラー: {e}")

    return None


def get_notebook_detail(nb_id: str):
    """複数エンドポイントで詳細取得を試みる。"""
    attempts = [
        ("GET", f"https://api.paperspace.com/v1/notebooks/{nb_id}",
         {"Authorization": f"Bearer {KEY}"}, {}),
        ("GET", "https://api.paperspace.io/notebooks/v2/getNotebook",
         HEADERS, {"notebookId": nb_id}),
        ("GET", f"https://api.paperspace.io/notebooks/{nb_id}",
         HEADERS, {}),
    ]
    best = None
    for method, url, headers, params in attempts:
        try:
            r = requests.request(method, url, headers=headers, params=params, timeout=30)
            print(f"  {method} {url} → {r.status_code}")
            if r.ok:
                data = r.json()
                # フィールドが多いほど良い
                if best is None or len(data) > len(best):
                    best = data
        except Exception as e:
            print(f"  エラー: {e}")
    return best


def main():
    print("=== Paperspace Pre-flight Check ===\n")

    # Step 1: Notebook 検索
    print("1. Notebook を検索中...")
    nb = find_notebook()
    if not nb:
        print(f"  ERROR: REPO_ID={REPO_ID} の Notebook が見つかりません")
        sys.exit(1)

    internal_id = nb.get("id", REPO_ID)
    print(f"  ✓ 発見: id={internal_id}, state={nb.get('state')}")

    # Step 2: 詳細取得
    print(f"\n2. 詳細取得中 (id={internal_id})...")
    detail = get_notebook_detail(internal_id) or nb

    safe_keys = {
        "id", "name", "state", "machineType", "clusterId", "projectId",
        "notebookRepoId", "container", "containerId", "containerUrl",
        "vmTypeId", "isPreemptible", "shutdownTimeout", "command",
        "startedByUserId", "teamHandle", "projectHandle",
    }
    detail_safe = {k: v for k, v in detail.items() if k in safe_keys}
    print(f"  詳細: {json.dumps(detail_safe, indent=2, ensure_ascii=False)}")

    # 保存
    out_path = "paperspace-automation/legacy_notebook_config.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(detail_safe, f, indent=2, ensure_ascii=False)
    print(f"\n  → {out_path} に保存しました")

    # Step 3: createNotebook に必要な値を表示
    print("\n3. createNotebook に必要な値:")
    print(f"  PAPERSPACE_PROJECT_ID  = {detail_safe.get('projectId') or detail_safe.get('projectHandle', '???')}")
    print(f"  PAPERSPACE_CLUSTER_ID  = {detail_safe.get('clusterId', '???')}")
    print(f"  PAPERSPACE_CONTAINER   = {detail_safe.get('container') or detail_safe.get('containerUrl', '???')}")
    print(f"  PAPERSPACE_MACHINE_TYPE = {detail_safe.get('machineType', 'Free-A4000')}")
    print(f"  command (現在の設定)   = {detail_safe.get('command', '(未設定)')}")

    print("\n=== Pre-flight Check 完了 ===")
    print("次のステップ: paperspace-automation/create_notebook.py を実行して新 Notebook を作成します")


if __name__ == "__main__":
    main()
