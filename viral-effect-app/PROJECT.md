# Viral Effect App

## 基本情報
- **パス**: viral-effect-app/
- **状態**: 開発中
- **技術スタック**: Svelte 5 / SvelteKit 2 / TypeScript / Vite / Tailwind CSS v4 / shadcn-svelte / WebGPU+WASM / Cloudflare Pages
- **起動コマンド**:
  - `npm install && npm run dev` — 開発サーバー起動
  - `npm run build` — 本番ビルド
  - `npm run test` — Vitestユニットテスト
  - `npm run test:e2e` — Playwright E2Eテスト

## 概要
XなどSNSでバズった画像加工エフェクトを素早く特定・実装し、ユーザーが自分の写真にブラウザ完結で適用・シェアできるWebアプリ。

## アーキテクチャ
- **エフェクトプラグイン型**: `effects/manifest.json` + 動的インポートで新エフェクトを追加可能
- **ブラウザ完結**: WebGPU/WASM推論、IndexedDBモデルキャッシュ
- **推論分離**: Web Worker (inference.worker.ts) でMLをメインスレッドから分離
- **軽量/重量分類**: weight:light（Canvas/WebGL系）は即座に適用、weight:heavy（ML推論）はモデルDL必要

## ディレクトリ
- `src/lib/` — コアロジック（EffectRunner, ModelCache, WorkerBridge, ShareHelper）
- `src/components/` — Svelteコンポーネント
- `effects/` — エフェクトプラグイン（各エフェクトがフォルダ単位）
- `tools/trend-collector/` — トレンド候補自動収集スクリプト

## 新エフェクト追加方法
`EFFECT_GUIDE.md` 参照（Phase 6で作成予定）

## デプロイ
- GitHub mainブランチpush → GitHub Actions → Cloudflare Pages 自動デプロイ
- URL: `viral-effect-app.pages.dev`（CF Pagesプロジェクト作成後に確定）
- 必要なシークレット: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`

## 関連プロジェクト
- `birefnet-remover` — Worker/ModelCacheのロジック参照元
