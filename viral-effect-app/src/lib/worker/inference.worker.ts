/**
 * inference.worker.ts — ML inference Web Worker
 *
 * Runs in a dedicated Worker thread so heavy ONNX inference never blocks the UI.
 * Communicates with the main thread via WorkerMessage / WorkerResponse (see types.ts).
 *
 * Key differences from birefnet-remover/offscreen/worker.js:
 *  - Uses self.onmessage / postMessage instead of chrome.runtime.*
 *  - WebGPU is ENABLED (falls back to WASM when unavailable)
 *  - ONNX WASM paths point to /onnx/ (served by vite-plugin-static-copy)
 */

import { env, pipeline, RawImage } from '@huggingface/transformers';
import type { WorkerMessage, WorkerResponse, ModelSpec, EffectParams } from '../types.js';

// ── ONNX Runtime configuration ──────────────────────────────────────────────

env.allowLocalModels = false;
env.useBrowserCache = false; // We handle caching via ModelCache (IndexedDB)

// Point to the WASM files served from public/onnx/ (copied by vite-plugin-static-copy)
env.backends = env.backends ?? {};
env.backends.onnx = env.backends.onnx ?? {};
// Use type assertion to avoid readonly constraint on runtime configuration
(env.backends.onnx as Record<string, unknown>).wasm = {
  wasmPaths: '/onnx/',
  proxy: false,
  numThreads: 1
};

// ── WebGPU detection ─────────────────────────────────────────────────────────

async function detectDevice(): Promise<'webgpu' | 'wasm'> {
  try {
    // navigator.gpu is defined in Worker scope when WebGPU is available
    const nav = navigator as Navigator & { gpu?: { requestAdapter(): Promise<unknown> } };
    if (nav.gpu) {
      const adapter = await nav.gpu.requestAdapter();
      if (adapter) return 'webgpu';
    }
  } catch {
    // WebGPU unavailable — fall through to WASM
  }
  return 'wasm';
}

// ── State ────────────────────────────────────────────────────────────────────

// Loaded pipelines keyed by model ID
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const pipelines = new Map<string, any>();
let cancelled = false;

// ── Helper: send response ────────────────────────────────────────────────────

function send(msg: WorkerResponse, transfer?: Transferable[]): void {
  (self as unknown as Worker).postMessage(msg, transfer ?? []);
}

// ── Model loading ────────────────────────────────────────────────────────────

async function loadModel(spec: ModelSpec, effectId: string): Promise<void> {
  if (pipelines.has(effectId)) {
    send({ type: 'MODEL_READY', modelId: spec.id });
    return;
  }

  send({ type: 'PROGRESS', percent: 0, label: 'モデルを読み込み中...' });

  const device = await detectDevice();

  try {
    type ProgressInfo = { status: string; loaded?: number; total?: number };

    const pipe = await pipeline('image-segmentation', spec.source, {
      device,
      dtype: device === 'webgpu' ? ('fp16' as const) : ('fp32' as const),
      progress_callback: (info: ProgressInfo) => {
        if (info.status === 'progress' && info.loaded != null && info.total != null) {
          const pct = Math.round((info.loaded / info.total) * 100);
          send({ type: 'PROGRESS', percent: pct, label: `ダウンロード中... ${pct}%`, loaded: info.loaded, total: info.total });
        } else if (info.status === 'done') {
          send({ type: 'PROGRESS', percent: 100, label: 'モデルの準備完了' });
        }
      }
    });

    pipelines.set(effectId, pipe);
    send({ type: 'MODEL_READY', modelId: spec.id });
  } catch (err) {
    send({ type: 'ERROR', message: (err as Error).message ?? 'モデル読み込み失敗' });
  }
}

// ── Inference ────────────────────────────────────────────────────────────────

