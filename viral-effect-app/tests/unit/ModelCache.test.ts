/**
 * Unit tests for ModelCache — IndexedDB LRU cache.
 * Uses fake-indexeddb to provide a real IndexedDB API in Node/happy-dom.
 */

import 'fake-indexeddb/auto'; // Polyfills indexedDB, IDBKeyRange globally
import { describe, it, expect, beforeEach } from 'vitest';
import { ModelCache } from '$lib/ModelCache';

describe('ModelCache', () => {
  let cache: ModelCache;

  beforeEach(() => {
    // Create a fresh instance each test (shares the same in-memory IDB)
    cache = new ModelCache();
  });

  it('should store and retrieve a model', async () => {
    const buf = new ArrayBuffer(8);
    await cache.set('test-model', buf, 0.001);
    const result = await cache.get('test-model');
    expect(result).not.toBeNull();
    expect(result?.byteLength).toBe(8);
  });

  it('should return null for missing model', async () => {
    const result = await cache.get('nonexistent');
    expect(result).toBeNull();
  });

  it('has() returns correct boolean', async () => {
    expect(await cache.has('x')).toBe(false);
    await cache.set('x', new ArrayBuffer(4), 0.001);
    expect(await cache.has('x')).toBe(true);
  });

  it('delete() removes the entry', async () => {
    await cache.set('del-me', new ArrayBuffer(4), 0.001);
    await cache.delete('del-me');
    expect(await cache.has('del-me')).toBe(false);
  });

  it('getTotalSizeMB() sums sizes', async () => {
    await cache.set('m1-total', new ArrayBuffer(4), 10);
    await cache.set('m2-total', new ArrayBuffer(4), 20);
    const total = await cache.getTotalSizeMB();
    expect(total).toBeGreaterThanOrEqual(30);
  });

  it('cleanup() removes LRU entries over limit', async () => {
    await cache.set('cleanup-large', new ArrayBuffer(4), 50);
    // Small delay then access small to make it more recent
    await new Promise((r) => setTimeout(r, 10));
    await cache.set('cleanup-small', new ArrayBuffer(4), 5);
    await cache.get('cleanup-small'); // touch to update accessedAt

    // Limit to 20 MB — 'large' (50 MB, accessed earlier) should be evicted
    await cache.cleanup(20);

    expect(await cache.has('cleanup-large')).toBe(false);
    expect(await cache.has('cleanup-small')).toBe(true);
  });
});
