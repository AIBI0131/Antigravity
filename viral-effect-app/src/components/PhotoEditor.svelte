<script lang="ts">
  import { onDestroy } from 'svelte';
  import { cn } from '$lib/utils.js';
  import type { EffectMeta } from '$lib/types.js';

  const MAX_SIZE_MB = 20;
  const MAX_RESOLUTION = 4096;
  const ACCEPTED_MIME = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

  interface Props {
    effect: EffectMeta;
    onApply: (file: File) => void;
    onBack: () => void;
  }

  let { effect, onApply, onBack }: Props = $props();

  let file: File | null = $state(null);

  onDestroy(() => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  });
  let previewUrl: string | null = $state(null);
  let validationError: string | null = $state(null);
  let dragging = $state(false);
  let inputEl: HTMLInputElement;

  async function validateAndSet(f: File): Promise<void> {
    validationError = null;

    if (!ACCEPTED_MIME.includes(f.type)) {
      validationError = '対応していない形式です（JPEG / PNG / WebP / GIF）';
      return;
    }
    if (f.size > MAX_SIZE_MB * 1024 * 1024) {
      validationError = `ファイルサイズが大きすぎます（上限 ${MAX_SIZE_MB}MB）`;
      return;
    }

    // Check resolution
    const img = new Image();
    const url = URL.createObjectURL(f);
    let imgLoadFailed = false;
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.onerror = () => { imgLoadFailed = true; resolve(); };
      img.src = url;
    });
    URL.revokeObjectURL(url);

    if (imgLoadFailed || img.naturalWidth === 0) {
      validationError = '画像を読み込めませんでした';
      return;
    }
    if (img.naturalWidth > MAX_RESOLUTION || img.naturalHeight > MAX_RESOLUTION) {
      validationError = `解像度が大きすぎます（上限 ${MAX_RESOLUTION}px）`;
      return;
    }

    // All good
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(f);
    file = f;
  }

  function onFileInput(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    if (input.files?.[0]) validateAndSet(input.files[0]);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped) validateAndSet(dropped);
  }
</script>

<section class="flex flex-col gap-4">
  <!-- Back + effect name header -->
  <div class="flex items-center gap-3">
    <button
      onclick={onBack}
      class="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-zinc-400 transition hover:bg-zinc-700/60 hover:text-zinc-100"
      aria-label="戻る"
    >
      ←
    </button>
    <div>
      <h2 class="text-base font-semibold text-zinc-100">{effect.name}</h2>
      <p class="text-xs text-zinc-500">写真をアップロードして適用</p>
    </div>
  </div>

  <!-- Drop zone -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class={cn(
      'relative flex min-h-48 flex-col items-center justify-center rounded-2xl border-2 border-dashed transition-all duration-200 cursor-pointer',
      dragging
        ? 'border-violet-400 bg-violet-900/20'
        : 'border-zinc-600 bg-zinc-800/40 hover:border-zinc-500 hover:bg-zinc-800/60'
    )}
    ondragover={(e) => { e.preventDefault(); dragging = true; }}
    ondragleave={() => (dragging = false)}
    ondrop={onDrop}
    onclick={() => inputEl.click()}
    role="button"
    tabindex="0"
    onkeydown={(e) => e.key === 'Enter' && inputEl.click()}
    aria-label="画像をドロップまたはクリックして選択"
  >
    {#if previewUrl}
      <img
        src={previewUrl}
        alt="プレビュー"
        class="max-h-64 max-w-full rounded-xl object-contain"
      />
      <p class="mt-2 text-xs text-zinc-500">クリックで変更</p>
    {:else}
      <div class="flex flex-col items-center gap-2 p-8 text-center">
        <div class="text-3xl">📷</div>
        <p class="text-sm font-medium text-zinc-300">
          ドラッグ&ドロップ または クリックして選択
        </p>
        <p class="text-xs text-zinc-500">JPEG / PNG / WebP 対応 • 最大 20MB</p>
      </div>
    {/if}
  </div>

  <input
    bind:this={inputEl}
    type="file"
    accept="image/jpeg,image/png,image/webp,image/gif"
    class="sr-only"
    oninput={onFileInput}
  />

  {#if validationError}
    <p class="rounded-lg bg-red-900/30 px-3 py-2 text-xs text-red-300">{validationError}</p>
  {/if}

  <!-- Apply button -->
  <button
    onclick={() => file && onApply(file)}
    disabled={!file || !!validationError}
    class={cn(
      'rounded-xl py-3 text-sm font-semibold transition-all duration-200 active:scale-[0.98]',
      file && !validationError
        ? 'bg-violet-600 text-white hover:bg-violet-500 shadow-lg shadow-violet-900/40'
        : 'cursor-not-allowed bg-zinc-700/50 text-zinc-500'
    )}
  >
    このエフェクトを適用
  </button>
</section>
