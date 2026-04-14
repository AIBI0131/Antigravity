/**
 * background-glow — Heavy effect (requires BiRefNet-lite ML model)
 *
 * Removes the background, then composites the subject on a dark background
 * with a soft glow halo effect using Canvas 2D shadow/blur.
 *
 * Processing is handled by the inference Worker (EffectRunner delegates to WorkerBridge).
 * This module only declares the Effect interface and required model spec.
 */

import type { Effect, ModelSpec, EffectParams } from '../../lib/types.js';

const BIREFNET_MODEL: ModelSpec = {
  id: 'birefnet-lite',
  source: 'onnx-community/BiRefNet_lite',
  sizeMB: 45
};

class BackgroundGlowEffect implements Effect {
  requiresWorker(): boolean { return true; }

  getRequiredModels(): ModelSpec[] {
    return [BIREFNET_MODEL];
  }

  /**
   * For heavy effects, EffectRunner.runEffect() calls the Worker directly.
   * apply() adds a post-processing glow pass after the Worker returns the result.
   */
  async apply(canvas: OffscreenCanvas, params: EffectParams): Promise<void> {
    const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    const { width, height } = canvas;

    // At this point, the Worker has already painted the cutout (foreground on transparent bg)
    // onto this canvas. We add: dark bg + glow effect.

    const glowColor = (params.glowColor as string) ?? '#ffffff';
    const glowIntensity = Number(params.glowIntensity ?? 0.8);
    const glowRadius = Number(params.glowRadius ?? 30);

    // Capture current (cutout) pixels
    const cutout = ctx.getImageData(0, 0, width, height);

    // 1. Fill with dark background
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, width, height);

    // 2. Draw glow layer (blurred copy of cutout)
    const glowCanvas = new OffscreenCanvas(width, height);
    const gc = glowCanvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    gc.putImageData(cutout, 0, 0);

    ctx.save();
    ctx.shadowColor = glowColor;
    ctx.shadowBlur = glowRadius;
    ctx.globalAlpha = glowIntensity;
    // Draw multiple times to intensify glow
    for (let i = 0; i < 3; i++) {
      ctx.drawImage(glowCanvas, 0, 0);
    }
    ctx.restore();

    // 3. Draw sharp foreground on top
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    ctx.putImageData(cutout, 0, 0);
    ctx.restore();
  }
}

export default new BackgroundGlowEffect();
