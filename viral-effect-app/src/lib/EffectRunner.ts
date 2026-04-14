/**
 * EffectRunner — loads manifest.json, dynamically imports effect modules,
 * and orchestrates Canvas-only (light) or Worker-based (heavy) execution.
 *
 * Effect modules live under src/effects/<id>/index.ts and are resolved
 * at build time via import.meta.glob (Vite code-splits each effect).
 *
 * manifest.json lives in static/effects/manifest.json and is fetched at runtime.
 */

import type {
  Effect,
  EffectMeta,
  EffectManifest,
  EffectParams,
  DownloadProgress
} from './types.js';
import { WorkerBridge } from './WorkerBridge.js';

type ProgressCallback = (progress: DownloadProgress) => void;

// Vite resolves this glob at build time — all effects get code-split automatically
// Path is relative to this file: src/lib/EffectRunner.ts → src/effects/*/index.ts
const effectModules = import.meta.glob<{ default: Effect }>('../effects/*/index.ts');

export class EffectRunner {
  private manifestCache: EffectMeta[] | null = null;
  private workerBridge: WorkerBridge | null = null;

  // ── Manifest ──────────────────────────────────────────────────────────────

  async loadManifest(): Promise<EffectMeta[]> {
    if (this.manifestCache) return this.manifestCache;

    const res = await fetch('/effects/manifest.json');
    if (!res.ok) throw new Error(`Failed to load manifest: ${res.status}`);

    const manifest: EffectManifest = await res.json();
    this.manifestCache = manifest.effects;
    return manifest.effects;
  }

  async getEnabledEffects(): Promise<EffectMeta[]> {
    const effects = await this.loadManifest();
    return effects.filter((e) => e.enabled);
  }

  // ── Effect loading ────────────────────────────────────────────────────────

  async loadEffect(effectId: string): Promise<Effect> {
    const key = `../effects/${effectId}/index.ts`;
    const loader = effectModules[key];

    if (!loader) {
      throw new Error(`Effect "${effectId}" not found. Available: ${Object.keys(effectModules).join(', ')}`);
    }

    const mod = await loader();
    return mod.default;
  }

  // ── Run ───────────────────────────────────────────────────────────────────

  /**
   * Apply an effect to the provided canvas.
   *
   * Light effects: run directly on the main thread (no Worker overhead).
   * Heavy effects: delegate to the inference Web Worker.
   */
  async runEffect(
    effectId: string,
    canvas: OffscreenCanvas,
    params: EffectParams,
    onProgress?: ProgressCallback
  ): Promise<void> {
    const effect = await this.loadEffect(effectId);

    if (!effect.requiresWorker()) {
      // Light path — direct Canvas/WebGL execution
      await effect.apply(canvas, params);
      return;
    }

    // Heavy path — Worker inference
    const bridge = this.getWorkerBridge();
    const models = effect.getRequiredModels();

    // Load required models sequentially (most effects have just one)
    for (const spec of models) {
      await bridge.loadModel(spec, effectId, onProgress);
    }

    // Create an ImageBitmap from the current canvas state for transfer
    const blob = await canvas.convertToBlob({ type: 'image/png' });
    const sourceBitmap = await createImageBitmap(blob);

    const resultBitmap = await bridge.infer(sourceBitmap, effectId, params, onProgress);

    // Paint result back onto the canvas
    const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D | null;
    if (!ctx) throw new Error('Cannot get 2d context from canvas');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(resultBitmap, 0, 0);
    resultBitmap.close();

    // Post-processing pass: heavy effects may apply additional Canvas compositing
    // (e.g. background-glow adds dark bg + glow halo on top of the Worker's cutout).
    // bg-removal.apply() is a no-op, so this is safe for all heavy effects.
    await effect.apply(canvas, params);
  }

  // ── Worker lifecycle ──────────────────────────────────────────────────────

  private getWorkerBridge(): WorkerBridge {
    if (!this.workerBridge) {
      this.workerBridge = new WorkerBridge();
    }
    return this.workerBridge;
  }

  cancel(): void {
    this.workerBridge?.cancel();
  }

  dispose(): void {
    this.workerBridge?.terminate();
    this.workerBridge = null;
  }
}
