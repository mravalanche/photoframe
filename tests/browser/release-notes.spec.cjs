const { test, expect } = require('@playwright/test');

const notes = [
  '# Photoframe 2.1.0', '', 'A **clearer update** with `verified` notes.', '',
  '## Improvements', '- Browse more photos.', '- Keep the frame unchanged while previewing.', '',
  '## Try this release', '1. Review the changes.', '2. Download and verify.', '',
  `[Details](https://example.com/${'release-details-'.repeat(30)})`, '',
  '```text', `A-long-code-line-${'abcdef'.repeat(60)}`, '```',
].join('\n');

async function readableNotes(container, page) {
  await expect(container.locator('h3')).toHaveText('Photoframe 2.1.0');
  const styles = await container.evaluate(notes => {
    const css = selector => getComputedStyle(notes.querySelector(selector));
    const pre = notes.querySelector('pre');
    return {
      headingSize: parseFloat(css('h3').fontSize), subheadingSize: parseFloat(css('h4').fontSize),
      headingWeight: css('h3').fontWeight, font: css('h3').fontFamily,
      paragraphSize: parseFloat(css('p').fontSize), paragraphMargin: parseFloat(css('p').marginTop),
      unordered: css('ul').listStyleType, ordered: css('ol').listStyleType,
      indent: parseFloat(css('ul').paddingInlineStart), itemGap: parseFloat(css('li + li').marginTop),
      underline: css('a').textDecorationLine, codeBackground: css('code').backgroundColor,
      preWrap: css('pre').whiteSpace, preWidth: pre.clientWidth, preScrollWidth: pre.scrollWidth,
      right: notes.getBoundingClientRect().right, width: document.documentElement.clientWidth,
      pageScrollWidth: document.documentElement.scrollWidth,
    };
  });
  expect(styles.headingSize).toBeGreaterThan(styles.subheadingSize);
  expect(styles.subheadingSize).toBeGreaterThan(styles.paragraphSize);
  expect(styles.headingWeight).toBe('600');
  expect(styles.font).toContain('Inter');
  expect(styles.paragraphMargin).toBeGreaterThan(0);
  expect(styles.unordered).toBe('disc');
  expect(styles.ordered).toBe('decimal');
  expect(styles.indent).toBeGreaterThanOrEqual(20);
  expect(styles.itemGap).toBeGreaterThan(0);
  expect(styles.underline).toContain('underline');
  expect(styles.codeBackground).not.toBe('rgba(0, 0, 0, 0)');
  expect(styles.preWrap).toBe('pre-wrap');
  expect(styles.preScrollWidth).toBeLessThanOrEqual(styles.preWidth + 1);
  expect(styles.right).toBeLessThanOrEqual(styles.width + 1);
  expect(styles.pageScrollWidth).toBeLessThanOrEqual(styles.width + 1);
  const link = container.getByRole('link', { name: 'Details' });
  await link.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(link).toBeFocused();
  expect(await link.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(2);
}

for (const theme of ['light', 'dark']) {
  test(`latest and selected release notes remain readable in ${theme}`, async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem('photoframe-theme', theme), theme);
    await page.route('**/api/updates/status', route => route.fulfill({ json: {
      managed: true, phase: 'idle', channel: 'stable', running_version: '2.0.0',
      release_selection_supported: true, latest_manifest: { version: '2.1.0', release_notes: notes },
    } }));
    await page.route('**/api/updates/session', route => route.fulfill({ json: { csrf: 'test' } }));
    await page.route('**/api/updates/catalog', route => route.fulfill({ json: {
      channel: 'stable', releases: [{ version: '2.1.0', available: true, relationship: 'newer' }],
    } }));
    await page.route('**/api/updates/inspect', route => route.fulfill({ json: {
      channel: 'stable', manifest: { version: '2.1.0', updater_protocol: 1,
        settings_schema_min: 2, settings_schema_max: 2, rollback_schema_min: 2,
        rollback_schema_max: 2, release_notes: notes },
    } }));
    await page.goto('/settings/updates');
    await expect(page.locator('#release-notes-section')).toBeVisible();
    await page.locator('#release-notes-section > summary').click();
    await readableNotes(page.locator('#release-notes'), page);
    await page.locator('#release-picker > summary').click();
    await page.locator('#load-releases').click();
    await expect(page.locator('#inspect-release')).toBeEnabled();
    await page.locator('#inspect-release').click();
    await readableNotes(page.locator('#selected-release-notes'), page);
  });
}
