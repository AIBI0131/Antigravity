# Viral Effect Web App — 実装計画 (implementation_plan.md)

> ステータス凡例: `[ ]` 未着手 / `[x]` 完了 / `[-]` スキップ / `[~]` 進行中

**承認日**: 2026-04-13
**技術スタック**: Svelte 5 + TypeScript + Vite / Tailwind CSS + shadcn-svelte / WebGPU+WASM / Cloudflare Pages

---

## フェーズ概要

| Phase | 内容 | 目安工数 |
|-------|------|---------|
| 0 | プロジェクト初期化・環境構築 | 0.5日 |
| 1 | 推論基盤（birefnet-remover参照・書き直し） | 2日 |
| 2 | UIコンポーネント基盤 | 1.5日 |
| 3 | 初期エフェクト実装（3種） | 2日 |
| 4 | URL・シェア・SEO | 0.5日 |
| 5 | テスト | 1日 |
| 6 | トレンド収集パイプライン（並行） | 随時 |
| 7 | リリース準備 | 0.5日 |

**合計目安**: 8〜9日（Phase 6は並行作業）

> ⚠️ **注意**: birefnet-removerは Chrome拡張専用API（`chrome.runtime.*`）を使用しており
> 直接コピーは不可。ロジックを参照しながら `postMessage` / Vite公開パス向けに書き直す。

---

## Phase 0: プロジェクト初期化・環境構築 ✅ 完了 (2026-04-13)

### 0-1. プロジェクト生成

- [x] `npm create svelte@latest .` で Svelte 5 + TypeScript + Vite 初期化
  - SPA mode (no SSR): `adapter-static` を選択
- [x] `package.json` — 依存関係確認・Node バージョン指定
- [x] `svelte.config.js` — `@sveltejs/adapter-static` 設定（Cloudflare Pages向け）
- [x] `vite.config.ts` — Worker inline化・Code splitting設定

### 0-2. スタイリング設定

- [x] `tailwind.config.ts` — Tailwind CSS 初期化、ダークテーマ設定（`darkMode: 'class'`）
- [x] `src/app.css` — グローバルスタイル・ダークテーマ変数定義
- [x] `src/app.html` — `<html class="dark">` 適用、OGP meta タグ枠
- [x] shadcn-svelte 初期化 (`npx shadcn-svelte@latest init`)
  - テーマ: Zinc (ダーク向け)

### 0-3. CI/CD・デプロイ設定

- [x] `.github/workflows/deploy.yml` — GitHub Actions → Cloudflare Pages 自動デプロイ設定
- [-] `wrangler.toml` (任意) — スキップ（CF Pagesはビルド設定のみで動作）
- [x] `.gitignore` — `node_modules/`, `dist/`, `.svelte-kit/` 等

### 0-4. テスト基盤

- [x] `vitest.config.ts` — Vitest 設定（jsdom環境）
- [x] `playwright.config.ts` — Playwright E2E 設定（Chromium + モバイルビューポート）
- [x] `tests/` フォルダ構造作成 (`unit/`, `e2e/`)

### 0-5. PROJECT.md・CLAUDE.md更新

- [x] `viral-effect-app/PROJECT.md` — プロジェクト基本情報作成
- [x] `CLAUDE.md` のプロジェクトレジストリに追記

### 0-6. ビルド検証

- [x] `npm run build` — 153モジュール変換成功、静的サイト生成確認（2026-04-13）
  - ※ Google Drive上でのnpm installはEBADF問題あり。`C:\dev\viral-effect-app`でビルドし結果をコピーするワークフロー採用

> **開発ワークフロー**: ソースはGoogle Drive（git管理）、ビルドは`C:\dev\viral-effect-app`（ローカルコピー）。
> 編集後は`C:\dev\sync_subst.ps1`でローカルに同期してからビルドする。

---

## Phase 1: 推論基盤（birefnet-remover移植） ✅ 完了 (2026-04-13)

> `birefnet-remover/offscreen/` と `birefnet-remover/lib/` を参照しながら移植する。

### 1-1. 型定義

