<script lang="ts">
  import { get } from 'svelte/store';
  import { onMount, onDestroy } from 'svelte';
  import EffectGallery from '$components/EffectGallery.svelte';
  import PhotoEditor from '$components/PhotoEditor.svelte';
  import ModelDownloader from '$components/ModelDownloader.svelte';
  import SharePanel from '$components/SharePanel.svelte';
  import ErrorMessage from '$components/ui/ErrorMessage.svelte';
  import {
    appStep,
    appError,
    selectedEffect,
    uploadedFile,
    downloadProgress,
    resultDataUrl,
    navigateTo,
    setError,
    resetToGallery
  } from '$lib/stores.js';
  import { EffectRunner } from '$lib/EffectRunner.js';
  import { ERROR_CODES } from '$lib/types.js';
  import type { EffectMeta } from '$lib/types.js';

  let runner: EffectRunner | null = null;

  function getRunner(): EffectRunner {
    if (!runner) runner = new EffectRunner();
    return runner;
  }

  // Revoke Blob URLs when replaced or on component destroy to prevent memory leaks
  let _prevResultUrl: string | null = null;
  const _unsubResultUrl = resultDataUrl.subscribe((url: string | null) => {
    if (_prevResultUrl && _prevResultUrl !== url) {
      URL.revokeObjectURL(_prevResultUrl);
    }
    _prevResultUrl = url;
  });
  onDestroy(() => {
    _unsubResultUrl();
    if (_prevResultUrl) URL.revokeObjectURL(_prevResultUrl);
    runner?.dispose();
  });

  // ── Deep link: ?effect=<id> → skip gallery ────────────────────────────────

  onMount(async () => {
    const effectId = new URLSearchParams(location.search).get('effect');
    if (!effectId) return;

    try {
      const effects = await getRunner().getEnabledEffects();
      const found = effects.find((e) => e.id === effectId);
      if (found) {
        selectedEffect.set(found);
        navigateTo('upload');
      }
    } catch {
      // Invalid / unknown effectId — fall through to gallery
    }
  });

  // ── Step: Gallery → Upload ─────────────────────────────────────────────────

  function onEffectSelect(effect: EffectMeta) {
    selectedEffect.set(effect);
    // Reflect the selected effect in the URL so back/forward and share work
    history.replaceState(null, '', `?effect=${effect.id}`);
    navigateTo('upload');
  }

  // ── Step: Upload → Processing ──────────────────────────────────────────────

  async function onApply(file: File) {
    uploadedFile.set(file);
    navigateTo('processing');

    const effect = get(selectedEffect);
    if (!effect) {
      setError(ERROR_CODES.E6, 'エフェクトが選択されていません', 'gallery');
      return;
    }

    // Create an OffscreenCanvas from the uploaded file
    let canvas: OffscreenCanvas;
    try {
      const bitmap = await createImageBitmap(file);
      canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
    } catch {
      setError(ERROR_CODES.E6, '画像の読み込みに失敗しました', 'upload');
      return;
    }

    // Build default params from manifest definitions
    const defaultParams = Object.fromEntries(
      (effect.params ?? []).map((p) => [p.id, p.default])
    );

    try {
      await getRunner().runEffect(
        effect.id,
        canvas,
        defaultParams,
        (progress) => downloadProgress.set(progress)
      );
    } catch (e) {
      const msg = (e as Error).message ?? '処理に失敗しました';
      if (msg === 'cancelled') { navigateTo('upload'); return; }
      setError(ERROR_CODES.E5, msg, 'processing');
      return;
    }

    // Convert result canvas to data URL
    try {
      const blob = await canvas.convertToBlob({ type: 'image/png' });
      const url = URL.createObjectURL(blob);
      resultDataUrl.set(url);
      navigateTo('result');
    } catch {
      setError(ERROR_CODES.E6, '結果の生成に失敗しました', 'processing');
    }
  }

  // ── Cancel ─────────────────────────────────────────────────────────────────

  function onCancel() {
    runner?.cancel();
    navigateTo('upload');
  }
</script>

<svelte:head>
  <title>バズエフェクト — SNSで話題の加工をブラウザで</title>
</svelte:head>

<main class="mx-auto max-w-2xl px-4 py-8">
  {#if $appStep === 'error' && $appError}
    <ErrorMessage error={$appError} />

  {:else if $appStep === 'gallery'}
    <EffectGallery onEffectSelect={onEffectSelect} />

  {:else if $appStep === 'upload' && $selectedEffect}
    <PhotoEditor
      effect={$selectedEffect}
      onApply={onApply}
      onBack={() => navigateTo('gallery')}
    />

  {:else if $appStep === 'processing'}
    <ModelDownloader
      progress={$downloadProgress}
      onCancel={onCancel}
      effectName={$selectedEffect?.name}
    />

  {:else if $appStep === 'result' && $resultDataUrl && $selectedEffect}
    <SharePanel
      resultDataUrl={$resultDataUrl}
      effect={$selectedEffect}
      onRetryPhoto={() => navigateTo('upload')}
      onRetryEffect={resetToGallery}
    />

  {:else}
    <div class="text-center text-zinc-500 text-sm py-16">読み込み中...</div>
  {/if}
</main>
