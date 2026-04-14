/**
 * ShareHelper — unified share / clipboard / download utility.
 *
 * Priority:
 *   1. Web Share API with file (mobile-native share sheet)
 *   2. Web Share API without file (desktop browsers)
 *   3. Clipboard copy (fallback)
 */

export function canUseWebShare(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.share;
}

export function buildShareText(effectId: string, effectName: string): string {
  const tag = `#${effectId.replace(/-/g, '')}`;
  return `${effectName}エフェクトを試してみた！ ${tag} #バズエフェクト`;
}

export function buildShareUrl(effectId: string): string {
  const base = typeof location !== 'undefined' ? location.origin : '';
  return `${base}/?effect=${effectId}`;
}

/**
 * Share a processed image.
 * @param dataUrl  - result data URL (blob: or data:)
 * @param effectId - effect id for URL + hashtag
 * @param effectName - display name for share text
 * @returns 'shared' | 'copied' | 'downloaded'
 */
export async function shareResult(
  dataUrl: string,
  effectId: string,
  effectName: string
): Promise<'shared' | 'copied' | 'downloaded'> {
  const text = buildShareText(effectId, effectName);
  const url = buildShareUrl(effectId);

  if (canUseWebShare()) {
    try {
      // Try sharing with the image file (works on mobile)
      const res = await fetch(dataUrl);
      const blob = await res.blob();
      const file = new File([blob], `${effectId}.png`, { type: 'image/png' });
      const canShareFile = !!navigator.canShare?.({ files: [file] });

      await navigator.share({
        title: effectName,
        text,
        url,
        ...(canShareFile ? { files: [file] } : {})
      });
      return 'shared';
    } catch (e) {
      // User cancelled (AbortError) — do not fall through
      if ((e as Error).name === 'AbortError') throw e;
      // Other errors: fall through to clipboard
    }
  }

  // Clipboard fallback
  try {
    await navigator.clipboard.writeText(`${text}\n${url}`);
    return 'copied';
  } catch {
    // Last resort: trigger a download
    triggerDownload(dataUrl, `${effectId}-result.png`);
    return 'downloaded';
  }
}

export function triggerDownload(dataUrl: string, filename: string): void {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  a.click();
}
