import { test, expect } from '@playwright/test';
test('production build works at /cloud/ and preserves a local file', async ({ page }) => {
  const errors = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (/\.(js|css|woff2?)(\?|$)/.test(response.url())) assets.push({ url: response.url(), status: response.status() });
  });
  await page.goto('/cloud/');
  await expect(page.getByRole('heading', { name: 'Мои файлы', exact: true })).toBeVisible();
  await page.locator('#file-input').setInputFiles({ name: 'deployment.txt', mimeType: 'text/plain', buffer: Buffer.from('deployment check') });
  await expect(page.locator('.file-name').filter({ hasText: 'deployment.txt' })).toBeVisible();
  // The toast is shown after IndexedDB has committed the upload.
  await expect(page.locator('.toast')).toBeVisible();
  await page.reload();
  await page.locator('.file-name').filter({ hasText: 'deployment.txt' }).click();
  await expect(page.locator('#text-preview')).toHaveText('deployment check');
  expect(errors).toEqual([]);
  expect(assets.some(asset => asset.url.endsWith('.js'))).toBe(true);
  for (const asset of assets) {
    expect(new URL(asset.url).pathname).toMatch(/^\/cloud\/assets\//);
    expect([200, 304]).toContain(asset.status);
  }
});
