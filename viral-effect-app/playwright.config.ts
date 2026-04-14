import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/e2e',
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry'
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: ['**/mobile-touch.spec.ts']
    },
    {
      name: 'mobile-safari',
      use: { ...devices['iPhone 14'] },
      testIgnore: ['**/wasm-fallback.spec.ts']
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 7'] },
      testIgnore: ['**/wasm-fallback.spec.ts']
    },
    // WebGPU無効化テスト用（wasm-fallback.spec.ts）
    {
      name: 'chromium-no-webgpu',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          args: ['--disable-features=WebGPU,WebGPUDeveloperFeatures']
        }
      },
      testMatch: 'wasm-fallback.spec.ts'
    }
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI
  }
});
