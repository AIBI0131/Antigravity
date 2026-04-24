#!/bin/bash
# Paperspace Notebook 自動起動スクリプト
# Notebook Command: bash /storage/paperspace-automation/startup.sh
set -uo pipefail
exec > >(tee -a /notebooks/startup.log) 2>&1
exec 200>/tmp/startup.lock
flock -n 200 || { echo "INFO: startup.sh already running (skip)."; exit 0; }
echo "===== startup.sh begin: $(date) ====="

# ── パス定数 ──────────────────────────────────────────────────────────────────
VENV=/notebooks/venv310                           # 各 Notebook 固有（再構築可）
WEBUI=/notebooks/stable-diffusion-webui           # 同上
CF=/notebooks/cloudflared                         # 同上
READY="$VENV/.READY"

STORAGE=/storage/paperspace-automation            # 共有永続領域
ENV_FILE="$STORAGE/.env"
API_TEMPLATE="$STORAGE/api_gravity_template.py"
WORKER="$STORAGE/auto_gen_worker.py"
DONE_FLAG="$STORAGE/DONE"
QUEUE_TXT="$STORAGE/queue.txt"
CHECKPOINT="$STORAGE/checkpoint.json"

# ── 1. .env（Notion トークン等）を /storage/ から読み込み ─────────────────────
if [ -f "$ENV_FILE" ]; then
    NOTION_TOKEN=$(grep -E '^NOTION_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    NOTION_URL_PAGE_ID=$(grep -E '^NOTION_URL_PAGE_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    GRAVITY_SECRET=$(grep -E '^GRAVITY_SECRET=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    GITHUB_TOKEN=$(grep -E '^GITHUB_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    GDRIVE_ROOT_FOLDER_ID=$(grep -E '^GDRIVE_ROOT_FOLDER_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    export GRAVITY_SECRET
    export GDRIVE_ROOT_FOLDER_ID
    echo "✅ .env 読み込み完了 ($ENV_FILE)"
else
    echo "INFO: $ENV_FILE なし — Notion 通知なしで起動"
    NOTION_TOKEN=""
    NOTION_URL_PAGE_ID=""
fi

# ── 1a. GitHub から最新スクリプトを自動更新 ───────────────────────────────────
if [ -n "${GITHUB_TOKEN:-}" ] && [ -z "${_STARTUP_UPDATED:-}" ]; then
    _repo="https://api.github.com/repos/AIBI0131/Antigravity/contents/paperspace-automation"
    _hdr=(-H "Authorization: token ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3.raw")
    for _f in startup.sh auto_gen_worker.py; do
        curl -fsS "${_hdr[@]}" "${_repo}/${_f}?ref=master" -o "$STORAGE/${_f}.new" --max-time 30 \
            && [ -s "$STORAGE/${_f}.new" ] \
            && mv "$STORAGE/${_f}.new" "$STORAGE/${_f}" \
            || rm -f "$STORAGE/${_f}.new"
    done
    # gdrive_uploader は workflow-gravity 配下から取得
    _wg_repo="https://api.github.com/repos/AIBI0131/Antigravity/contents/workflow-gravity"
    curl -fsS "${_hdr[@]}" "${_wg_repo}/gdrive_uploader.py?ref=master" -o "$STORAGE/gdrive_uploader.py.new" --max-time 30 \
        && [ -s "$STORAGE/gdrive_uploader.py.new" ] \
        && mv "$STORAGE/gdrive_uploader.py.new" "$STORAGE/gdrive_uploader.py" \
        || rm -f "$STORAGE/gdrive_uploader.py.new"
    chmod +x "$STORAGE/startup.sh"
    echo "✅ GitHub から最新スクリプト取得完了"
    export _STARTUP_UPDATED=1
    exec 200>&-  # flock 解放（tee プロセス置換への fd 継承を防ぐ）
    exec bash "$0"
fi

# ── 1b. /notebooks/ → /storage/ へのファイル移行（初回のみ）───────────────────
if [ ! -f "$QUEUE_TXT" ] && [ -f "/notebooks/queue.txt" ]; then
    cp /notebooks/queue.txt "$QUEUE_TXT"
    echo "INFO: queue.txt を /storage/ に移行しました"
fi
if [ ! -f "$CHECKPOINT" ] && [ -f "/notebooks/checkpoint.json" ]; then
    cp /notebooks/checkpoint.json "$CHECKPOINT"
    echo "INFO: checkpoint.json を /storage/ に移行しました"
fi

# ── 1c. GitHub から最新 queue.txt を取得 ──────────────────────────────────────
if [ -n "${GITHUB_TOKEN:-}" ]; then
    echo "GitHub から queue.txt を取得中..."
    _http=$(curl -sS -o "$QUEUE_TXT.tmp" -w '%{http_code}' \
        -H "Authorization: token ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3.raw" \
        "https://api.github.com/repos/AIBI0131/Antigravity/contents/paperspace-automation/queue.txt?ref=master" \
        --max-time 30)
    if [ "$_http" = "200" ] && [ -s "$QUEUE_TXT.tmp" ]; then
        mv "$QUEUE_TXT.tmp" "$QUEUE_TXT"
        echo "✅ queue.txt 取得成功"
    else
        rm -f "$QUEUE_TXT.tmp"
        echo "WARN: queue.txt 取得失敗 (HTTP $_http) — 既存ファイルを使用"
    fi
else
    echo "INFO: GITHUB_TOKEN 未設定 — queue.txt のGitHub同期スキップ"
fi

# ── helper: queue 完了判定 ────────────────────────────────────────────────────
queue_is_done() {
    [ ! -f "$DONE_FLAG" ] && return 1
    [ ! -f "$QUEUE_TXT" ] && return 1
    local saved current
    saved=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['queue_hash'])" "$DONE_FLAG" 2>/dev/null)
    current=$(md5sum "$QUEUE_TXT" | cut -d' ' -f1)
    [ "$saved" = "$current" ]
}

# ── helper: 自分の Notebook ID を API から自動取得 ────────────────────────────
_resolve_my_notebook_id() {
    local api_key="$1"
    local repo_id="${PAPERSPACE_NOTEBOOK_REPO_ID:-}"
    if [ -z "$repo_id" ]; then
        echo "[auto-stop] WARN: PAPERSPACE_NOTEBOOK_REPO_ID 未設定 — ID 自動取得スキップ" >&2
        return 1
    fi
    _STOP_API_KEY="$api_key" _STOP_REPO_ID="$repo_id" python3 -c "
import json, os, sys, urllib.request
api_key = os.environ['_STOP_API_KEY']
repo_id = os.environ['_STOP_REPO_ID']
try:
    req = urllib.request.Request(
        'https://api.paperspace.com/v1/notebooks',
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    raw = json.load(urllib.request.urlopen(req, timeout=15))
    data = raw.get('items', raw) if isinstance(raw, dict) else raw
    for n in data:
        if n.get('notebookRepoId') == repo_id:
            print(n['id']); sys.exit(0)
    print('no match for repo_id=' + repo_id, file=sys.stderr)
except Exception as e:
    print('API error: ' + str(e), file=sys.stderr)
" 2>/dev/null
}

# ── helper: Notebook 自動停止 ─────────────────────────────────────────────────
stop_notebook() {
    local api_key notebook_id
    api_key=$(grep -E '^PAPERSPACE_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
    notebook_id=$(_resolve_my_notebook_id "$api_key")
    if [ -z "$notebook_id" ]; then
        notebook_id=$(grep -E '^PAPERSPACE_NOTEBOOK_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r')
        echo "[auto-stop] API 自動取得失敗 — .env フォールバック: $notebook_id"
    fi

    cp /notebooks/startup.log "$STORAGE/last_startup.log" 2>/dev/null || true
    cp /notebooks/worker.log "$STORAGE/last_worker.log" 2>/dev/null || true
    echo "[auto-stop] ログを /storage/ に退避完了"

    echo "[auto-stop] プロセス掃除中..."
    pkill -f 'launch.py'       2>/dev/null || true
    pkill -f 'auto_gen_worker' 2>/dev/null || true
    pkill -f 'cloudflared'     2>/dev/null || true
    sleep 3

    if [ -n "$api_key" ] && [ -n "$notebook_id" ]; then
        echo "[auto-stop] 全件完了 — Notebook を自動停止します (id=$notebook_id) ($(date))"
        local result
        result=$(curl -sS -X POST \
            "https://api.paperspace.io/notebooks/v2/stopNotebook" \
            -H "x-api-key: ${api_key}" \
            -H "Content-Type: application/json" \
            -d "{\"notebookId\": \"${notebook_id}\"}" \
            --max-time 30)
        echo "[auto-stop] 結果: $result"
    else
        echo "[auto-stop] WARN: PAPERSPACE_API_KEY 未設定 — 自動停止できません"
    fi
}

# ── early check: 全件完了済みなら即停止（GPU 節約）─────────────────────────
if queue_is_done; then
    echo "INFO: queue 全件完了済み (DONE フラグ有効) — Notebook を即停止して GPU 解放 ($(date))"
    stop_notebook
    exit 0
fi

# ── 2. セットアップ（.READY なければ初回構築・冪等） ─────────────────────────
if [ ! -f "$READY" ]; then
    echo "=== Setup: venv 構築 (初回 10〜15分) ==="
    # python3.10 を優先、なければ python3.11 を使う
    PY=""
    for candidate in python3.10 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            PY="$candidate"
            break
        fi
    done
    if [ -z "$PY" ]; then
        echo "INFO: python3.10 を apt でインストールします..."
        apt-get update -qq
        apt-get install -y -qq python3.10 python3.10-venv libpython3.10-dev build-essential ffmpeg
        PY=python3.10
    else
        apt-get update -qq
        apt-get install -y -qq build-essential ffmpeg || true
    fi
    echo "使用 Python: $($PY --version)"
    "$PY" -m venv --without-pip "$VENV" \
        || "$PY" -m venv "$VENV" \
        || { echo "FATAL: venv 作成失敗"; exit 1; }
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python"
    "$VENV/bin/pip" install -U pip setuptools wheel
    "$VENV/bin/pip" install \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu121
    "$VENV/bin/pip" install \
        httpx matplotlib ipython pyparsing requests notion-client xformers \
        google-api-python-client google-auth
    "$VENV/bin/pip" install --no-build-isolation \
        "https://github.com/openai/CLIP/archive/d50d76daa670286dd6cacf3bcd80b5e4823fc8e1.zip"
    echo "built_at=$(date +%s)" > "$READY"
    echo "✅ venv 構築完了"
fi

if [ ! -d "$WEBUI" ]; then
    git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui "$WEBUI"
else
    echo "✅ WebUI 既存（スキップ）"
fi

if [ ! -f "$CF" ]; then
    wget -q \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -O "$CF"
    chmod +x "$CF"
else
    echo "✅ cloudflared 既存（スキップ）"
fi

# ── 3. サービス状態確認（個別チェック） ──────────────────────────────────────
WEBUI_UP=false
CF_UP=false
pgrep -f 'launch.py'           > /dev/null 2>&1 && WEBUI_UP=true
pgrep -f 'cloudflared tunnel'  > /dev/null 2>&1 && CF_UP=true

if $WEBUI_UP && $CF_UP; then
    echo "INFO: WebUI + cloudflared 両方起動中 — startup をスキップ"
    exit 0
fi

# WebUI が死んでいる場合のみゾンビ掃除（CF も巻き込んで再起動）
if ! $WEBUI_UP; then
    pkill -f 'launch.py'           2>/dev/null || true
    pkill -f 'cloudflared tunnel'  2>/dev/null || true
    pkill -f 'auto_gen_worker'     2>/dev/null || true
    sleep 2
    CF_UP=false
fi

# ── 4. api_gravity.py 配置（/storage/ にあれば使う） ─────────────────────────
if [ -f "$API_TEMPLATE" ]; then
    mkdir -p "$WEBUI/scripts"
    cp "$API_TEMPLATE" "$WEBUI/scripts/api_gravity.py"
    echo "✅ api_gravity.py 配置"
else
    echo "WARN: $API_TEMPLATE が見つかりません（Gravity API 機能なしで起動）"
fi

# ── 5. cloudflared トンネル起動（http2・リトライ3回・Notion URL 通知） ──
if ! $CF_UP; then
cat > /tmp/cf_start.sh << 'CFEOF'
#!/bin/bash
CF_BIN="$1"
NOTION_TOKEN="$2"
NOTION_URL_PAGE_ID="$3"
VENV_PY="$4"

for attempt in 1 2 3; do
    echo "[cf] 試行 $attempt/3 ($(date))"
    > /notebooks/cf.log
    "$CF_BIN" tunnel --url http://127.0.0.1:7860 --protocol http2 \
        >> /notebooks/cf.log 2>&1 &
    CF_PID=$!

    URL=""
    for i in $(seq 1 60); do
        sleep 1
        URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /notebooks/cf.log | tail -1)
        [ -n "$URL" ] && break
    done

    if [ -n "$URL" ]; then
        TS=$(date +%s)
        printf '{"url":"%s","timestamp":%d}\n' "$URL" "$TS" > /notebooks/sd_url.json
        echo "[cf] ✅ URL 取得: $URL"

        if [ -n "$NOTION_TOKEN" ] && [ -n "$NOTION_URL_PAGE_ID" ]; then
            "$VENV_PY" - "$URL" "$NOTION_TOKEN" "$NOTION_URL_PAGE_ID" << 'PYEOF' \
                && echo "[cf] ✅ Notion URL 更新済" || echo "[cf] WARN: Notion 更新失敗"
import sys, json
from urllib.request import Request, urlopen
url, token, page_id = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.dumps({"properties":{"title":{"title":[{"text":{"content":url}}]}}}).encode()
req = Request(f"https://api.notion.com/v1/pages/{page_id}", data=data, method="PATCH",
              headers={"Authorization":f"Bearer {token}","Notion-Version":"2022-06-28","Content-Type":"application/json"})
urlopen(req)
PYEOF
        fi

        wait $CF_PID
        exit 0
    fi

    echo "[cf] URL 取得失敗 → 再試行"
    kill $CF_PID 2>/dev/null; wait $CF_PID 2>/dev/null
    sleep 5
done
echo "[cf] ERROR: 3回試行しても URL 取得できず"
CFEOF
chmod +x /tmp/cf_start.sh
nohup /tmp/cf_start.sh "$CF" "${NOTION_TOKEN:-}" "${NOTION_URL_PAGE_ID:-}" "$VENV/bin/python" \
    200>&- >> /notebooks/startup.log 2>&1 &
fi  # CF_UP

# ── 6. WebUI 起動（バックグラウンド・PID を保持） ─────────────────────────────
if ! $WEBUI_UP; then
cd "$WEBUI"
export GRADIO_SERVER_TIMEOUT=300
export GRADIO_KEEP_ALIVE=True
export PYTHON="$VENV/bin/python"

nohup "$VENV/bin/python" launch.py \
    --xformers \
    --enable-insecure-extension-access \
    --api \
    --gradio-queue \
    200>&- > /notebooks/webui.log 2>&1 &
WEBUI_PID=$!
echo "WebUI PID: $WEBUI_PID"

# ── 7. 自動生成ワーカー起動 ──────────────────────────────────────────────────
if queue_is_done; then
    echo "INFO: queue 全件完了済み (DONE フラグ有効) — worker 起動スキップ、Notebook を停止します"
    stop_notebook
elif [ -f "$WORKER" ]; then
    rm -f "$DONE_FLAG"
    nohup "$VENV/bin/python" -u "$WORKER" \
        200>&- > /notebooks/worker.log 2>&1 &
    echo "worker PID: $!"
else
    echo "WARN: $WORKER が見つかりません"
fi

# ── 7a. queue.txt バックグラウンド同期（GitHub → /storage/） ─────────────────
if [ -n "${GITHUB_TOKEN:-}" ]; then
(
  exec 200>&-
  _qurl="https://api.github.com/repos/AIBI0131/Antigravity/contents/paperspace-automation/queue.txt?ref=master"
  _qhdr=(-H "Authorization: token ${GITHUB_TOKEN}" -H "Accept: application/vnd.github.v3.raw")
  while true; do
    sleep 60
    curl -fsS "${_qhdr[@]}" "$_qurl" -o "$QUEUE_TXT.sync" --max-time 30 \
        && [ -s "$QUEUE_TXT.sync" ] \
        && ! cmp -s "$QUEUE_TXT.sync" "$QUEUE_TXT" \
        && mv "$QUEUE_TXT.sync" "$QUEUE_TXT" \
        && echo "[queue-sync] ✅ queue.txt 更新検出・反映 ($(date))" \
        || rm -f "$QUEUE_TXT.sync"
  done
) >> /notebooks/startup.log 2>&1 &
echo "queue-sync PID: $!"
fi

fi  # WEBUI_UP

# ── 8. ワーカー + WebUI 死活監視ループ ────────────────────────────��──────────
(
  exec 200>&-  # flock fd を閉じる（self-update の exec bash "$0" がロックを取得できるようにする）
  sleep 300  # 起動完了を待つ
  MAX_RESTART=5
  restart_count=0
  while true; do
    if ! pgrep -f 'auto_gen_worker' > /dev/null 2>&1; then
      if queue_is_done; then
        echo "[monitor] worker 完了 (DONE フラグ有効) — Notebook を停止します ($(date))"
        stop_notebook
        break
      elif [ -f "$WORKER" ]; then
        restart_count=$((restart_count + 1))
        if [ "$restart_count" -gt "$MAX_RESTART" ]; then
          echo "[monitor] worker が $MAX_RESTART 回再起動失敗 — 監視終了 ($(date))"
          break
        fi
        echo "[monitor] worker dead — 再起動 ($restart_count/$MAX_RESTART) ($(date))"
        nohup "$VENV/bin/python" -u "$WORKER" 200>&- >> /notebooks/worker.log 2>&1 &
      fi
    else
      restart_count=0
    fi
    if ! pgrep -f 'launch.py' > /dev/null 2>&1; then
      echo "[monitor] WebUI dead — startup.sh を再実行 ($(date))"
      bash "$0"
      break
    fi
    sleep 120
  done
) >> /notebooks/startup.log 2>&1 &
echo "monitor PID: $!"

echo "===== startup.sh done: $(date) ====="
disown -a 2>/dev/null || true