- [x] `src/lib/types.ts`
  ```
  役割: プロジェクト全体で使う型定義
  主要型:
    - Effect: { apply(canvas, params): Promise<void>; getRequiredModels(): ModelSpec[]; requiresWorker(): boolean }
    - EffectMeta: manifest.jsonの1エントリの型
    - EffectParams: Record<string, string|number|boolean>
    - ModelSpec: { id, source, sizeMB }
    - DownloadProgress: { modelId, loaded, total, percent }
    - EffectWeight: 'light' | 'heavy'
    - WorkerMessage:
        | { type: 'LOAD_MODEL'; modelSpec: ModelSpec }
        | { type: 'RUN_INFERENCE'; imageData: ImageBitmap; params: EffectParams }
        | { type: 'CANCEL' }
    - WorkerResponse:
        | { type: 'PROGRESS'; percent: number; label: string }
        | { type: 'RESULT'; imageData: ImageBitmap }
        | { type: 'ERROR'; message: string }
  ```

### 1-2. Web Worker（推論分離）

- [x] `src/lib/worker/inference.worker.ts`
  ```
  役割: ML推論をメインスレッドから分離。UIフリーズを防ぐ。
  処理:
    - self.onmessage で WorkerMessage を受信（chrome.runtime 不使用）
    - WebGPU検出: 'gpu' in navigator → WebGPU / なければ WASM 自動切替
      ※ birefnet-removerと逆：Webアプリは WebGPU を有効化する
    - @huggingface/transformers を使って推論実行
    - 進捗・結果を postMessage(WorkerResponse) で返す
  参照ロジック: birefnet-remover/offscreen/worker.js の
    initializeModel / processImage / processWithTiling / applyMaskToImage
    ※ chrome.runtime.* → postMessage / chrome.runtime.getURL → /onnx/ に書き直し必須
  ```

- [x] `vite.config.ts` への ONNX WASM パス設定
  ```
  役割: onnxruntime-web の .wasm ファイルを public/ 配下に配信
  設定: vite-plugin-static-copy で node_modules/onnxruntime-web/dist/*.wasm
        → public/onnx/ にコピー
  Worker 内: env.backends.onnx.wasm.wasmPaths = '/onnx/'
  参照元: birefnet-remover/vite.config.js の static-copy 設定パターン
  ```

- [x] `src/lib/WorkerBridge.ts`
  ```
  役割: メインスレッドから Worker を操作するラッパー
  機能:
    - loadModel(spec: ModelSpec, onProgress: (p: DownloadProgress) => void): Promise<void>
    - infer(imageData: ImageData, effectId: string, params: EffectParams): Promise<ImageData>
    - terminate(): void
  メッセージ型: WorkerMessage / WorkerResponse (types.ts 参照)
  ```

### 1-3. モデルキャッシュ

- [x] `src/lib/ModelCache.ts`
  ```
  役割: IndexedDB を使いモデルをローカルキャッシュ。再DLを防ぐ。
  機能:
    - get(modelId: string): Promise<ArrayBuffer | null>
    - set(modelId: string, data: ArrayBuffer): Promise<void>
    - has(modelId: string): Promise<boolean>
    - delete(modelId: string): Promise<void>
    - cleanup(maxSizeMB: number): Promise<void>  ← LRU削除
    - getTotalSizeMB(): Promise<number>
  参照元: birefnet-remover内のキャッシュ実装
  ```

### 1-4. エフェクトランナー

- [x] `src/lib/EffectRunner.ts`
  ```
  役割: manifest.json を読み込み、エフェクトを動的インポートして実行する中核。
  機能:
    - loadManifest(): Promise<EffectMeta[]>
    - loadEffect(effectId: string): Promise<Effect>  ← dynamic import
    - runEffect(effectId, canvas, params, onProgress): Promise<void>
    - getEnabledEffects(): Promise<EffectMeta[]>
  分岐: effect.requiresWorker() === false の場合は WorkerBridge を使わず
        直接 Canvas API で処理（lightエフェクトの高速化）
  ```

### 1-5. manifest.json

