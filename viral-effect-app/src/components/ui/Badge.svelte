<script lang="ts">
  import { cn } from '$lib/utils.js';
  import type { EffectWeight } from '$lib/types.js';

  interface Props {
    weight: EffectWeight;
    sizeMB?: number;
    class?: string;
  }

  let { weight, sizeMB, class: className }: Props = $props();
</script>

{#if weight === 'light'}
  <span class={cn(
    'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
    'bg-emerald-900/50 text-emerald-300 ring-1 ring-emerald-500/30',
    className
  )}>
    ⚡ 即座に適用
  </span>
{:else}
  <span class={cn(
    'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
    'bg-violet-900/50 text-violet-300 ring-1 ring-violet-500/30',
    className
  )}>
    🤖 AIモデル{sizeMB ? ` ~${sizeMB}MB` : ''}
  </span>
{/if}