async function runInference(
  imageBitmap: ImageBitmap,
  effectId: string,
  params: EffectParams
): Promise<void> {
  cancelled = false;

  // Render ImageBitmap to OffscreenCanvas so we can get pixel data
  const canvas = new OffscreenCanvas(imageBitmap.width, imageBitmap.height);
  const ctx = canvas.getContext('2d') as OffscreenCanvasRenderingContext2D;
  ctx.drawImage(imageBitmap, 0, 0);
  imageBitmap.close(); // Free the transferred bitmap

  const pipe = pipelines.get(effectId);
  if (!pipe) {
    send({ type: 'ERROR', message: `モデルが読み込まれていません (effectId: ${effectId})` });
    return;
  }

  if (cancelled) return;

  try {
    send({ type: 'PROGRESS', percent: 10, label: '推論中...' });

    const blob = await canvas.convertToBlob({ type: 'image/png' });
    const url = URL.createObjectURL(blob);
    const image = await RawImage.fromURL(url);
    URL.revokeObjectURL(url);

    type SegResult = Array<{ mask: RawImage; label: string; score: number }>;
    const results = await pipe(image) as SegResult;

    if (cancelled) return;

    const output = await applySegmentationMask(canvas, results, params);

    if (cancelled) return;

    send({ type: 'PROGRESS', percent: 95, label: '仕上げ中...' });

    const resultBitmap = await createImageBitmap(output);
    send({ type: 'RESULT', imageBitmap: resultBitmap }, [resultBitmap]);
  } catch (err) {
    send({ type: 'ERROR', message: (err as Error).message ?? '推論に失敗しました' });
  }
}

/**
 * Composite the segmentation mask onto the source canvas.
 * Effects can customise behaviour via `params.maskMode`:
 *   - 'cutout' (default): keep foreground, transparent background
 */
async function applySegmentationMask(
  src: OffscreenCanvas,
  results: Array<{ mask: RawImage; label: string; score: number }>,
  params: EffectParams
): Promise<OffscreenCanvas> {
  const { width, height } = src;
  const srcCtx = src.getContext('2d') as OffscreenCanvasRenderingContext2D;
  const srcData = srcCtx.getImageData(0, 0, width, height);

  const out = new OffscreenCanvas(width, height);
  const outCtx = out.getContext('2d') as OffscreenCanvasRenderingContext2D;
  const outData = outCtx.createImageData(width, height);

  const targetLabel = (params.targetLabel as string) ?? null;
  const result = targetLabel
    ? (results.find((r) => r.label === targetLabel) ?? results[0])
    : results[0];

  if (!result?.mask) {
    outCtx.drawImage(src, 0, 0);
    return out;
  }

  const mask: RawImage = result.mask;
  const resizedMask: RawImage = (mask.width !== width || mask.height !== height)
    ? await mask.resize(width, height)
    : mask;

  const channels = (resizedMask as unknown as { channels: number }).channels ?? 1;

  for (let i = 0; i < width * height; i++) {
    const s = i * 4;
    const alpha = (resizedMask.data as Uint8Array)[i * channels];

    outData.data[s]     = srcData.data[s];
    outData.data[s + 1] = srcData.data[s + 1];
    outData.data[s + 2] = srcData.data[s + 2];
    outData.data[s + 3] = alpha;
  }

  outCtx.putImageData(outData, 0, 0);
  return out;
}

// ── Message handler ──────────────────────────────────────────────────────────

self.onmessage = async (e: MessageEvent<WorkerMessage>) => {
  const msg = e.data;

  switch (msg.type) {
    case 'LOAD_MODEL':
      await loadModel(msg.modelSpec, msg.effectId);
      break;

    case 'RUN_INFERENCE':
      await runInference(msg.imageBitmap, msg.effectId, msg.params);
      break;

    case 'CANCEL':
      cancelled = true;
      break;

    default:
      send({ type: 'ERROR', message: `Unknown message type` });
  }
};

console.log('[InferenceWorker] Initialized');