- [x] `static/effects/manifest.json`
  ```
  役割: 全エフェクトの一覧・メタデータ。このファイルを更新するだけで新エフェクトが公開される。
  スキーマ:
    {
      "version": "1",
      "effects": [{
        "id": string,           // エフェクトID（フォルダ名と一致）
        "name": string,         // 表示名
        "description": string,  // 説明文
        "weight": "light"|"heavy",
        "enabled": boolean,     // falseで非表示（段階リリース制御）
        "requiredModels": [{ "id", "source", "sizeMB" }],
        "params": [{ "id", "type", "default", ...オプション }],
        "previewGif": string,   // プレビューGIFパス
        "addedAt": string       // ISO日付
      }]
    }
  ```

---

## Phase 2: UIコンポーネント基盤 ✅ 完了 (2026-04-13)

### 2-1. ルートレイアウト・状態管理

- [x] `src/routes/+page.svelte` (または `src/App.svelte`)
  ```
  役割: アプリ全体の状態管理・コンポーネントの組み合わせ
  状態:
    - selectedEffect: EffectMeta | null
    - uploadedImage: File | null
    - processedCanvas: HTMLCanvasElement | null
    - appStep: 'gallery' | 'upload' | 'processing' | 'result'
    - downloadProgress: DownloadProgress | null
  フロー: EffectGallery → PhotoEditor → ModelDownloader → 処理 → SharePanel
  ```

- [x] `src/lib/stores.ts`
  ```
  役割: Svelte stores でグローバル状態を管理
  状態:
    - appStep: 'gallery' | 'upload' | 'processing' | 'result' | 'error'
    - error: { code: string; recoverStep: AppStep } | null
      例: モデルDL失敗 → recoverStep: 'processing'（リトライボタン表示）
          入力検証失敗 → recoverStep: 'upload'（再アップロード促す）
  ```

### 2-2. エフェクトギャラリー

- [x] `src/components/EffectGallery.svelte`
  ```
  役割: エフェクト一覧を表示するトップ画面。アプリの「顔」。
  表示:
    - エフェクトカードのグリッド（manifest.jsonから生成）
    - 空状態: 「エフェクトを準備中...」メッセージ
    - light/heavyバッジ（heavyはモデルDL必要の案内付き）
  イベント: onEffectSelect(effectMeta)
  ```

- [x] `src/components/EffectCard.svelte`
  ```
  役割: 1エフェクトのカード表示
  表示:
    - preview.gif をホバーで再生（モバイルはタップで再生）
    - エフェクト名・説明文
    - 重量バッジ（heavy: 「モデルDL必要 〜XXX MB」）
  ```

### 2-3. 写真エディター

- [x] `src/components/PhotoEditor.svelte`
  ```
  役割: 写真アップロード・プレビュー表示・適用ボタン
  機能:
    - ドラッグ&ドロップ + ファイル選択
    - 入力検証: MIME type (image/*), サイズ上限 20MB, 解像度上限 4096px
    - 画像プレビュー表示
    - 「このエフェクトを適用」ボタン
    - 「別のエフェクトに変更」リンク（ギャラリーに戻る）
  エラー状態: 非対応フォーマット / サイズ超過 / 解像度超過
  ```

### 2-4. モデルダウンローダー

- [x] `src/components/ModelDownloader.svelte`
  ```
  役割: モデルDL中の待機UX。最大のフリクション点をポジティブ体験に変える。
  表示:
    - プログレスバー（何MBのうち何MB, ○○%）
    - 説明テキスト（「初回のみ必要です。次回からは即座に適用されます」）
    - 待機中プレビューアニメーション（エフェクトのGIFまたはローディングアニメ）
    - キャンセルボタン
  状態: idle | downloading | cached | error
  ```

### 2-5. シェアパネル

- [x] `src/components/SharePanel.svelte`
  ```
  役割: 処理済み画像の保存・シェア
  機能:
    - 画像プレビュー表示（before/after 比較トグル任意）
    - ダウンロードボタン（Canvas.toBlob → PNG）
    - Web Share API ボタン（非対応環境ではクリップボードコピーにフォールバック）
    - シェアテキスト生成（ハッシュタグ + アプリURL + ?effect=xxx）
    - 「別の写真で試す」「別のエフェクトを試す」ボタン
  ```

### 2-6. 共通UIコンポーネント

