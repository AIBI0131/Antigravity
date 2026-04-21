"""
Stop existing Notebook, then create a new one with a startup command.

Usage:
    $env:PAPERSPACE_API_KEY="<your_key>"
    python paperspace-automation/stop_and_recreate.py
"""
import json
import os
import sys
import time

import requests

KEY = os.environ.get("PAPERSPACE_API_KEY", "")
if not KEY:
    print("ERROR: PAPERSPACE_API_KEY not set")
    sys.exit(1)

H_V1 = {"Authorization": f"Bearer {KEY}"}
H_IO = {"x-api-key": KEY, "Content-Type": "application/json"}

legacy_config = {}
cfg_path = "paperspace-automation/legacy_notebook_config.json"
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        legacy_config = json.load(f)

OLD_ID     = legacy_config.get("id", os.environ.get("PAPERSPACE_NOTEBOOK_ID", "ncn5vjxzti"))
PROJECT_ID = "p6rny5vxgj7"  # Uncategorized Notebooks (previous creation succeeded here)
CLUSTER_ID = "clg07azjl"
MACHINE    = legacy_config.get("machineType", "Free-A4000")
COMMAND    = (
    "curl -fsSL https://raw.githubusercontent.com/AIBI0131/Antigravity/master/"
    "paperspace-automation/startup.sh -o /tmp/startup.sh && bash /tmp/startup.sh"
)
CONTAINER  = "paperspace/gradient-base:pt211-tf215-jax0414-py311-20231116"

if not OLD_ID or not PROJECT_ID:
    print("ERROR: run preflight_check.py first")
    sys.exit(1)

print("=== Stop existing Notebook -> Create new one ===")
print(f"  OLD_ID     : {OLD_ID}")
print(f"  PROJECT_ID : {PROJECT_ID}")
print(f"  container  : {CONTAINER}")
print(f"  command    : {COMMAND}")
print()


def _strip_nulls(s):
    """Remove null bytes (U+0000) from a string."""
    return s.replace("\x00", "") if isinstance(s, str) else s


def _safe_json(payload: dict) -> bytes:
    """Serialize to JSON bytes, removing any U+0000 that json.dumps escapes as \\u0000."""
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    raw = raw.replace("\\u0000", "")
    return raw.encode("ascii")


# -- Step 1: Stop existing Notebook ------------------------------------------
print("1. Stopping existing Notebook...")
r = requests.post(
    "https://api.paperspace.io/notebooks/v2/stopNotebook",
    headers=H_IO,
    data=_safe_json({"notebookId": _strip_nulls(OLD_ID)}),
    timeout=30,
)
print(f"   stopNotebook -> {r.status_code} {r.text[:200]}")
if not r.ok and r.status_code != 409:
    print("   ERROR: stop failed")
    sys.exit(1)

# -- Step 2: Wait for Cancelled state ----------------------------------------
print("2. Waiting for Cancelled state (up to 5 min)...")
stopped = False
for i in range(30):
    time.sleep(10)
    r2 = requests.get("https://api.paperspace.com/v1/notebooks", headers=H_V1, timeout=15)
    if not r2.ok:
        print(f"   [{i+1}/30] API error: {r2.status_code}")
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
        print(f"   [{i+1}/30] Notebook gone from list (stopped)")
        stopped = True
    if stopped:
        print("   OK: stopped confirmed")
        break

if not stopped:
    print("   WARN: timeout -- waiting 15s more and continuing")
    time.sleep(15)

# -- Step 3: Create new Notebook with command --------------------------------
print("3. Creating new Notebook (with machine fallback)...")
MACHINE_ORDER = [MACHINE] + [m for m in ["Free-RTX5000", "Free-A4000", "Free-P5000", "Free-RTX4000"] if m != MACHINE]

data = None
for machine in MACHINE_ORDER:
    payload = {
        "projectHandle": _strip_nulls(PROJECT_ID),
        "machineType": machine,
        "clusterId": CLUSTER_ID,
        "container": CONTAINER,
        "name": "automation-webui",
        "isPreemptible": False,
        "shutdownTimeout": 6,
        "environment": {"JUPYTER_CONFIG_PATH": "/notebooks"},
    }
    r3 = requests.post(
        "https://api.paperspace.io/notebooks/v2/createNotebook",
        headers=H_IO,
        data=_safe_json(payload),
        timeout=60,
    )
    print(f"   [{machine}] -> {r3.status_code} {r3.text[:300]}")
    if r3.ok:
        data = r3.json()
        print(f"   OK: created on {machine}")
        break
    if r3.status_code not in (429, 500):
        break

if not data:
    print("\nERROR: failed to create Notebook (all machines 429 or other error)")
    print("Retry: python paperspace-automation/stop_and_recreate.py")
    sys.exit(1)

new_id = data.get("id", "?")
with open("paperspace-automation/new_notebook.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\nOK: New Notebook created!")
print(f"  New Notebook ID : {new_id}")
print()
print("Next steps:")
print(f"  1. Update GitHub Secret PAPERSPACE_NOTEBOOK_ID -> {new_id}")
print(f"     GitHub -> Settings -> Secrets -> Actions -> PAPERSPACE_NOTEBOOK_ID")
print(f"  2. git push")
print(f"  3. Run Watchdog manually to verify")
