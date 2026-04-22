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

# ── 1. .env（Notion トークン等）を /storage/ から読み込み ─────────────────────
if [ -f "$ENV_FILE" ]; then
    NOTION_TOKEN=$(grep -E '^NOTION_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    NOTION_URL_PAGE_ID=$(grep -E '^NOTION_URL_PAGE_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    GRAVITY_SECRET=$(grep -E '^GRAVITY_SECRET=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')
    export GRAVITY_SECRET
    echo "✅ .env 読み込み完了 ($ENV_FILE)"
else
    echo "INFO: $ENV_FILE なし — Notion 通知なしで起動"
    NOTION_TOKEN=""
    NOTION_URL_PAGE_ID=""
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
        httpx matplotlib ipython pyparsing requests notion-client xformers
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

# ── 3. ゾンビ掃除 ─────────────────────────────────────────────────────────────
pkill -f 'launch.py'           2>/dev/null || true
pkill -f 'cloudflared tunnel'  2>/dev/null || true
pkill -f 'auto_gen_worker'     2>/dev/null || true
sleep 2

# ── 4. api_gravity.py 配置（/storage/ にあれば使う） ─────────────────────────
if [ -f "$API_TEMPLATE" ]; then
    mkdir -p "$WEBUI/scripts"
    cp "$API_TEMPLATE" "$WEBUI/scripts/api_gravity.py"
    echo "✅ api_gravity.py 配置"
else
    echo "WARN: $API_TEMPLATE が見つかりません（Gravity API 機能なしで起動）"
fi

# ── 5. cloudflared トンネル起動（http2・リトライ3回・Notion URL 通知） ──
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

# ── 6. WebUI 起動（バックグラウンド・PID を保持） ─────────────────────────────
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

# ── 7. 自動生成ワーカー起動 ──────────────────────────────────────────────────
if [ -f "$WORKER" ]; then
    nohup "$VENV/bin/python" -u "$WORKER" \
        > /notebooks/worker.log 2>&1 &
    echo "worker PID: $!"
else
    echo "WARN: $WORKER が見つかりません"
fi

echo "===== startup.sh done: $(date) ====="
disown -a 2>/dev/null || true