- [x] `src/components/ui/ProgressBar.svelte` — プログレスバー（shadcn-svelte拡張）
- [x] `src/components/ui/Badge.svelte` — light/heavyバッジ
- [x] `src/components/ui/ErrorMessage.svelte` — エラー表示（各種エラータイプ対応）

---

## Phase 3: 初期エフェクト実装 ✅ 完了 (2026-04-13)

> 各エフェクトは `src/effects/<id>/index.ts` に実装し、`static/effects/manifest.json` に登録。

### 3-1. エフェクト1: 背景グロー（heavy）

- [x] `src/effects/background-glow/index.ts` (enabled: false — BiRefNet必要)
  ```
  weight: "heavy"
  必要モデル: RMBG-1.4 (BiRefNet系, ~176MB)
  パラメータ: glowColor(color), glowIntensity(range), glowRadius(range)
  ```

- [-] `effects/background-glow/preview.webp` — 後日追加予定

### 3-2. エフェクト2: カラーポップ（light）

- [x] `src/effects/color-pop/index.ts` — Canvas API, HSL色相マスク (enabled: true)
- [-] `effects/color-pop/preview.webp` — 後日追加予定

### 3-3. エフェクト3: ネオン輪郭（light）

- [x] `src/effects/neon-outline/index.ts` — Sobel エッジ + Canvas shadow glow (enabled: true)
- [-] `effects/neon-outline/preview.webp` — 後日追加予定

### 3-4. manifest.json 更新

- [x] `static/effects/manifest.json` — 4エフェクト登録 (color-pop, neon-outline が enabled:true)
- [x] ビルド・型チェック完了、開発サーバー動作確認済み (2026-04-13)

---

## Phase 4: URL・シェア・SEO ✅ 完了 (2026-04-14)

### 4-1. シェアヘルパー

- [x] `src/lib/ShareHelper.ts`
  ```
  役割: シェア機能の統合ラッパー。環境に応じて最適な手段を選択。
  機能:
    - share(blob, text, url): Promise<void>
      → Web Share API が使えれば使用
      → 使えなければ: ダウンロード + URLをクリップボードにコピー
    - buildShareText(effectId): string
      → 「#ViralEffect #<エフェクト名> でつくった / <アプリURL>?effect=<id>」
    - canUseWebShare(): boolean
  ```

### 4-2. URLディープリンク対応

- [ ] `src/routes/+page.svelte` に URLパラメータ処理を追加
  ```
  機能:
    - ?effect=<id> でギャラリーをスキップし直接エフェクト選択状態にする
    - エフェクトID が存在しない場合はギャラリーに戻る
  ```

### 4-3. SEO・OGP

- [ ] `src/app.html` — OGP meta タグ + CSP 設定
  ```
  og:title, og:description, og:image（アプリのOGP画像）
  twitter:card: summary_large_image
  Content-Security-Policy: worker-src 'self' blob:; connect-src 'self' https://huggingface.co https://cdn-lfs.huggingface.co;
  ```
- [ ] `static/og-image.png` — OGP用画像（1200×630px）
- [ ] `static/favicon.svg` / `favicon.ico`

---

## Phase 5: テスト

### 5-1. ユニットテスト

- [ ] `tests/unit/ModelCache.test.ts`
  ```
  テスト内容:
    - キャッシュ書き込み・読み込みの正常系
    - 存在しないキーの読み込み（null返却）
    - LRU削除（容量超過時に古いものから削除）
    - 複数モデルの並行書き込み（競合しない）
  ```

- [ ] `tests/unit/EffectRunner.test.ts`
  ```
  テスト内容:
    - manifest.json の正常読み込み
    - enabled: false のエフェクトが getEnabledEffects から除外される
    - 存在しない effectId でのエラーハンドリング
    - dynamic import のモック
  ```

- [ ] `tests/unit/ShareHelper.test.ts`
  ```
  テスト内容:
    - Web Share API 利用可能時: share() が navigator.share を呼ぶ
    - Web Share API 非対応時: クリップボードフォールバックが動く
    - buildShareText が正しい文字列を返す
  ```

