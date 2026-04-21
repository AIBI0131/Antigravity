"""利用可能な Paperspace コンテナ一覧を取得する。"""
import json, os, requests

KEY = os.environ["PAPERSPACE_API_KEY"]
CLUSTER_ID = "clg07azjl"
H_V1 = {"Authorization": f"Bearer {KEY}"}
H_IO = {"x-api-key": KEY}

endpoints = [
    ("https://api.paperspace.com/v1/containers",                          H_V1, {}),
    ("https://api.paperspace.com/v1/notebooks/templates",                 H_V1, {}),
    ("https://api.paperspace.com/v1/clusters/{c}/notebookTemplates".format(c=CLUSTER_ID), H_V1, {}),
    ("https://api.paperspace.io/notebooks/getTemplates",                  H_IO, {}),
    ("https://api.paperspace.io/notebooks/getTemplates",                  H_IO, {"clusterId": CLUSTER_ID}),
    ("https://api.paperspace.io/notebooks/templates",                     H_IO, {}),
    ("https://api.paperspace.io/clusters/{c}/templates".format(c=CLUSTER_ID), H_IO, {}),
]

for url, headers, params in endpoints:
    r = requests.get(url, headers=headers, params=params, timeout=15)
    print(f"{r.status_code} {url}")
    if r.ok:
        data = r.json()
        print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
        print()
