/**
 * E2E: mobile-touch — iPhone / Android viewport tests
 *
 * Runs under the mobile-safari and mobile-chrome projects defined in
 * playwright.config.ts. Tests touch interactions and responsive layout.
 */

import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(__dirname, '../fixtures/test-image.png');

test.describe('Responsive layout', () => {
  test('gallery renders in 2-column grid on small screen', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('button[aria-label^="エフェクト:"]').first()).toBeVisible({
      timeout: 8000
    });

    // Grid should be 2 columns at mobile width (grid-cols-2)
    const grid = page.locator('.grid').first();
    await expect(grid).toBeVisible();
    const classes = await grid.getAttribute('class');
    expect(classes).toContain('grid-cols-2');
  });

  test('main content fits within viewport width', async ({ page }) => {
    await page.goto('/');
    const main = page.locator('main');
    const box = await main.boundingBox();
    const viewport = page.viewportSize();
    if (box && viewport) {
      expect(box.width).toBeLessThanOrEqual(viewport.width);
    }
  });
});

test.describe('Mobile touch: effect selection', () => {
  test('tapping an effect card navigates to upload step', async ({ page }) => {
    await page.goto('/');
    const card = page.locator('button[aria-label="エフェクト: カラーポップ"]');
    await expect(card).toBeVisible({ timeout: 8000 });

    await card.tap();
    await expect(page.locator('text=写真をアップロードして適用')).toBeVisible({ timeout: 3000 });
  });

  test('drop zone is visible on upload step', async ({ page }) => {
    await page.goto('/');
    await page.locator('button[aria-label="エフェクト: カラーポップ"]').tap({ timeout: 8000 });

    // Drop zone (role=button with aria-label)
    await expect(
      page.locator('[aria-label="画像をドロップまたはクリックして選択"]')
    ).toBeVisible();
  });
});

test.describe('Mobile touch: full apply flow', () => {
  test('completes the full flow on mobile viewport', async ({ page, browserName }) => {
    // WebKit (Safari) does not support OffscreenCanvas + createImageBitmap in the Playwright
    // test environment. Real Safari on iOS supports these APIs; skip only in the test runner.
    test.skip(browserName === 'webkit', 'OffscreenCanvas not available in Playwright WebKit');

    await page.goto('/');
    await page.locator('button[aria-label="エフェクト: カラーポップ"]').tap({ timeout: 8000 });

    // Upload via file input (mobile browsers use file picker)
    const input = page.locator('input[type="file"]');
    await input.setInputFiles(FIXTURE);

    // Preview appears
    await expect(page.locator('img[alt="プレビュー"]')).toBeVisible({ timeout: 3000 });

    // Apply
    await page.locator('button:has-text("このエフェクトを適用")').tap();
    await expect(page.locator('h2:has-text("完成！")')).toBeVisible({ timeout: 15000 });

    // Result: share button is present
    await expect(page.locator('button:has-text("シェア")')).toBeVisible();
  });
});
