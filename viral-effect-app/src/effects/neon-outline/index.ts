/**
 * neon-outline — Light effect (Canvas 2D only)
 *
 * Detects edges using a Sobel filter, draws them in a neon colour,
 * and darkens the non-edge areas for a "glow on dark" look.
 */

import type { Effect, ModelSpec, EffectParams } from '../../lib/types.js';

class NeonOutlineEffect implements Effect {
  requiresWorker(): boolean { return false; }
  getRequiredModels(): ModelSpec[] { return []; }

  async apply(canvas: OffscreenCanvas, params: EffectParams): Promise<void> {
    const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    const { width, height } = canvas;

    // Params
    const outlineColor = (params.outlineColor as string) ?? '#00ffff';
    const outlineWidth = Number(params.outlineWidth ?? 1);
    const bgDarkness   = Number(params.bgDarkness   ?? 0.7); // 0–1: how much to darken bg

    // Parse hex → rgb for the neon colour
    const [nr, ng, nb] = hexToRgb(outlineColor);

    const src = ctx.getImageData(0, 0, width, height);
    const out = ctx.createImageData(width, height);
    const s = src.data;
    const d = out.data;

    // Build greyscale luma array
    const luma = new Float32Array(width * height);
    for (let i = 0; i < width * height; i++) {
      const p = i * 4;
      luma[i] = (s[p] * 0.299 + s[p + 1] * 0.587 + s[p + 2] * 0.114) / 255;
    }

    // Sobel edge detection
    const edge = new Float32Array(width * height);
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        const idx = y * width + x;
        const gx =
          -luma[(y - 1) * width + (x - 1)] - 2 * luma[y * width + (x - 1)] - luma[(y + 1) * width + (x - 1)] +
           luma[(y - 1) * width + (x + 1)] + 2 * luma[y * width + (x + 1)] + luma[(y + 1) * width + (x + 1)];
        const gy =
          -luma[(y - 1) * width + (x - 1)] - 2 * luma[(y - 1) * width + x] - luma[(y - 1) * width + (x + 1)] +
           luma[(y + 1) * width + (x - 1)] + 2 * luma[(y + 1) * width + x] + luma[(y + 1) * width + (x + 1)];
        edge[idx] = Math.min(Math.sqrt(gx * gx + gy * gy), 1);
      }
    }

    // Dilate edges by outlineWidth (simple box max)
    const dilated = outlineWidth > 1 ? dilateEdge(edge, width, height, outlineWidth) : edge;

    // Compose output: darken original + neon overlay on edges
    for (let i = 0; i < width * height; i++) {
      const p = i * 4;
      const e = dilated[i];
      const darken = 1 - bgDarkness * (1 - e); // near 1 on edges, near (1-bgDarkness) on bg

      d[p]     = Math.round(s[p]     * darken * (1 - e) + nr * e * 255);
      d[p + 1] = Math.round(s[p + 1] * darken * (1 - e) + ng * e * 255);
      d[p + 2] = Math.round(s[p + 2] * darken * (1 - e) + nb * e * 255);
      d[p + 3] = s[p + 3];
    }

    ctx.putImageData(out, 0, 0);

    // Add a second glow pass via Canvas shadow on an edge-only layer
    const edgeCanvas = new OffscreenCanvas(width, height);
    const ec = edgeCanvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
    ec.putImageData(out, 0, 0);

    ctx.save();
    ctx.shadowColor = outlineColor;
    ctx.shadowBlur = 12;
    ctx.globalCompositeOperation = 'screen';
    ctx.drawImage(edgeCanvas, 0, 0);
    ctx.restore();
  }
}

function dilateEdge(edge: Float32Array, w: number, h: number, r: number): Float32Array {
  const out = new Float32Array(edge.length);
  const ri = Math.round(r);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let max = 0;
      for (let dy = -ri; dy <= ri; dy++) {
        for (let dx = -ri; dx <= ri; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
            max = Math.max(max, edge[ny * w + nx]);
          }
        }
      }
      out[y * w + x] = max;
    }
  }
  return out;
}

function hexToRgb(hex: string): [number, number, number] {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return [0, 1, 1]; // default cyan
  return [
    parseInt(result[1], 16) / 255,
    parseInt(result[2], 16) / 255,
    parseInt(result[3], 16) / 255
  ];
}

export default new NeonOutlineEffect();
