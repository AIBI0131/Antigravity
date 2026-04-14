<script lang="ts">
  import type { EffectMeta } from '$lib/types.js';
  import { shareResult, triggerDownload } from '$lib/ShareHelper.js';

  interface Props {
    resultDataUrl: string;
    effect: EffectMeta;
    onRetryPhoto: () => void;
    onRetryEffect: () => void;
  }

  let { resultDataUrl, effect, onRetryPhoto, onRetryEffect }: Props = $props();

  let shareStatus = $state<'idle' | 'copied' | 'downloaded'>('idle');

  function downloadImage() {
    triggerDownload(resultDataUrl, `${effect.id}-result.png`);
  }

  async function shareImage() {
    try {
      const result = await shareResult(resultDataUrl, effect.id, effect.name);
      if (result === 'copied' || result === 'downloaded') {
        shareStatus = result;
        setTimeout(() => (shareStatus = 'idle'), 2000);
      }
    } catch {
      // AbortError (user cancelled) or other — do nothing
    }
  }

  const shareLabel = $derived(
    shareStatus === 'copied' ? '✓ コピー済み' :
    shareStatus === 'downloaded' ? '✓ 保存済み' :
    '📤 シェア'
  );
</script>

<section class="flex flex-col gap-5">
  <div class="text-center">
    <h2 class="text-xl font-bold text-zinc-100">完成！</h2>
    <p class="text-sm text-zinc-400">シェアまたは保存しよう</p>
  </div>

  <!-- Result / before preview -->
  <div class="relative overflow-hidden rounded-2xl bg-zinc-900">
    <img
      src={resultDataUrl}
      alt="処理結果"
      class="w-full object-contain max-h-96"
    />
    <div class="absolute left-2 top-2">
      <span class="rounded-full bg-black/60 px-2 py-0.5 text-xs text-zinc-200">
        {effect.name}
      </span>
    </div>
  </div>

  <!-- Action buttons -->
  <div class="flex gap-2">
    <button
      onclick={downloadImage}
      class="flex-1 rounded-xl bg-zinc-700/60 py-3 text-sm font-medium text-zinc-200 transition hover:bg-zinc-600/60 active:scale-[0.98]"
    >
      ↓ 保存
    </button>
    <button
      onclick={shareImage}
      class="flex-1 rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white transition hover:bg-violet-500 shadow-lg shadow-violet-900/40 active:scale-[0.98]"
    >
      {shareLabel}
    </button>
  </div>

  <!-- Try again links -->
  <div class="flex justify-center gap-4 text-xs text-zinc-500">
    <button onclick={onRetryPhoto} class="transition hover:text-zinc-300">
      別の写真で試す
    </button>
    <span>·</span>
    <button onclick={onRetryEffect} class="transition hover:text-zinc-300">
      別のエフェクトを試す
    </button>
  </div>
</section>
