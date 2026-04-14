/**
 * stores.ts — Svelte stores for global app state.
 *
 * Uses Svelte 5 runes ($state) where components need reactivity,
 * and plain writable stores for cross-component sharing.
 */

import { writable } from 'svelte/store';
import type { AppStep, AppError, EffectMeta, DownloadProgress } from './types.js';

// ── App navigation state ──────────────────────────────────────────────────────

export const appStep = writable<AppStep>('gallery');

export const appError = writable<AppError | null>(null);

// ── Selected effect ───────────────────────────────────────────────────────────

export const selectedEffect = writable<EffectMeta | null>(null);

// ── Uploaded image ────────────────────────────────────────────────────────────

export const uploadedFile = writable<File | null>(null);

// ── Processing state ──────────────────────────────────────────────────────────

export const downloadProgress = writable<DownloadProgress | null>(null);

/** The processed result as a data URL (PNG) */
export const resultDataUrl = writable<string | null>(null);

// ── Helpers ───────────────────────────────────────────────────────────────────

export function navigateTo(step: AppStep): void {
  appError.set(null);
  appStep.set(step);
}

export function setError(code: string, message: string, recoverStep: AppStep): void {
  appError.set({ code, message, recoverStep });
  appStep.set('error');
}

export function resetToGallery(): void {
  selectedEffect.set(null);
  uploadedFile.set(null);
  downloadProgress.set(null);
  resultDataUrl.set(null);
  appError.set(null);
  appStep.set('gallery');
}