- [ ] `tests/unit/WorkerBridge.test.ts`
  ```
  テスト内容:
    - WebGPU検出ロジック（navigator.gpu あり/なしのモック）
    - WASM fallback への自動切替
    - Worker が応答しない場合のタイムアウト処理
  ```

- [ ] `tests/unit/ModelCache.lru.test.ts`
  ```
  テスト内容:
    - 容量上限（例: 2GB）超過時に最も古いモデルから削除される
    - 複数モデルの並行 set() で競合しない（Promise lock）
    - cleanup() 後に getTotalSizeMB() が上限以下になる
  ```

### 5-2. E2Eテスト

- [ ] `tests/e2e/basic-flow.spec.ts`
  ```
  テストシナリオ:
    1. トップページ表示 → エフェクトギャラリーが見える
    2. lightエフェクト（color-pop）を選択
    3. テスト画像をアップロード
    4. エフェクト適用（モデルDLなし）
    5. ダウンロードボタンで画像が保存される
  ビューポート: デスクトップ + モバイル（375px幅）
  ```

- [ ] `tests/e2e/mobile-touch.spec.ts`
  ```
  テストシナリオ:
    - モバイルビューポートでのファイル選択（iOS Safari想定）
    - EffectCard のタップでプレビューが表示される
  ```

- [ ] `tests/e2e/wasm-fallback.spec.ts`
  ```
  テストシナリオ:
    - --disable-features=WebGPU フラグでChromiumを起動
    - heavyエフェクトが WASM fallback で正常に動作する
    - モデルDL進捗が表示される
  ```

---

## Phase 6: トレンド収集パイプライン（並行作業） ✅ 完了 (2026-04-14)

> Phase 1〜5 の開発と並行して進める。コードは不要な作業も含む。

### 6-1. Notion ダッシュボード整備

- [x] 既存 Notion DB にトレンド候補テーブルを追加
  - DB ID: `34188bba-289a-81fa-b0bd-f572f137c43c`
  - 「AI for GOOD Agent Logger in Notion」ページ配下に作成
  ```
  カラム構成:
    - エフェクト名 (title)
    - 参考URL (url)
    - SNSソース (select: X/Instagram/Reddit/その他)
    - 発見日 (date)
    - 実装難易度 (select: light/heavy/unknown)
    - ステータス (select: 未検討/実装予定/実装済み/見送り)
    - メモ (rich_text)
  ```

### 6-2. 自動収集スクリプト

- [x] `tools/trend-collector/reddit_collector.py`
  ```
  役割: Reddit API からバズ画像投稿を定期収集
  対象subreddit: r/pics, r/interestingasfuck, r/photoshopbattles, r/BeAmazed
  条件: upvote数 1000以上、画像付き投稿
  出力: Notion DB に自動登録（未検討ステータスで）
  ```

- [x] `tools/trend-collector/rss_monitor.py`
  ```
  役割: X/SNSトレンドのRSSフィードを監視
  対象: Twitterトレンド系RSS、画像系ハッシュタグ
  ```

- [x] `tools/trend-collector/requirements.txt`
- [x] `tools/trend-collector/.env.example`

### 6-3. 新エフェクト追加手順書

- [x] `EFFECT_GUIDE.md`
  ```
  内容:
    1. effects/<id>/ フォルダ作成
    2. meta.json の書き方（テンプレート付き）
    3. index.ts の実装方法（lightとheavyそれぞれのテンプレート）
    4. preview.webp の作成方法
    5. manifest.json への追記方法
    6. ローカル動作確認手順
    7. git push → 自動デプロイ確認
  目標: 30分以内で新エフェクトを追加・公開できること
  ```

---

## Phase 7: リリース準備

### 7-1. パフォーマンス確認

- [ ] Lighthouse スコア確認（Performance 80以上目標）
- [ ] モバイル（iPhone/Android）での動作確認
  - [ ] WebGPU非対応端末（iOS Safari）でWASMフォールバック確認
  - [ ] lightエフェクトが即座に動作することを確認
- [ ] 初回 DL 進捗表示が正しく動作することを確認

### 7-2. 最終デプロイ確認

- [ ] Cloudflare Pages 本番環境にデプロイ
- [ ] `<project-name>.pages.dev` でのアクセス確認
- [ ] OGP画像の確認（Xカードバリデーター等）
- [ ] ?effect=xxx ディープリンクの動作確認

