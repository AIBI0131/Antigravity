/**
 * E2E: wasm-fallback — verifies the app works when WebGPU is disabled.
 *
 * This test runs under the `chromium-no-webgpu` project which launches
 * Chromium with --disable-features=WebGPU,WebGPUDeveloperFeatures.
 *
 * Scope:
 *  - Light effects (Canvas-only) must work regardless of WebGPU status.
 *  - The UI must not crash on load when WebGPU is unavailable.
 *  - NOTE: WASM inference path for heavy effects is exercised here at the
 *    unit level (WorkerBridge / detectDevice). Full E2E inference is skipped
 *    until a heavy effect is enabled for testing (models are 100-200 MB).
 */

import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(__dirname, '../fixtures/test-image.png');

test.describe('App with WebGPU disabled', () => {
  test('gallery loads without errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));

    await page.goto('/');
    await expect(page.locator('button[aria-label^="エフェクト:"]').first()).toBeVisible({
      timeout: 8000
    });

    // No JS errors on load
    expect(errors.filter((e) => !e.includes('favicon'))).toHaveLength(0);
  });

  test('light effect (color-pop) applies correctly without WebGPU', async ({ page }) => {
    await page.goto('/');
    await page.locator('button[aria-label="エフェクト: カラーポップ"]').click({ timeout: 8000 });

    await page.locator('input[type="file"]').setInputFiles(FIXTURE);
    await page.locator('button:has-text("このエフェクトを適用")').click();

    // Canvas-only effects don't need WebGPU — result should appear normally
    await expect(page.locator('h2:has-text("完成！")')).toBeVisible({ timeout: 15000 });
  });

  test('neon-outline applies correctly without WebGPU', async ({ page }) => {
    await page.goto('/');
    await page.locator('button[aria-label="エフェクト: ネオン輪郭"]').click({ timeout: 8000 });

    await page.locator('input[type="file"]').setInputFiles(FIXTURE);
    await page.locator('button:has-text("このエフェクトを適用")').click();

    await expect(page.locator('h2:has-text("完成！")')).toBeVisible({ timeout: 15000 });
  });
});

test.describe('Deep link with WebGPU disabled', () => {
  test('?effect=neon-outline deep link works without WebGPU', async ({ page }) => {
    await page.goto('/?effect=neon-outline');
    await expect(page.locator('text=写真をアップロードして適用')).toBeVisible({ timeout: 8000 });

    await page.locator('input[type="file"]').setInputFiles(FIXTURE);
    await page.locator('button:has-text("このエフェクトを適用")').click();
    await expect(page.locator('h2:has-text("完成！")')).toBeVisible({ timeout: 15000 });
  });
});
