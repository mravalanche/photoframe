const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser',
  // Browser projects share one mutable demo runtime (preview, layouts and hidden photos).
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  projects: [320, 390, 768, 1280].map(width => ({
    name: `chromium-${width}`,
    use: { browserName: 'chromium', viewport: { width, height: 900 } },
  })),
  webServer: {
    command: 'uv run python tests/browser_server.py',
    url: 'http://127.0.0.1:8765/health/live',
    reuseExistingServer: false,
  },
});