### 7-3. ドキュメント

- [ ] `README.md` — プロジェクト概要・ローカル開発手順
- [ ] `EFFECT_GUIDE.md` 最終確認（Phase 6で作成）

---

## ファイル構成 全体マップ

```
viral-effect-app/
├── implementation_plan.md        ← このファイル
├── EFFECT_GUIDE.md               ← Phase 6
├── README.md                     ← Phase 7
│
├── src/
│   ├── app.html                  ← Phase 0, 4
│   ├── app.css                   ← Phase 0
│   ├── routes/
│   │   └── +page.svelte          ← Phase 2-1, 4
│   ├── lib/
│   │   ├── types.ts              ← Phase 1-1
│   │   ├── stores.ts             ← Phase 2-1
│   │   ├── EffectRunner.ts       ← Phase 1-4
│   │   ├── ModelCache.ts         ← Phase 1-3
│   │   ├── WorkerBridge.ts       ← Phase 1-2
│   │   ├── ShareHelper.ts        ← Phase 4-1
│   │   └── worker/
│   │       └── inference.worker.ts ← Phase 1-2
│   └── components/
│       ├── EffectGallery.svelte  ← Phase 2-2
│       ├── EffectCard.svelte     ← Phase 2-2
│       ├── PhotoEditor.svelte    ← Phase 2-3
│       ├── ModelDownloader.svelte ← Phase 2-4
│       ├── SharePanel.svelte     ← Phase 2-5
│       └── ui/
│           ├── ProgressBar.svelte ← Phase 2-6
│           ├── Badge.svelte       ← Phase 2-6
│           └── ErrorMessage.svelte ← Phase 2-6
│
├── effects/
│   ├── manifest.json             ← Phase 1-5
│   ├── background-glow/
│   │   ├── index.ts              ← Phase 3-1
│   │   ├── meta.json             ← Phase 3-1
│   │   └── preview.webp          ← Phase 3-1（初期は静止画、後日GIF化）
│   ├── color-pop/
│   │   ├── index.ts              ← Phase 3-2
│   │   ├── meta.json             ← Phase 3-2
│   │   └── preview.webp          ← Phase 3-2
│   └── neon-outline/
│       ├── index.ts              ← Phase 3-3
│       ├── meta.json             ← Phase 3-3
│       └── preview.webp          ← Phase 3-3
│
├── static/
│   ├── og-image.png              ← Phase 4-3
│   └── favicon.svg               ← Phase 4-3
│
├── tests/
│   ├── unit/
│   │   ├── ModelCache.test.ts      ← Phase 5-1
│   │   ├── ModelCache.lru.test.ts  ← Phase 5-1（LRU削除・並行書き込み）
│   │   ├── EffectRunner.test.ts    ← Phase 5-1
│   │   ├── ShareHelper.test.ts     ← Phase 5-1
│   │   └── WorkerBridge.test.ts    ← Phase 5-1（WebGPU/WASM切替）
│   └── e2e/
│       ├── basic-flow.spec.ts      ← Phase 5-2
│       ├── mobile-touch.spec.ts    ← Phase 5-2
│       └── wasm-fallback.spec.ts   ← Phase 5-2（--disable-features=WebGPU）
│
├── tools/
│   └── trend-collector/          ← Phase 6-2
│       ├── reddit_collector.py
│       ├── rss_monitor.py
│       └── requirements.txt
│
├── .github/
│   └── workflows/
│       └── deploy.yml            ← Phase 0-3
│
├── package.json                  ← Phase 0-1
├── svelte.config.js              ← Phase 0-1
├── vite.config.ts                ← Phase 0-1
├── tailwind.config.ts            ← Phase 0-2
├── vitest.config.ts              ← Phase 0-4
├── playwright.config.ts          ← Phase 0-4
└── tsconfig.json                 ← Phase 0-1
```

---

## 進捗管理メモ

- 各フェーズ完了時に `[x]` に更新する
- 仕様変更があった場合はタスクを追記し `[~]` で進行中を示す
- Phase 6（トレンド収集）は他フェーズと並行して随時更新する
