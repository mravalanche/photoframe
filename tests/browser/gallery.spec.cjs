const { test, expect } = require('@playwright/test');

// Exercise native keyboard and touch activation in the same existing modal workflow.
// Keep gallery mutations together; restore the chosen photo before each case finishes.
test.describe.configure({ mode: 'serial' });
test.use({ hasTouch: true });

async function fitsGallery(page) {
  const layout = await page.locator('#photo-gallery').evaluate(gallery => {
    const width = document.documentElement.clientWidth;
    return {
      width,
      scrollWidth: document.documentElement.scrollWidth,
      columns: getComputedStyle(gallery).gridTemplateColumns.split(' ').length,
      badges: [...gallery.querySelectorAll('[data-gallery-fit]')].map(badge => {
        const button = badge.closest('button').getBoundingClientRect();
        const rect = badge.getBoundingClientRect();
        return {left: rect.left - button.left, right: button.right - rect.right,
          bottom: button.bottom - rect.bottom,
          badgeHeight: badge.querySelector('.badge').getBoundingClientRect().height};
      }),
    };
  });
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1);
  expect(layout.columns).toBe(layout.width < 640 ? 3 : layout.width < 1024 ? 4 : 5);
  for (const badge of layout.badges) {
    expect(badge.left).toBeGreaterThanOrEqual(0);
    expect(badge.right).toBeGreaterThanOrEqual(0);
    expect(badge.bottom).toBeGreaterThanOrEqual(0);
    expect(badge.badgeHeight).toBeLessThanOrEqual(22);
  }
}

