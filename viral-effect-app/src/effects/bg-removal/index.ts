/**
 * bg-removal effect — Background Removal (heavy, ML-based)
 *
 * Uses BiRefNet-lite (via @huggingface/transformers) to segment
 * the foreground and make the background transparent.
 *
 * Processing is done in the inference Worker; this module only
 * declares the Effect interface and metadata.
 */

import type { Effect, ModelSpec, EffectParams } from '../../lib/types.js';

const BG_REMOVAL_MODEL: ModelSpec = {
  id: 'birefnet-lite',
  source: 'onnx-community/BiRefNet_lite',
  sizeMB: 45
};

class BgRemovalEffect implements Effect {
  requiresWorker(): boolean {
    return true;
  }

  getRequiredModels(): ModelSpec[] {
    return [BG_REMOVAL_MODEL];
  }

  /**
   * For heavy effects the Worker handles actual processing.
   * apply() is a no-op — EffectRunner.runEffect() calls the Worker directly.
   */
  async apply(_canvas: OffscreenCanvas, _params: EffectParams): Promise<void> {
    // Processing delegated to inference.worker.ts via WorkerBridge
  }
}

export default new BgRemovalEffect();
