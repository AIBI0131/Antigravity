// ============================================================
// Viral Effect App — Core Type Definitions
// ============================================================

// --------------- Effect Interface ---------------

export interface Effect {
  /** Apply the effect to the given canvas using the provided params */
  apply(canvas: OffscreenCanvas, params: EffectParams): Promise<void>;
  /** Return model specs this effect requires (empty for light effects) */
  getRequiredModels(): ModelSpec[];
  /** True if this effect needs the Web Worker (ML inference). False for Canvas-only effects. */
  requiresWorker(): boolean;
}

// --------------- Manifest / Meta ---------------

export type EffectWeight = 'light' | 'heavy';

export interface ModelSpec {
  id: string;
  /** HuggingFace model ID or direct URL */
  source: string;
  sizeMB: number;
}

export interface EffectParamDef {
  id: string;
  type: 'range' | 'select' | 'boolean' | 'color';
  label: string;
  default: string | number | boolean;
  min?: number;
  max?: number;
  step?: number;
  options?: Array<{ value: string; label: string }>;
}

export interface EffectMeta {
  id: string;
  name: string;
  description: string;
  weight: EffectWeight;
  enabled: boolean;
  requiredModels: ModelSpec[];
  params: EffectParamDef[];
  /** Path to preview image (webp preferred) */
  previewImage: string;
  addedAt: string;
}

export interface EffectManifest {
  version: string;
  effects: EffectMeta[];
}

// --------------- Effect Params ---------------

export type EffectParams = Record<string, string | number | boolean>;

// --------------- Worker Protocol ---------------

export type WorkerMessage =
  | { type: 'LOAD_MODEL'; modelSpec: ModelSpec; effectId: string }
  | { type: 'RUN_INFERENCE'; imageBitmap: ImageBitmap; effectId: string; params: EffectParams }
  | { type: 'CANCEL' };

export type WorkerResponse =
  | { type: 'PROGRESS'; percent: number; label: string; loaded?: number; total?: number }
  | { type: 'MODEL_READY'; modelId: string }
  | { type: 'RESULT'; imageBitmap: ImageBitmap }
  | { type: 'ERROR'; message: string };

// --------------- Download Progress ---------------

export interface DownloadProgress {
  modelId: string;
  loaded: number;
  total: number;
  percent: number;
  label: string;
}

// --------------- App State ---------------

export type AppStep = 'gallery' | 'upload' | 'processing' | 'result' | 'error';

export interface AppError {
  code: string;
  message: string;
  recoverStep: AppStep;
}

// Error codes
export const ERROR_CODES = {
  E1: 'FILE_TOO_LARGE',
  E2: 'UNSUPPORTED_FORMAT',
  E3: 'RESOLUTION_EXCEEDED',
  E4: 'MODEL_DOWNLOAD_FAILED',
  E5: 'INFERENCE_FAILED',
  E6: 'CANVAS_FAILED',
  E7: 'SHARE_FAILED',
  E8: 'WEBGPU_UNAVAILABLE',
  E9: 'WORKER_CRASHED',
} as const;
