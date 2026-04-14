<script lang="ts">
  import { cn } from '$lib/utils.js';

  interface Props {
    percent: number;      // 0–100
    label?: string;
    class?: string;
  }

  let { percent, label, class: className }: Props = $props();
  const clamped = $derived(Math.max(0, Math.min(100, percent)));
</script>

<div class={cn('w-full', className)}>
  {#if label}
    <div class="mb-1 flex justify-between text-xs text-zinc-400">
      <span>{label}</span>
      <span>{clamped}%</span>
    </div>
  {/if}
  <div class="h-2 w-full overflow-hidden rounded-full bg-zinc-700">
    <div
      class="h-full rounded-full bg-violet-500 transition-all duration-300 ease-out"
      style="width: {clamped}%"
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    ></div>
  </div>
</div>
