<script lang="ts">
  import type { AppError } from '$lib/types.js';
  import { navigateTo } from '$lib/stores.js';

  interface Props {
    error: AppError;
  }

  let { error }: Props = $props();

  const MESSAGES: Record<string, string> = {
    FILE_TOO_LARGE:        'ファイルサイズが大きすぎます（上限 20MB）',
    UNSUPPORTED_FORMAT:    '対応していない画像形式です（JPEG / PNG / WebP を使用してください）',
    RESOLUTION_EXCEEDED:   '解像度が大きすぎます（上限 4096px）',
    MODEL_DOWNLOAD_FAILED: 'AIモデルのダウンロードに失敗しました',
    INFERENCE_FAILED:      '画像処理に失敗しました',
    CANVAS_FAILED:         '画像の描画に失敗しました',
    SHARE_FAILED:          'シェアに失敗しました',
    WEBGPU_UNAVAILABLE:    'WebGPU が利用できません（WASMで処理します）',
    WORKER_CRASHED:        '処理エンジンがクラッシュしました'
  };

  const friendlyMessage = $derived(MESSAGES[error.code] ?? error.message);
</script>

<div class="rounded-xl border border-red-500/30 bg-red-900/20 p-4 text-sm" role="alert">
  <p class="font-medium text-red-300">⚠ エラーが発生しました</p>
  <p class="mt-1 text-red-200/80">{friendlyMessage}</p>
  <button
    onclick={() => navigateTo(error.recoverStep)}
    class="mt-3 rounded-lg bg-red-800/50 px-3 py-1.5 text-xs font-medium text-red-200 transition hover:bg-red-700/50 active:scale-95"
  >
    {error.recoverStep === 'upload' ? '再アップロード' :
     error.recoverStep === 'processing' ? 'リトライ' : 'やり直す'}
  </button>
</div>
