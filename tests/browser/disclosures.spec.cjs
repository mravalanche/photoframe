const { test, expect } = require('@playwright/test');

async function fitsViewport(page, context) {
  await page.waitForTimeout(100); // Allow disclosure and responsive layout transitions to settle.
  const overflow = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    return {
      width, scrollWidth: document.documentElement.scrollWidth,
      offenders: [...document.querySelectorAll('details[open], details[open] .collapse-content, [data-album-picker], [data-album-choice], nav, select')]
        .filter(el => el.getClientRects().length)
        .filter(el => el.getBoundingClientRect().right > width + 1 || el.getBoundingClientRect().left < -1)
        .map(el => `${el.tagName}#${el.id}.${el.className}`),
    };
  });
  expect(overflow.scrollWidth, `${context}: ${JSON.stringify(overflow)}`).toBeLessThanOrEqual(overflow.width + 1);
  expect(overflow.offenders, context).toEqual([]);
}

for (const path of ['/', '/photos', '/settings/provider', '/settings/hardware', '/settings/updates', '/settings/advanced', '/ui/showcase']) {
  test(`all disclosures and menus fit on ${path}`, async ({ page }) => {
    await page.route('**/api/updates/status', route => route.fulfill({ json: {
      managed: true, phase: 'idle', running_version: '2.0.0', channel: 'stable',
      release_selection_supported: true, latest_manifest: {
        version: '2.1.0', release_notes: '# Release notes\nA representative published release with notes.'
      }
    }}));
    await page.route('**/api/updates/session', route => route.fulfill({ json: { csrf: 'browser-test' } }));
    await page.route('**/api/updates/catalog', route => route.fulfill({ json: {
      channel: 'stable', releases: [{version: '2.1.0', channel: 'stable', relationship: 'newer',
        available: true, bundle_available: true, published_at: '2026-10-28T12:00:00Z'}]
    }}));
    await page.goto(path);
    if (path === '/settings/updates') await expect(page.locator('#release-notes-section')).toBeVisible();
    await expect(page.locator('main')).toBeVisible();
    await fitsViewport(page, `${path} closed`);
    const details = page.locator('details');
    const count = await details.count();
    for (let i = 0; i < count; i++) {
      const detail = details.nth(i);
      if (!(await detail.isVisible())) continue;
      // Open ancestors first for nested disclosures; activate the actual summary.
      await detail.evaluate(el => {
        for (let parent = el.parentElement; parent; parent = parent.parentElement) {
          if (parent.tagName === 'DETAILS') parent.open = true;
        }
      });
      if (!(await detail.getAttribute('open') !== null)) await detail.locator(':scope > summary').click();
      await expect(detail).toHaveAttribute('open', '');
      if (await detail.getAttribute('id') === 'release-picker') {
        await detail.locator('#load-releases').click();
        await expect(page.locator('#release-choice')).toBeEnabled();
      }
      await fitsViewport(page, `${path} disclosure ${i}`);
      // Native select popups are rendered by the browser/OS; activate each enabled menu
      // and dismiss it, checking that its control and containing layout still fit.
      const selects = detail.locator('select:visible:enabled');
      for (let j = 0; j < await selects.count(); j++) {
        await selects.nth(j).focus();
        await page.keyboard.press('Alt+ArrowDown');
        await page.keyboard.press('Escape');
        await fitsViewport(page, `${path} disclosure ${i} select ${j}`);
      }
    }
    // Check controls outside disclosures too (provider, hardware and update menus).
    const menus = page.locator('select:visible:enabled');
    for (let i = 0; i < await menus.count(); i++) {
      await menus.nth(i).focus();
      await page.keyboard.press('Alt+ArrowDown');
      await page.keyboard.press('Escape');
      await fitsViewport(page, `${path} menu ${i}`);
    }
  });
}

test('album picker fits with long names and thumbnails', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#album-settings')).toBeVisible();
  await page.locator('#album-settings > summary').click();
  await page.locator('.album-copy').evaluateAll(elements => elements.forEach(el => {
    el.firstChild.textContent = 'A very long album name '.repeat(12) + 'UnbrokenAlbumName'.repeat(12);
  }));
  await fitsViewport(page, 'long album picker');
  await expect(page.locator('[data-album-choice]').first()).toBeVisible();
  await page.locator('[data-album-choice]').first().click();
  await fitsViewport(page, 'selected long album');
});

test('photo framing dialog and its menus fit', async ({ page }) => {
  await page.goto('/photos');
  await page.locator('#photo-gallery button[type="submit"]').first().click();
  const editor = page.locator('#photo-editor-dialog');
  await expect(editor).toBeVisible();
  await fitsViewport(page, 'photo framing dialog');
  const menus = editor.locator('select:visible:enabled');
  expect(await menus.count()).toBeGreaterThan(0);
  for (let i = 0; i < await menus.count(); i++) {
    await menus.nth(i).focus();
    await page.keyboard.press('Alt+ArrowDown');
    await page.keyboard.press('Escape');
    await fitsViewport(page, `photo framing menu ${i}`);
  }
  await editor.getByRole('button', { name: 'Close photo editor', exact: true }).click();
  await expect(editor).not.toBeVisible();
});

test('conditional schedule menus fit when revealed', async ({ page }) => {
  await page.goto('/');
  await page.locator('#schedule-settings > summary').click();
  for (const mode of ['daily', 'weekly', 'interval']) {
    await page.locator('#frame-schedule-mode').selectOption(mode);
    if (mode === 'interval') await page.locator('[data-interval-custom]').check();
    await fitsViewport(page, `${mode} schedule`);
    const menus = page.locator('#schedule-settings select:visible:enabled');
    for (let i = 0; i < await menus.count(); i++) {
      await menus.nth(i).focus();
      await page.keyboard.press('Alt+ArrowDown');
      await page.keyboard.press('Escape');
      await fitsViewport(page, `${mode} schedule menu ${i}`);
    }
  }
});