for (const theme of ['light', 'dark']) {
  test(`gallery keeps framing, keyboard focus and hidden-photo recovery in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.goto('/photos');
    let tile = page.locator('[data-gallery-photo="portrait"]');
    await expect(tile).toBeVisible();
    await fitsGallery(page);
    await expect(page.getByText('Reload this album and retry missing photos.', {exact: true})).toHaveCount(0);
    await expect(page.locator('#album-refresh-progress')).toBeHidden();
    const filename = (await tile.getByRole('button').getAttribute('title')).split(' · ')[0];
    await expect(tile).not.toContainText(filename);
    await expect(page.locator('[data-selected-album]')).toContainText('Album ·');
    let button = tile.getByRole('button');
    await expect(button).toHaveAccessibleName(new RegExp(filename.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    await expect(button).toHaveAttribute('aria-haspopup', 'dialog');
    await button.scrollIntoViewIfNeeded();
    await button.focus();
    const scrollBefore = await page.evaluate(() => scrollY);
    await page.keyboard.press('Enter');
    const editor = page.locator('#photo-editor-dialog');
    await expect(editor).toBeVisible();
    const previousFit = await editor.locator('#selected-fit').inputValue();
    const previousMatte = await editor.locator('#selected-matte').inputValue();
    try {
      await expect(editor.locator('#selected-title')).toHaveText('Photo preview');
      await expect(editor.locator('[data-editor-filename]')).toHaveAttribute('title', filename);
      await editor.locator('#selected-fit').selectOption('fit');
      await editor.locator('#selected-matte').selectOption('black');
      await editor.getByRole('button', { name: 'Save framing', exact: true }).click();
      await expect(page.locator('[data-gallery-photo="portrait"] [data-gallery-fit] .badge')).toHaveText('Whole');
      await expect(editor.locator('#selected-fit')).toHaveValue('fit');
      await page.keyboard.press('Escape');
      await expect(editor).toBeHidden();
      tile = page.locator('[data-gallery-photo="portrait"]');
      button = tile.getByRole('button');
      await expect(button).toBeFocused();
      expect(Math.abs(await page.evaluate(() => scrollY) - scrollBefore)).toBeLessThanOrEqual(3);
      await expect(button).toHaveAccessibleName(/Custom framing · Whole photo · Black/);

      // Tapping the thumbnail reopens its saved layout.
      await button.tap();
      await expect(editor).toBeVisible();
      await expect(editor.locator('#selected-fit')).toHaveValue('fit');
      await expect(editor.locator('#selected-matte')).toHaveValue('black');
      await editor.getByRole('button', { name: 'Hide from this frame', exact: true }).click();
      await expect(page.locator('[data-gallery-photo="portrait"]')).toHaveCount(0);
      await page.getByRole('button', { name: 'Undo', exact: true }).click();
      await expect(page.locator('[data-gallery-photo="portrait"]')).toBeVisible();
      await page.locator('#photo-layouts > summary').click();
      await expect(page.locator('#all-photo-layouts')).toBeVisible();
      await page.locator('#all-photo-layouts > summary').click();
      await expect(page.locator('#all-photo-fit')).toBeEnabled();
      await fitsGallery(page);
    } finally {
      const headers = { Origin: new URL(page.url()).origin };
      await page.request.post('/photo/unhide', { headers, form: { photo_id: 'portrait' } });
      await page.request.post('/photo/framing', { headers, form: { photo_id: 'portrait', fit_mode: previousFit, matte: previousMatte } });
      await page.request.post('/photo/preview/clear', { headers });
    }
  });

  test(`gallery restores focus when the editor closes before HTMX settles in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.goto('/photos');
    const button = page.locator('[data-gallery-photo="coast"] button');
    await expect(button).toBeVisible();
    await page.evaluate(() => {
      window.galleryQuickCloseSettled = new Promise(resolve => {
        document.addEventListener('htmx:afterSwap', () => {
          document.querySelector('#photo-editor-dialog')?.close();
        }, { once: true });
        document.addEventListener('htmx:afterSettle', resolve, { once: true });
      });
    });
    await button.click();
    await page.evaluate(() => window.galleryQuickCloseSettled);
    await expect(page.locator('#photo-editor-dialog')).toBeHidden();
    await expect(button).toBeFocused();
  });

  test(`gallery preserves accessible filenames and missing-thumbnail recovery in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.route('**/thumbnail/portrait*', route => route.abort());
    await page.goto('/photos');
    const tile = page.locator('[data-gallery-photo="portrait"]');
    await tile.scrollIntoViewIfNeeded();
    const button = tile.getByRole('button');
    await expect(tile.locator('.thumbnail-error')).toBeVisible();
    await expect(button).toHaveAccessibleDescription('Preview unavailable');
    await expect(button.locator('button')).toHaveCount(0);
    await button.evaluate(el => { el.setAttribute('aria-label', 'Preview ' + 'A very long filename '.repeat(60)); });
    await fitsGallery(page);
    await button.tap();
    await expect(page.locator('#photo-editor-dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(button).toBeFocused();
    await expect(page.getByRole('button', { name: 'Refresh photos & thumbnails' })).toBeVisible();
    await page.unroute('**/thumbnail/portrait*');
    await page.reload();
    await expect(page.locator('[data-gallery-photo="portrait"] img')).toBeVisible();
    await expect(page.locator('[data-gallery-photo="portrait"] .thumbnail-error')).toHaveCount(0);
    await expect(page.locator('[data-gallery-photo="portrait"] button')).not.toHaveAttribute('aria-describedby');
  });
  test(`editor has one persistent close control and supports backdrop dismissal in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.goto('/photos');
    const opener = page.locator('[data-gallery-photo="coast"] button');
    await opener.click();
    const editor = page.locator('#photo-editor-dialog');
    const close = editor.getByRole('button', { name: 'Close photo editor', exact: true });
    await expect(editor.locator('[data-photo-editor-close]')).toHaveCount(1);
    await expect(editor.locator('.modal-box > header')).toContainText('Photo preview');
    expect((await editor.locator('.modal-box > header').boundingBox()).height).toBeLessThanOrEqual(60);
    await expect(editor.locator('section.card')).toHaveCount(0);
    await editor.locator('.modal-box').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(close).toBeInViewport();
    const positions = await close.evaluate(async button => {
      const panel = button.closest('.modal-box');
      await Promise.all(panel.getAnimations().map(animation => animation.finished.catch(() => {})));
      await document.fonts.ready;
      const positions = [];
      for (let frame = 0; frame < 12; frame++) {
        await new Promise(requestAnimationFrame);
        const {x, y, width, height} = button.getBoundingClientRect();
        positions.push([x, y, width, height]);
      }
      return [...new Set(positions.map(position => JSON.stringify(position)))];
    });
    // A scrollbar-gutter/scroll-lock feedback loop previously moved the X every frame.
    expect(positions).toHaveLength(1);
    const box = await close.boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
    await close.click();
    await expect(editor).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await expect(editor).toBeVisible();
    const title = await editor.locator('#selected-title').boundingBox();
    await page.mouse.move(title.x + 5, title.y + 5);
    await page.mouse.down();
    await page.mouse.move(1, 1);
    await page.mouse.up();
    await expect(editor).toBeVisible();
    await page.mouse.click(1, 1);
    await expect(editor).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test(`manual preview notice stays quiet after polling and navigation in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/photos');
    const headers = { Origin: new URL(page.url()).origin };
    await page.request.post('/photo/preview', { headers, form: { photo_id: 'square' } });
    await page.goto('/');
    const notice = page.locator('[data-manual-preview-notice]');
    await expect(notice).toBeVisible();
    await expect(notice).toBeHidden({ timeout: 6000 });
    await expect(page.locator('[data-preview-marker]')).toHaveText('↗ Preview pending');
    await expect(page.locator('.frame-canvas [data-frame-transition="manual-preview"]')).toHaveCount(0);
    await page.evaluate(() => htmx.ajax('GET', '/partials/frame-status', {target: '#frame-status', swap: 'outerHTML'}));
    await expect(notice).toBeHidden();
    await page.reload();
    await expect(notice).toBeHidden();
    await page.request.post('/photo/preview', { headers, form: { photo_id: 'portrait' } });
    await page.reload();
    await expect(notice).toBeVisible();
    await page.getByRole('button', {name: 'Clear selected preview', exact: true}).click();
    await expect(page.locator('[data-preview-marker]')).toHaveCount(0);
    await expect(page.locator('#frame-status')).toBeFocused();
    await page.reload();
    await expect(page.locator('[data-preview-marker]')).toHaveCount(0);
    await page.goto('/photos');
    await expect(page.locator('#photo-editor-dialog')).toBeHidden();
  });

}


test('gallery keeps its real grid and corner badges behind a forwarded backend host', async ({ page }) => {
  await page.route('**/photos', async route => {
    // Emulate a port forward that passes the VM host rather than the browser origin.
    const response = await route.fetch({ headers: {
      ...route.request().headers(), host: 'internal-frame.invalid:8765',
    } });
    await route.fulfill({ response });
  });
  await page.goto('/photos');
  await expect(page.locator('#photo-gallery')).toBeVisible();
  await fitsGallery(page);
  const placement = await page.locator('[data-gallery-photo="coast"] button').evaluate(button => {
    const image = button.getBoundingClientRect();
    const badge = button.querySelector('[data-gallery-fit] .badge').getBoundingClientRect();
    return {left: badge.left - image.left, bottom: image.bottom - badge.bottom,
      imageWidth: image.width, badgeWidth: badge.width};
  });
  expect(placement.left).toBeLessThanOrEqual(8);
  expect(placement.bottom).toBeLessThanOrEqual(8);
  expect(placement.badgeWidth).toBeLessThan(placement.imageWidth);
});


test('HTMX album refresh progress occupies space only while refreshing', async ({ page }) => {
  // The normal activity coordinator owns a pinned progress tray. Exercise the
  // inline HTMX fallback as well, when that optional script cannot load.
  await page.route('**/static/activity.js*', route => route.abort());
  await page.goto('/photos');
  await expect(page.locator('#photo-gallery')).toBeVisible();
  const refresh = page.locator('[data-album-refresh-form]');
  const progress = page.locator('#album-refresh-progress');
  await expect(progress).toBeHidden();
  expect((await refresh.boundingBox()).height).toBeLessThanOrEqual(44);
  let finishRefresh;
  await page.route('**/album/refresh', async route => {
    await new Promise(resolve => { finishRefresh = resolve; });
    await route.continue();
  });
  await refresh.getByRole('button').click();
  await expect(progress).toBeVisible();
  finishRefresh();
  await expect(progress).toBeHidden();
  await expect(refresh.getByRole('button')).toBeEnabled();
});
