# バズエフェクト

SNS でバズった画像エフェクトをブラウザだけで写真に適用・シェアできる Web アプリ。

**サーバーレス / プライバシー完全保護** — 画像はデバイスから一切外に出ません。

## 機能

- **カラーポップ** — 指定した色だけ残してあとは白黒に
- **ネオン輪郭** — エッジを光るネオンカラーで際立たせる
- WebGPU 非対応ブラウザでも WASM フォールバックで動作
- Web Share API / クリップボード / DL のフォールバックチェーン
- `?effect=<id>` ディープリンクで直接適用画面へ

## 技術スタック

| 役割 | 技術 |
|---|---|
| フレームワーク | Svelte 5 + SvelteKit (adapter-static) |
| スタイリング | Tailwind CSS v4 + shadcn-svelte |
| ML 推論 | @huggingface/transformers (WebGPU → WASM) |
| Worker | Web Worker + OffscreenCanvas |
| ホスティング | Cloudflare Pages |

## ローカル開発

**注意**: ソースは Google Drive 上 (`i:\マイドライブ\Antigravity\viral-effect-app`) で管理。
`node_modules` の関係でビルドは `C:\dev\viral-effect-app` で行います。

```powershell
# 1. ソースをローカルにコピー
Copy-Item "i:\マイドライブ\Antigravity\viral-effect-app\*" "C:\dev\viral-effect-app" -Recurse -Force

# 2. 依存インストール（初回のみ）
cd C:\dev\viral-effect-app
npm install

# 3. 開発サーバー起動
npm run dev
# → http://localhost:5173
```

## テスト

```bash
# ユニットテスト
npm run test

# E2E テスト (Chromium + mobile + WebGPU無効)
npm run test:e2e

# 特定スイートのみ
npx playwright test tests/e2e/basic-flow.spec.ts --project=chromium
```

## ビルド & デプロイ

```bash
npm run build        # static/ に出力
npm run preview      # ビルド確認

git push origin main # → Cloudflare Pages 自動デプロイ
```

## 新エフェクト追加

`EFFECT_GUIDE.md` を参照。30 分以内で追加・公開できます。

## トレンド収集

`tools/trend-collector/` 配下のスクリプトで Reddit / RSS から新エフェクトの着想を自動収集し Notion DB に登録します。

```bash
cd tools/trend-collector
pip install -r requirements.txt
cp .env.example .env  # トークンを設定

python reddit_collector.py --dry-run
python rss_monitor.py --dry-run
```

## ライセンス

MIT
