/**
 * WorkerBridge — main-thread interface to the inference Web Worker.
 *
 * Manages the Worker lifecycle and provides a typed async API so
 * the rest of the app never deals with raw postMessage calls.
 */

import type {
  WorkerMessage,
  WorkerResponse,
  ModelSpec,
  EffectParams,
  DownloadProgress
} from './types.js';

type ProgressCallback = (progress: DownloadProgress) => void;

export class WorkerBridge {
  private worker: Worker | null = null;
  private pendingResolve: ((bitmap: ImageBitmap) => void) | null = null;
  private pendingReject: ((err: Error) => void) | null = null;
  private onProgress: ProgressCallback | null = null;
  private currentModelId: string | null = null;

  private ensureWorker(): Worker {
    if (!this.worker) {
      // Vite handles ?worker inline for bundling
      this.worker = new Worker(
        new URL('./worker/inference.worker.ts', import.meta.url),
        { type: 'module' }
      );
      this.worker.onmessage = this.handleMessage.bind(this);
      this.worker.onerror = (e) => {
        this.pendingReject?.(new Error(`Worker error: ${e.message}`));
        this.clearPending();
      };
    }
    return this.worker;
  }

  private handleMessage(e: MessageEvent<WorkerResponse>): void {
    const msg = e.data;

    switch (msg.type) {
      case 'PROGRESS':
        if (this.onProgress && this.currentModelId) {
          this.onProgress({
            modelId: this.currentModelId,
            loaded: msg.loaded ?? msg.percent,
            total: msg.total ?? 100,
            percent: msg.percent,
            label: msg.label
          });
        }
        break;

      case 'MODEL_READY':
        // Model loaded — resolve the loadModel promise (if waiting)
        this.pendingResolve?.(null as unknown as ImageBitmap);
        this.clearPending();
        break;

      case 'RESULT':
        this.pendingResolve?.(msg.imageBitmap);
        this.clearPending();
        break;

      case 'ERROR':
        this.pendingReject?.(new Error(msg.message));
        this.clearPending();
        break;
    }
  }

  private clearPending(): void {
    this.pendingResolve = null;
    this.pendingReject = null;
    this.onProgress = null;
  }

  /**
   * Send a message and wait for MODEL_READY or ERROR.
   */
  loadModel(spec: ModelSpec, effectId: string, onProgress?: ProgressCallback): Promise<void> {
    return new Promise((resolve, reject) => {
      const worker = this.ensureWorker();
      this.currentModelId = spec.id;
      this.onProgress = onProgress ?? null;
      this.pendingResolve = () => resolve();
      this.pendingReject = reject;

      const msg: WorkerMessage = { type: 'LOAD_MODEL', modelSpec: spec, effectId };
      worker.postMessage(msg);
    });
  }

  /**
   * Run inference and return the result as an ImageBitmap.
   * The caller is responsible for closing the bitmap when done.
   */
  infer(
    imageData: ImageBitmap,
    effectId: string,
    params: EffectParams,
    onProgress?: ProgressCallback
  ): Promise<ImageBitmap> {
    return new Promise((resolve, reject) => {
      const worker = this.ensureWorker();
      this.onProgress = onProgress ?? null;
      this.pendingResolve = resolve;
      this.pendingReject = reject;

      const msg: WorkerMessage = {
        type: 'RUN_INFERENCE',
        imageBitmap: imageData,
        effectId,
        params
      };
      // Transfer the ImageBitmap for zero-copy
      worker.postMessage(msg, [imageData]);
    });
  }

  /**
   * Signal the worker to cancel the in-progress inference.
   */
  cancel(): void {
    if (this.worker) {
      const msg: WorkerMessage = { type: 'CANCEL' };
      this.worker.postMessage(msg);
    }
    this.pendingReject?.(new Error('cancelled'));
    this.clearPending();
  }

  /**
   * Terminate the worker entirely (e.g. on component unmount).
   */
  terminate(): void {
    this.cancel();
    this.worker?.terminate();
    this.worker = null;
  }
}
