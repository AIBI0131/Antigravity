<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import EffectCard from './EffectCard.svelte';
  import { EffectRunner } from '$lib/EffectRunner.js';
  import type { EffectMeta } from '$lib/types.js';

  interface Props {
    onEffectSelect: (effect: EffectMeta) => void;
  }

  let { onEffectSelect }: Props = $props();

  let effects: EffectMeta[] = $state([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let runner: EffectRunner | null = null;

  onMount(async () => {
    try {
      runner = new EffectRunner();
      effects = await runner.getEnabledEffects();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

  onDestroy(() => runner?.dispose());
</script>

<section class="flex flex-col gap-6">
  <div class="text-center">
    <h1 class="text-2xl font-bold tracking-tight text-zinc-100">
      バズエフェクト
    </h1>
    <p class="mt-1 text-sm text-zinc-400">
      SNSで話題の加工エフェクトをブラウザ完結で体験
    </p>
  </div>

  {#if loading}
    <!-- Loading skeleton -->
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
      {#each { length: 6 } as _, i (i)}
        <div class="overflow-hidden rounded-2xl border border-zinc-700/50 bg-zinc-800/60">
          <div class="aspect-square w-full animate-pulse bg-zinc-700/50"></div>
          <div class="p-3 space-y-2">
            <div class="h-3 w-2/3 animate-pulse rounded bg-zinc-700/50"></div>
            <div class="h-2 w-full animate-pulse rounded bg-zinc-700/50"></div>
          </div>
        </div>
      {/each}
    </div>

  {:else if error}
    <div class="rounded-xl border border-red-500/30 bg-red-900/20 p-6 text-center text-sm text-red-300">
      エフェクトの読み込みに失敗しました: {error}
    </div>

  {:else if effects.length === 0}
    <div class="rounded-xl border border-zinc-700/50 bg-zinc-800/40 p-12 text-center">
      <div class="text-4xl mb-3">✨</div>
      <p class="text-zinc-400 text-sm">エフェクトを準備中です。もうしばらくお待ちください。</p>
    </div>

  {:else}
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
      {#each effects as effect (effect.id)}
        <EffectCard {effect} onSelect={onEffectSelect} />
      {/each}
    </div>
  {/if}
</section>
