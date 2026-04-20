#!/bin/bash
# Paperspace Notebook 自動起動スクリプト
# Notebook Settings → Command: bash /notebooks/startup.sh
set -uo pipefail
exec > >(tee -a /notebooks/startup.log) 2>&1
echo "===== startup.sh begin: $(date) ====="

VENV=/notebooks/venv310
WEBUI=/notebooks/stable-diffusion-webui
CF=/notebooks/cloudflared
READY="$VENV/.READY"

# ── 1. rclone.conf チェック（任意・なくても動作する） ────────────────────────
export RCLONE_CONFIG=/notebooks/rclone.conf
RCLONE_AVAILABLE=false
if [ -f "$RCLONE_CONFIG" ] && command -v rclone &>/dev/null; then
    RCLONE_AVAILABLE=true
    echo "✅ rclone 利用可能"
else
    echo "INFO: rclone 未設定 — Drive 同期をスキップ（WebUI は起動します）"
fi

# ── 2. .env（Notion トークン等）を Drive から取得して読み込み ─────────────────
if [ "$RCLONE_AVAILABLE" = true ]; then
    rclone copyto gdrive:Antigravity/paperspace.env /notebooks/.env 2>/dev/null || true
fi
if [ -f /notebooks/.env ]; then
    NOTION_TOKEN=$(grep -E '^NOTION_TOKEN=' /notebooks/.env | cut -d= -f2- | tr -d '\r')
    NOTION_URL_PAGE_ID=$(grep -E '^NOTION_URL_PAGE_ID=' /notebooks/.env | cut -d= -f2- | tr -d '\r')
    GRAVITY_SECRET=$(grep -E '^GRAVITY_SECRET=' /notebooks/.env | cut -d= -f2- | tr -d '\r')
    export GRAVITY_SECRET
    echo "✅ .env 読み込み完了"
else
    echo "INFO: .env なし"
fi

# ── 3. セットアップ（.READY なければ初回構築・冪等） ─────────────────────────
if [ ! -f "$READY" ]; then
    echo "=== Setup: venv 構築 (初回 10〜15分) ==="
    apt-get update -qq
    apt-get install -y -qq python3.10 python3.10-venv python3-pip libpython3.10-dev build-essential ffmpeg
    python3.10 -m venv --without-pip "$VENV" \
        || python3.10 -m venv "$VENV" \
        || { echo "FATAL: venv 作成失敗"; exit 1; }
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python3.10"
    "$VENV/bin/pip" install -U pip setuptools==69.5.1 wheel
    "$VENV/bin/pip" install \
        torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 \
        xformers==0.0.23.post1 \
        --index-url https://download.pytorch.org/whl/cu121
    "$VENV/bin/pip" install \
        httpx==0.24.1 matplotlib ipython pyparsing requests notion-client
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

# ── 4. ゾンビ掃除 ─────────────────────────────────────────────────────────────
pkill -f 'launch.py'           2>/dev/null || true
pkill -f 'cloudflared tunnel'  2>/dev/null || true
pkill -f 'auto_gen_worker'     2>/dev/null || true
sleep 2

# ── 5. api_gravity.py 配置 ────────────────────────────────────────────────────
TEMPLATE=/notebooks/api_gravity_template.py
if [ -f "$TEMPLATE" ]; then
    mkdir -p "$WEBUI/scripts"
    cp "$TEMPLATE" "$WEBUI/scripts/api_gravity.py"
    echo "✅ api_gravity.py 配置"
else
    echo "WARN: $TEMPLATE が見つかりません（Gravity API 機能なしで起動）"
fi

# ── 6. cloudflared トンネル起動（http2・リトライ3回・Notion URL 通知） ──
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
    >> /notebooks/startup.log 2>&1 &

# ── 7. WebUI 起動（バックグラウンド・PID を保持） ─────────────────────────────
cd "$WEBUI"
export GRADIO_SERVER_TIMEOUT=300
export GRADIO_KEEP_ALIVE=True
export PYTHON="$VENV/bin/python"

nohup "$VENV/bin/python" launch.py \
    --xformers \
    --enable-insecure-extension-access \
    --api \
    --gradio-queue \
    > /notebooks/webui.log 2>&1 &
WEBUI_PID=$!
echo "WebUI PID: $WEBUI_PID"

# ── 8. 自動生成ワーカー起動 ──────────────────────────────────────────────────
if [ -f /notebooks/auto_gen_worker.py ]; then
    nohup "$VENV/bin/python" -u /notebooks/auto_gen_worker.py \
        > /notebooks/worker.log 2>&1 &
    echo "worker PID: $!"
else
    echo "WARN: /notebooks/auto_gen_worker.py が見つかりません"
fi

echo "===== startup.sh done: $(date) ====="

# WebUI が生きている限りコンテナを保持
trap 'pkill -P $$ 2>/dev/null || true' EXIT
wait "$WEBUI_PID"
