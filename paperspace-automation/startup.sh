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
    set -a
    # shellcheck source=/dev/null
    source /notebooks/.env
    set +a
    echo "✅ .env 読み込み完了"
else
    echo "INFO: .env なし（Notion ワーカーは起動しません）"
fi

# ── 3. セットアップ（.READY なければ初回構築・冪等） ─────────────────────────
if [ ! -f "$READY" ]; then
    echo "=== Setup: venv 構築 (初回 10〜15分) ==="
    apt-get update -qq
    apt-get install -y -qq python3.10 python3.10-venv libpython3.10-dev build-essential ffmpeg
    python3.10 -m venv "$VENV" || { echo "FATAL: venv 作成失敗"; exit 1; }
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

# ── 6. cloudflared トンネル＋URL 同期（バックグラウンド） ────────────────────
CF_BIN="$CF"
nohup bash -c "
    \"$CF_BIN\" tunnel --url http://127.0.0.1:7860 2>&1 | tee /notebooks/cf.log |
    while IFS= read -r line; do
        if echo \"\$line\" | grep -oE 'https://[a-z0-9-]+\\.trycloudflare\\.com' > /tmp/cf_url; then
            URL=\$(cat /tmp/cf_url)
            TS=\$(date +%s)
            echo \"{\\\"url\\\":\\\"\$URL\\\",\\\"timestamp\\\":\$TS,\\\"source\\\":\\\"paperspace\\\"}\" \
                > /storage/sd_url.json
            if [ \"$RCLONE_AVAILABLE\" = true ]; then
                rclone copyto /storage/sd_url.json gdrive:Antigravity/sd_url.json \
                    && echo \"✅ sd_url.json → Drive 同期: \$URL\" \
                    || echo \"WARN: rclone 同期失敗\"
            fi
            break
        fi
    done
" &

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

# ── 8. Notion ワーカー起動（.env が読み込まれていれば） ─────────────────────
if [ -n "${NOTION_TOKEN:-}" ] && [ -f /notebooks/auto_gen_worker.py ]; then
    nohup "$VENV/bin/python" /notebooks/auto_gen_worker.py \
        > /notebooks/worker.log 2>&1 &
    echo "Notion worker PID: $!"
else
    echo "INFO: NOTION_TOKEN 未設定 or auto_gen_worker.py 未配置。ワーカー起動をスキップ。"
fi

echo "===== startup.sh done: $(date) ====="

# WebUI が生きている限りコンテナを保持
trap 'pkill -P $$ 2>/dev/null || true' EXIT
wait "$WEBUI_PID"
