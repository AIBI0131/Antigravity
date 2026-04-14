/**
 * Unit tests for ShareHelper.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  buildShareText,
  buildShareUrl,
  canUseWebShare,
  shareResult,
  triggerDownload
} from '$lib/ShareHelper';

// ── buildShareText ────────────────────────────────────────────────────────────

describe('buildShareText', () => {
  it('includes the effect name', () => {
    const text = buildShareText('color-pop', 'カラーポップ');
    expect(text).toContain('カラーポップ');
  });

  it('includes a hashtag derived from effectId (hyphens removed)', () => {
    const text = buildShareText('color-pop', 'カラーポップ');
    expect(text).toContain('#colorpop');
  });

  it('always includes #バズエフェクト', () => {
    const text = buildShareText('neon-outline', 'ネオン輪郭');
    expect(text).toContain('#バズエフェクト');
  });
});

// ── buildShareUrl ─────────────────────────────────────────────────────────────

describe('buildShareUrl', () => {
  it('includes the effectId as a query param', () => {
    const url = buildShareUrl('color-pop');
    expect(url).toContain('?effect=color-pop');
  });
});

// ── canUseWebShare ────────────────────────────────────────────────────────────

describe('canUseWebShare', () => {
  it('returns false when navigator.share is undefined', () => {
    // happy-dom does not implement navigator.share
    expect(canUseWebShare()).toBe(false);
  });

  it('returns true when navigator.share is defined', () => {
    const original = (navigator as unknown as Record<string, unknown>).share;
    (navigator as unknown as Record<string, unknown>).share = vi.fn();
    expect(canUseWebShare()).toBe(true);
    (navigator as unknown as Record<string, unknown>).share = original;
  });
});

// ── shareResult ───────────────────────────────────────────────────────────────

describe('shareResult', () => {
  const fakeDataUrl = 'data:image/png;base64,abc123';

  beforeEach(() => {
    // Stub fetch to return a fake blob
    global.fetch = vi.fn().mockResolvedValue({
      blob: () => Promise.resolve(new Blob(['img'], { type: 'image/png' }))
    } as unknown as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns "copied" when navigator.share is unavailable and clipboard succeeds', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true
    });

    const result = await shareResult(fakeDataUrl, 'color-pop', 'カラーポップ');
    expect(result).toBe('copied');
  });

  it('returns "shared" when navigator.share is available and succeeds', async () => {
    const shareMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'share', {
      value: shareMock,
      configurable: true
    });
    Object.defineProperty(navigator, 'canShare', {
      value: vi.fn().mockReturnValue(false),
      configurable: true
    });

    const result = await shareResult(fakeDataUrl, 'color-pop', 'カラーポップ');
    expect(result).toBe('shared');
    expect(shareMock).toHaveBeenCalledOnce();
  });

  it('falls through to clipboard when navigator.share throws non-AbortError', async () => {
    Object.defineProperty(navigator, 'share', {
      value: vi.fn().mockRejectedValue(new Error('not allowed')),
      configurable: true
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true
    });

    const result = await shareResult(fakeDataUrl, 'color-pop', 'カラーポップ');
    expect(result).toBe('copied');
  });

  it('re-throws AbortError (user cancelled)', async () => {
    const abortErr = Object.assign(new Error('cancelled'), { name: 'AbortError' });
    Object.defineProperty(navigator, 'share', {
      value: vi.fn().mockRejectedValue(abortErr),
      configurable: true
    });

    await expect(shareResult(fakeDataUrl, 'color-pop', 'カラーポップ')).rejects.toMatchObject({
      name: 'AbortError'
    });
  });
});

// ── triggerDownload ───────────────────────────────────────────────────────────

describe('triggerDownload', () => {
  it('creates an anchor with correct href and download attributes', () => {
    const clickSpy = vi.fn();
    const anchor = document.createElement('a');
    vi.spyOn(document, 'createElement').mockReturnValueOnce(
      Object.assign(anchor, { click: clickSpy })
    );

    triggerDownload('blob:http://localhost/abc', 'result.png');

    expect(anchor.href).toContain('blob:');
    expect(anchor.download).toBe('result.png');
    expect(clickSpy).toHaveBeenCalledOnce();
  });
});
