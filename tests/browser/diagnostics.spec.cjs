const { test, expect } = require('@playwright/test');
const { readFile } = require('node:fs/promises');

for (const theme of ['light', 'dark']) {
  test(`diagnostics filter, refresh and full download work in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/settings/advanced');
    const log = page.locator('#diagnostic-log');
    await expect(log.getByText('Photoframe started', { exact: true })).toBeVisible();
    await expect(log.getByText(/times in UTC/)).toBeVisible();
    await log.locator('#diagnostic-level').selectOption('errors');
    await log.getByRole('button', { name: 'Refresh log' }).click();
    await expect(log.getByText('No recent events recorded matching this filter.')).toBeVisible();
    await expect(log.getByText('Photoframe started', { exact: true })).toHaveCount(0);

    // Export retains the complete bounded history even while the view is filtered.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      log.getByRole('link', { name: 'Download log' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('photoframe-diagnostics.txt');
    const exported = await readFile(await download.path(), 'utf8');
    expect(exported).toContain('app.started: Photoframe started');
    expect(exported).toContain('UTC, newest first');

    await log.locator('#diagnostic-level').selectOption('all');
    await log.getByRole('button', { name: 'Refresh log' }).click();
    await expect(log.getByText('Photoframe started', { exact: true })).toBeVisible();
    const width = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(width.content).toBeLessThanOrEqual(width.viewport + 1);
    expect(errors).toEqual([]);
  });

  test(`failed diagnostics refresh retains results and can recover in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.goto('/settings/advanced');
    const log = page.locator('#diagnostic-log');
    await expect(log.getByText('Photoframe started', { exact: true })).toBeVisible();
    const previous = await log.locator('ol').innerText();
    await page.route('**/partials/diagnostics*', route => route.abort());
    await log.getByRole('button', { name: 'Refresh log' }).click();
    await expect(log.locator('#diagnostic-load-error')).toBeVisible();
    expect(await log.locator('ol').innerText()).toBe(previous);

    await page.unroute('**/partials/diagnostics*');
    await log.getByRole('button', { name: 'Refresh log' }).click();
    await expect(log.locator('#diagnostic-load-error')).toBeHidden();
    await expect(log.getByText('Photoframe started', { exact: true })).toBeVisible();
  });
}
