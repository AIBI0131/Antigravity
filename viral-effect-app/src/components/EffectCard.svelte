<script lang="ts">
  import { onMount } from 'svelte';
  import { cn } from '$lib/utils.js';
  import Badge from './ui/Badge.svelte';
  import type { EffectMeta } from '$lib/types.js';

  interface Props {
    effect: EffectMeta;
    onSelect: (effect: EffectMeta) => void;
  }

  let { effect, onSelect }: Props = $props();

  let hovered = $state(false);
  // プレースホルダーを初期値にして、実画像が存在する場合のみ差し替える
  // → 404による「壊れた画像」チラつきを防止
  let imgSrc = $state('/placeholder-preview.webp');

  onMount(() => {
    if (effect.previewImage) {
      const probe = new Image();
      probe.onload = () => { imgSrc = effect.previewImage as string; };
      probe.src = effect.previewImage;
    }
  });

  const totalSizeMB = $derived(
    effect.requiredModels.reduce((s, m) => s + m.sizeMB, 0)
  );
</script>

<button
  onclick={() => onSelect(effect)}
  onmouseenter={() => (hovered = true)}
  onmouseleave={() => (hovered = false)}
  class={cn(
    'group relative flex flex-col overflow-hidden rounded-2xl',
    'border border-zinc-700/50 bg-zinc-800/60 text-left',
    'transition-all duration-200',
    'hover:border-zinc-500/70 hover:bg-zinc-800 hover:shadow-lg hover:shadow-black/30',
    'active:scale-[0.98] focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-500'
  )}
  aria-label={`エフェクト: ${effect.name}`}
>
  <!-- Preview image -->
  <div class="relative aspect-square w-full overflow-hidden bg-zinc-900">
    <img
      src={imgSrc}
      alt={effect.name}
      class={cn(
        'h-full w-full object-cover transition-transform duration-300',
        hovered ? 'scale-105' : 'scale-100'
      )}
      loading="lazy"
    />

    <!-- Weight badge overlay -->
    <div class="absolute left-2 top-2">
      <Badge weight={effect.weight} sizeMB={totalSizeMB > 0 ? totalSizeMB : undefined} />
    </div>
  </div>

  <!-- Info -->
  <div class="flex flex-1 flex-col gap-1 p-3">
    <h3 class="text-sm font-semibold text-zinc-100 leading-tight">{effect.name}</h3>
    <p class="text-xs text-zinc-400 leading-relaxed line-clamp-2">{effect.description}</p>
  </div>
</button>
