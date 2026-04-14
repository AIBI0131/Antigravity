/**
 * Unit tests for EffectRunner.
 *
 * Mocks:
 *  - fetch (manifest.json)
 *  - import.meta.glob (effect modules)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EffectRunner } from '$lib/EffectRunner';
import type { EffectManifest } from '$lib/types';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_MANIFEST: EffectManifest = {
  version: '1',
  effects: [
    {
      id: 'color-pop',
      name: 'カラーポップ',
      description: 'Test',
      weight: 'light',
      enabled: true,
      requiredModels: [],
      params: [],
      previewImage: '/effects/color-pop/preview.webp',
      addedAt: '2026-04-13'
    },
    {
      id: 'neon-outline',
      name: 'ネオン輪郭',
      description: 'Test',
      weight: 'light',
      enabled: true,
      requiredModels: [],
      params: [],
      previewImage: '/effects/neon-outline/preview.webp',
      addedAt: '2026-04-13'
    },
    {
      id: 'background-glow',
      name: '背景グロー',
      description: 'Test',
      weight: 'heavy',
      enabled: false,  // disabled
      requiredModels: [{ id: 'rmbg-1.4', source: 'RMBG-1.4', sizeMB: 176 }],
      params: [],
      previewImage: '/effects/background-glow/preview.webp',
      addedAt: '2026-04-13'
    }
  ]
};

function mockFetch(manifest: EffectManifest) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(manifest)
  } as unknown as Response);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('EffectRunner.loadManifest', () => {
  beforeEach(() => mockFetch(MOCK_MANIFEST));

  it('returns all effects from manifest', async () => {
    const runner = new EffectRunner();
    const effects = await runner.loadManifest();
    expect(effects).toHaveLength(3);
  });

  it('caches the manifest on second call (fetch called once)', async () => {
    const runner = new EffectRunner();
    await runner.loadManifest();
    await runner.loadManifest();
    expect(global.fetch).toHaveBeenCalledOnce();
  });

  it('throws when fetch fails', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404
    } as unknown as Response);

    const runner = new EffectRunner();
    await expect(runner.loadManifest()).rejects.toThrow('404');
  });
});

describe('EffectRunner.getEnabledEffects', () => {
  beforeEach(() => mockFetch(MOCK_MANIFEST));

  it('returns only enabled effects', async () => {
    const runner = new EffectRunner();
    const effects = await runner.getEnabledEffects();
    expect(effects).toHaveLength(2);
    expect(effects.every((e) => e.enabled)).toBe(true);
  });

  it('excludes background-glow (enabled: false)', async () => {
    const runner = new EffectRunner();
    const effects = await runner.getEnabledEffects();
    expect(effects.find((e) => e.id === 'background-glow')).toBeUndefined();
  });
});

describe('EffectRunner.loadEffect', () => {
  beforeEach(() => mockFetch(MOCK_MANIFEST));

  it('throws for an unknown effectId', async () => {
    const runner = new EffectRunner();
    await expect(runner.loadEffect('does-not-exist')).rejects.toThrow(
      '"does-not-exist" not found'
    );
  });
});
