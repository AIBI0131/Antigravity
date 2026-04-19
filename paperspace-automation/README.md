# Paperspace 無料GPU 自動運転セットアップ

## アーキテクチャ

```
GitHub Actions (10分毎)
  → Paperspace API で Notebook 停止を検知
  → POST /notebooks/{id}/start
      → startup.sh 自動実行
          → WebUI :7860 起動
          → cloudflared トンネル → URL を Google Drive (sd_url.json) に保存
          → auto_gen_worker.py 起動
              → Notion DB の pending レコードを拾う
              → /sdapi/v1/txt2img に投げる
              → 結果 PNG を Google Drive に保存
              → Notion レコードを done/failed に更新
```

---

## 事前 Spike（着手前に必ず確認）

### Spike #1: Paperspace API 確認

Paperspace の API Key を取得したら以下を実行:

```bash
export KEY="<あなたの API Key>"
export NB="<Notebook ID>"   # URL の /notebook/<ここ> の部分

# v1 系 (新 DO 系)
curl -sS -H "Authorization: Bearer $KEY" \
  "https://api.paperspace.com/v1/notebooks/$NB" | python3 -m json.tool | head -20

# v1 系がダメなら旧系
curl -sS -H "x-api-key: $KEY" \
  "https://api.paperspace.io/notebooks/getNotebook?notebookId=$NB" | python3 -m json.tool | head -20
```

動作確認できた方の URL を `paperspace_watchdog.py` の `ENDPOINTS` 優先順位に合わせる。

### Spike #2: Paperspace Notebook Startup Command 確認

Paperspace UI → 対象の Notebook を選択 → **Edit** または **Settings** を開く

- **「Command」「Startup command」「Container command」欄が存在する** → Phase 2b 実行可
- 存在しない場合は Issues に報告してください (fallback 対応が必要)

---

## 初回セットアップ

### 1. GitHub Secrets 登録

リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Secret 名 | 内容 |
|---|---|
| `PAPERSPACE_API_KEY` | Paperspace コンソール → Account → API Keys |
| `PAPERSPACE_NOTEBOOK_ID` | Notebook URL の ID 部分 (例: `rdlpoamf83uqqub`) |
| `GDRIVE_SA_JSON` | Google Cloud Service Account の JSON 全文 (Drive read 権限) |
| `GDRIVE_SD_URL_FILE_ID` | Drive 上の `sd_url.json` の fileId (`/d/<ID>/view` の `<ID>`) |

DRY_RUN 変数 (Optional):
- リポジトリ → Variables → New variable: `WATCHDOG_DRY_RUN` = `1` → 読み取り専用モード

### 2. Google Service Account 作成 (GDRIVE_SA_JSON)

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. APIs → Google Drive API を有効化
3. IAM → Service Accounts → アカウント作成 (Drive read 権限)
4. Keys → JSON をダウンロード → 内容を `GDRIVE_SA_JSON` Secret に貼る
5. Drive 上の `sd_url.json` を右クリック → 共有 → このメールを追加 (閲覧者)

### 3. Notion Integration & DB 作成

1. [notion.so/my-integrations](https://www.notion.so/my-integrations) → New integration → Internal
2. Capabilities: Read, Update, Insert content を有効にする
3. Token をコピー (`secret_xxxxxxxx`)
4. 任意のページに Database を作成し、以下のカラムを追加:

| カラム名 | 型 |
|---|---|
| Name | title |
| Status | select (pending / running / done / failed) |
| Prompt | rich_text |
| Negative | rich_text |
| Steps | number |
| CFG | number |
| Width | number |
| Height | number |
| Sampler | select |
| Seed | number |
| Result URL | url |
| Error | rich_text |

5. DB のページ右上 → Share → Integration を招待

### 4. `paperspace.env` を Google Drive に配置

Drive で `Antigravity/paperspace.env` というファイルを作成し、以下を記述:

```
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_QUEUE_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

DB ID は Notion DB ページの URL の `/.../<DB_ID>?v=...` の部分。

### 5. Paperspace VM への初回ファイル配置 (Phase 2b 以降)

Notebook を起動してターミナルで実行:

```bash
# rclone.conf を永続ストレージに配置 (既に Phase 1 で設定済みなら不要)
ls /notebooks/rclone.conf   # 存在確認

# startup.sh と関連ファイルを配置
# 方法A: リポジトリを clone してコピー
git clone https://github.com/<user>/Antigravity /tmp/ag
cp /tmp/ag/paperspace-automation/startup.sh          /notebooks/startup.sh
cp /tmp/ag/paperspace-automation/auto_gen_worker.py  /notebooks/auto_gen_worker.py
cp /tmp/ag/paperspace-automation/api_gravity_template.py /notebooks/api_gravity_template.py
chmod +x /notebooks/startup.sh

# 方法B: GitHub Raw URL から wget
BASE="https://raw.githubusercontent.com/<user>/Antigravity/master/paperspace-automation"
wget -q "$BASE/startup.sh"          -O /notebooks/startup.sh
wget -q "$BASE/auto_gen_worker.py"  -O /notebooks/auto_gen_worker.py
wget -q "$BASE/api_gravity_template.py" -O /notebooks/api_gravity_template.py
chmod +x /notebooks/startup.sh
```

### 6. Notebook Command の設定

Paperspace UI → Notebook → Edit / Settings → Command:

```
bash /notebooks/startup.sh
```

---

## Phase 別動作確認

### Phase 2a: Watchdog のみ

```bash
# ローカル dry-run (Notebook を実際には触らない)
export PAPERSPACE_API_KEY="..."
export PAPERSPACE_NOTEBOOK_ID="..."
export GDRIVE_SA_JSON="$(cat /path/to/sa.json)"
export GDRIVE_SD_URL_FILE_ID="..."
export DRY_RUN=1
python .github/scripts/paperspace_watchdog.py

# GitHub Actions で手動実行
gh workflow run paperspace-watchdog.yml
gh run watch
```

### Phase 2b: startup.sh

```bash
# Paperspace ターミナルで
tail -f /notebooks/startup.log
# → "startup.sh done" が 3分以内に出ること

cat /storage/sd_url.json
# → 新しい timestamp の URL が入っていること
```

### Phase 2c: Notion ワーカー

```bash
# Notion DB に1件 pending レコードを追加
# → 30秒以内に running、3〜5分で done に変わること

tail -f /notebooks/worker.log
```

---

## 運用メモ

- **モデル追加**: Notebook を手動起動 → `webui2 (4).ipynb` のセル③を実行
- **Watchdog 一時停止**: GitHub → Actions → Disable workflow
- **ログ確認**: `/notebooks/startup.log`, `/notebooks/webui.log`, `/notebooks/worker.log`, `/notebooks/cf.log`
- **rclone 再認証**: `rclone config reconnect gdrive:`

---

## トラブルシューティング

| 症状 | 確認先 | 対処 |
|---|---|---|
| Watchdog が毎回 restart する | `sd_url.json` のタイムスタンプ | cloudflared or WebUI の起動を確認 |
| startup.sh が exit 1 で止まる | `/notebooks/startup.log` | `rclone.conf` の存在確認 |
| ワーカーが動かない | `/notebooks/worker.log` | `paperspace.env` が Drive に存在するか確認 |
| Notion 更新が止まる | worker.log の 429 ログ | 自動 backoff で回復待ち |
| Drive 同期が失敗 | `rclone config reconnect gdrive:` | rclone の認証更新 |
