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
      captions: [...gallery.querySelectorAll('[data-gallery-caption]')].map(caption => {
        const filename = caption.querySelector('[data-gallery-filename]');
        return {
          height: caption.getBoundingClientRect().height,
          itemHeight: Math.max(...[...caption.children].map(el => el.getBoundingClientRect().height)),
          right: caption.getBoundingClientRect().right,
          filenameStyle: getComputedStyle(filename).textOverflow,
        };
      }),
    };
  });
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1);
  expect(layout.columns).toBe(layout.width < 768 ? 2 : 3);
  for (const caption of layout.captions) {
    expect(caption.height).toBeLessThanOrEqual(caption.itemHeight + 1);
    expect(caption.right).toBeLessThanOrEqual(layout.width + 1);
    expect(caption.filenameStyle).toBe('ellipsis');
  }
}

for (const theme of ['light', 'dark']) {
  test(`gallery keeps framing, keyboard focus and hidden-photo recovery in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.goto('/photos');
    let tile = page.locator('[data-gallery-photo="portrait"]');
    await expect(tile).toBeVisible();
    await fitsGallery(page);
    const filename = await tile.locator('[data-gallery-filename]').getAttribute('title');
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
      await expect(editor.locator('#selected-title')).toHaveText(filename);
      await editor.locator('#selected-fit').selectOption('fit');
      await editor.locator('#selected-matte').selectOption('black');
      await editor.getByRole('button', { name: 'Save framing', exact: true }).click();
      await expect(page.locator('[data-gallery-photo="portrait"] [data-gallery-caption] .badge')).toHaveText('Whole photo');
      await expect(editor.locator('#selected-fit')).toHaveValue('fit');
      await page.keyboard.press('Escape');
      await expect(editor).toBeHidden();
      tile = page.locator('[data-gallery-photo="portrait"]');
      button = tile.getByRole('button');
      await expect(button).toBeFocused();
      expect(Math.abs(await page.evaluate(() => scrollY) - scrollBefore)).toBeLessThanOrEqual(3);
      await expect(button).toHaveAccessibleName(/Custom framing · Whole photo · Black/);

      // Tapping the photo reopens the saved layout; the caption itself is not a control.
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

  test(`gallery handles long captions and accessible missing thumbnails in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.route('**/thumbnail/portrait*', route => route.abort());
    await page.goto('/photos');
    const tile = page.locator('[data-gallery-photo="portrait"]');
    await tile.scrollIntoViewIfNeeded();
    const button = tile.getByRole('button');
    await expect(tile.locator('.thumbnail-error')).toBeVisible();
    await expect(button).toHaveAccessibleDescription('Preview unavailable');
    await expect(button.locator('button')).toHaveCount(0);
    await tile.locator('[data-gallery-filename]').evaluate(el => { el.textContent = 'An extremely long filename with no gaps '.repeat(20) + 'unbrokentext'.repeat(30); });
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
}
