<script lang="ts">
  import ProgressBar from './ui/ProgressBar.svelte';
  import type { DownloadProgress } from '$lib/types.js';

  interface Props {
    progress: DownloadProgress | null;
    onCancel: () => void;
  }

  let { progress, onCancel }: Props = $props();

  const percent = $derived(progress?.percent ?? 0);
  const label = $derived(progress?.label ?? 'AIモデルを準備中...');
</script>

<section class="flex flex-col items-center gap-6 px-4 py-8 text-center">
  <!-- Pulsing animation -->
  <div class="relative flex items-center justify-center">
    <div class="absolute h-20 w-20 animate-ping rounded-full bg-violet-500/20"></div>
    <div class="relative flex h-16 w-16 items-center justify-center rounded-full bg-violet-600/30 text-3xl">
      🤖
    </div>
  </div>

  <div class="flex flex-col gap-1">
    <h2 class="text-base font-semibold text-zinc-100">AIモデルをダウンロード中</h2>
    <p class="max-w-xs text-xs text-zinc-400 leading-relaxed">
      初回のみ必要です。次回からは即座に適用されます。
    </p>
  </div>

  <div class="w-full max-w-xs">
    <ProgressBar {percent} label={label} />
    {#if progress && progress.total > 100}
      <p class="mt-1 text-center text-xs text-zinc-500">
        {Math.round(progress.loaded / 1024 / 1024 * 10) / 10} MB /
        {Math.round(progress.total / 1024 / 1024 * 10) / 10} MB
      </p>
    {/if}
  </div>

  <button
    onclick={onCancel}
    class="rounded-lg px-4 py-2 text-xs font-medium text-zinc-500 transition hover:bg-zinc-700/50 hover:text-zinc-300"
  >
    キャンセル
  </button>
</section>
