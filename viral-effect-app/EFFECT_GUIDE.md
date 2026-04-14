# 新エフェクト追加ガイド

このガイドに従えば **30 分以内**で新しいエフェクトを追加・本番公開できます。

---

## 目次

1. [フォルダ作成](#1-フォルダ作成)
2. [meta.json の書き方](#2-metajson-の書き方)
3. [index.ts の実装](#3-indexts-の実装)
4. [preview.webp の作成](#4-previewwebp-の作成)
5. [manifest.json への追記](#5-manifestjson-への追記)
6. [ローカル動作確認](#6-ローカル動作確認)
7. [git push → 自動デプロイ](#7-git-push--自動デプロイ)

---

## 1. フォルダ作成

```
static/effects/<effect-id>/
  meta.json          ← エフェクトのメタ情報
  preview.webp       ← ギャラリーに表示するサムネイル (必須)
  index.ts           ← エフェクト処理コード (src/lib/effects/ に置く)
```

`<effect-id>` はケバブケース (`my-cool-effect` など)。

```bash
mkdir static/effects/my-cool-effect
touch static/effects/my-cool-effect/meta.json
touch static/effects/my-cool-effect/preview.webp
touch src/lib/effects/my-cool-effect/index.ts
```

---

## 2. meta.json の書き方

```json
{
  "id": "my-cool-effect",
  "name": "かっこいいエフェクト",
  "description": "一行で説明するキャッチコピー（40 文字以内推奨）",
  "weight": "light",
  "enabled": false,
  "requiredModels": [],
  "params": [],
  "previewImage": "/effects/my-cool-effect/preview.webp",
  "addedAt": "2026-04-14"
}
```

### フィールド説明

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | string | フォルダ名と一致させる |
| `name` | string | ギャラリーに表示する名前 |
| `description` | string | エフェクトの説明文 |
| `weight` | `"light"` \| `"heavy"` | Canvas のみ → light、ONNX モデル使用 → heavy |
| `enabled` | boolean | **最初は `false`** でリリース前テスト。公開時に `true` |
| `requiredModels` | array | light の場合は `[]`。heavy の場合は下記参照 |
| `params` | array | 将来の拡張用。現時点では `[]` のまま |
| `previewImage` | string | `/effects/<id>/preview.webp` |
| `addedAt` | string | 追加日 (YYYY-MM-DD) |

### heavy エフェクトの requiredModels

```json
"requiredModels": [
  {
    "id": "rmbg-1.4",
    "source": "RMBG-1.4",
    "sizeMB": 176
  }
]
```

---

## 3. index.ts の実装

### light エフェクト (Canvas のみ)

`src/lib/effects/my-cool-effect/index.ts` を作成:

```typescript
import type { EffectModule } from '$lib/types.js';

const effect: EffectModule = {
  async run(canvas: OffscreenCanvas, _params: Record<string, unknown>): Promise<void> {
    const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    if (!ctx) throw new Error('Canvas context unavailable');

    // ── ここにエフェクト処理を書く ───────────────────────────────────
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;

    for (let i = 0; i < data.length; i += 4) {
      // 例: グレースケール変換
      const avg = (data[i] + data[i + 1] + data[i + 2]) / 3;
      data[i]     = avg;   // R
      data[i + 1] = avg;   // G
      data[i + 2] = avg;   // B
      // data[i + 3] は alpha (変更しない)
    }

    ctx.putImageData(imageData, 0, 0);
    // ─────────────────────────────────────────────────────────────────
  },
};

export default effect;
```

### heavy エフェクト (ONNX モデル使用)

ONNX モデルを使う場合は `WorkerBridge` 経由で推論を行います:

```typescript
import type { EffectModule } from '$lib/types.js';
import { WorkerBridge } from '$lib/WorkerBridge.js';

// モデル仕様 (manifest.json の requiredModels と一致させる)
const MODEL_SPEC = {
  id: 'my-model',
  source: 'my-org/my-model',
  sizeMB: 100,
};

const effect: EffectModule = {
  async run(
    canvas: OffscreenCanvas,
    _params: Record<string, unknown>,
    onProgress?: (p: import('$lib/types.js').ProgressState) => void,
  ): Promise<void> {
    const bridge = new WorkerBridge();
    try {
      // モデルをダウンロード & キャッシュ
      await bridge.loadModel(MODEL_SPEC, 'my-cool-effect', onProgress);

      // 推論
      const bitmap = await createImageBitmap(canvas);
      const result = await bridge.runInference('my-cool-effect', bitmap);
      bitmap.close();

      // 結果を canvas に描画
      const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
      const resultBitmap = await createImageBitmap(result);
      ctx.drawImage(resultBitmap, 0, 0, canvas.width, canvas.height);
      resultBitmap.close();
    } finally {
      bridge.dispose();
    }
  },
};

export default effect;
```

---

## 4. preview.webp の作成

### 仕様

- サイズ: **640 × 480 px** 以上 (表示は 16:9 にトリミング)
- 形式: WebP または PNG (WebP 推奨)
- 容量: **100 KB 以下** を目標

### 作成手順

1. 適当な写真にエフェクトを手動適用して画面キャプチャ
2. [Squoosh](https://squoosh.app/) で WebP に変換・圧縮
3. `static/effects/<id>/preview.webp` として保存

### 代替: placeholder 使用

開発中は `static/placeholder-preview.webp` をコピーして代替可:

```bash
cp static/placeholder-preview.webp static/effects/my-cool-effect/preview.webp
```

---

## 5. manifest.json への追記

`static/effects/manifest.json` の `effects` 配列に追記:

```json
{
  "version": "1",
  "effects": [
    ...既存エフェクト...,
    {
      "id": "my-cool-effect",
      "name": "かっこいいエフェクト",
      "description": "一行で説明するキャッチコピー",
      "weight": "light",
      "enabled": false,
      "requiredModels": [],
      "params": [],
      "previewImage": "/effects/my-cool-effect/preview.webp",
      "addedAt": "2026-04-14"
    }
  ]
}
```

### EffectRunner への登録

`src/lib/EffectRunner.ts` の `EFFECT_MODULES` グロブが自動的に `src/lib/effects/*/index.ts` を取得するため、**ファイルを配置するだけで登録完了**です。手動変更不要。

---

## 6. ローカル動作確認

```bash
# 1. ソースを C:\dev にコピー (Windowsの場合)
powershell -Command "Copy-Item -Path 'i:\マイドライブ\Antigravity\viral-effect-app\*' -Destination 'C:\dev\viral-effect-app' -Recurse -Force"

# 2. dev サーバー起動
cd C:\dev\viral-effect-app
npm run dev

# 3. ブラウザで確認
# http://localhost:5173/
# → ギャラリーに新しいエフェクトカードが表示される (enabled: false のままでも開発確認可)

# 4. enabled: true にしてギャラリーに表示
# static/effects/manifest.json の enabled を true に変更

# 5. E2E テスト
npm run test:e2e

# 6. ユニットテスト
npm run test
```

### 動作確認チェックリスト

- [ ] ギャラリーにエフェクトカードが表示される
- [ ] カードをクリックするとアップロード画面に遷移する
- [ ] 写真をアップロードして「このエフェクトを適用」ボタンが有効になる
- [ ] 適用ボタンを押すと処理が走り「完成！」が表示される
- [ ] 結果画像が正しく加工されている
- [ ] 保存ボタンで画像をダウンロードできる
- [ ] シェアボタンで Web Share API または クリップボードコピーが動作する
- [ ] ?effect=\<id\> ディープリンクで直接アップロード画面に遷移できる

---

## 7. git push → 自動デプロイ

### ソースを Google Drive にコピー (Windows)

```powershell
# C:\dev → Google Drive に逆コピー (src のみ)
Copy-Item "C:\dev\viral-effect-app\src" "i:\マイドライブ\Antigravity\viral-effect-app\" -Recurse -Force
Copy-Item "C:\dev\viral-effect-app\static" "i:\マイドライブ\Antigravity\viral-effect-app\" -Recurse -Force
```

### git push

```bash
cd "i:/マイドライブ/Antigravity/viral-effect-app"
git add static/effects/my-cool-effect/ src/lib/effects/my-cool-effect/
git commit -m "feat(effects): add my-cool-effect"
git push origin main
```

### 自動デプロイの確認

`.github/workflows/deploy.yml` により GitHub Actions が起動し、Cloudflare Pages に自動デプロイされます。

1. GitHub Actions の進捗: `https://github.com/<owner>/<repo>/actions`
2. デプロイ後: `https://<project>.pages.dev/?effect=my-cool-effect` で確認

---

## よくある失敗パターン

| 症状 | 原因 | 対処 |
|---|---|---|
| ギャラリーにカードが表示されない | `manifest.json` の `enabled: false` | `true` に変更して保存 |
| エフェクト適用後に真っ暗 | `canvas.getContext('2d')` が null | OffscreenCanvas の取得確認 |
| preview 画像が表示されない | WebP が壊れているか PATH 誤り | ファイルと PATH を確認 |
| heavy エフェクトでモデルが落ちてこない | `requiredModels.source` が間違い | Hugging Face モデル ID を確認 |
| ディープリンクが gallery に戻る | `manifest.json` の id と URL パラメータが不一致 | 完全一致させる |
