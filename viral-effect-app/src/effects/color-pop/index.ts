/**
 * color-pop — Light effect (Canvas API only, no model needed)
 *
 * Keeps pixels in a specified hue range colorful,
 * converts everything else to greyscale.
 */

import type { Effect, ModelSpec, EffectParams } from '../../lib/types.js';

class ColorPopEffect implements Effect {
  requiresWorker(): boolean { return false; }
  getRequiredModels(): ModelSpec[] { return []; }

  async apply(canvas: OffscreenCanvas, params: EffectParams): Promise<void> {
    const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    const { width, height } = canvas;
    const imgData = ctx.getImageData(0, 0, width, height);
    const data = imgData.data;

    const targetHue = Number(params.targetHue ?? 0);    // 0–360
    const tolerance = Number(params.tolerance ?? 30);   // degrees

    for (let i = 0; i < data.length; i += 4) {
      const r = data[i] / 255;
      const g = data[i + 1] / 255;
      const b = data[i + 2] / 255;

      const hue = rgbToHue(r, g, b);
      const diff = Math.min(
        Math.abs(hue - targetHue),
        360 - Math.abs(hue - targetHue)
      );

      if (diff > tolerance) {
        // Desaturate: convert to greyscale
        const grey = Math.round(data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114);
        data[i]     = grey;
        data[i + 1] = grey;
        data[i + 2] = grey;
      }
    }

    ctx.putImageData(imgData, 0, 0);
  }
}

function rgbToHue(r: number, g: number, b: number): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;

  if (delta === 0) return 0;

  let hue: number;
  if (max === r) {
    hue = ((g - b) / delta) % 6;
  } else if (max === g) {
    hue = (b - r) / delta + 2;
  } else {
    hue = (r - g) / delta + 4;
  }

  hue = (hue * 60 + 360) % 360;
  return hue;
}

export default new ColorPopEffect();
