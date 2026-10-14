import { test, expect } from '@playwright/test';

const textFile = { name: 'Заметки.txt', mimeType: 'text/plain', buffer: Buffer.from('Планы на осень\nСохранить важное.') };

test('profile settings persist without changing the file archive', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.file-row')).toHaveCount(4);
  await page.locator('#file-input').setInputFiles(textFile);
  await expect(page.getByRole('region', { name: 'Загрузки' })).toContainText('Всё на своём месте');
  await page.getByLabel('Открыть профиль', { exact: true }).click();
  await page.getByLabel('Ваше имя', { exact: true }).fill('Анна Лесная');
  await page.getByLabel('Название архива', { exact: true }).fill('Тихий архив');
  await page.getByRole('button', { name: 'Сохранить изменения' }).click();
  await expect(page.locator('.profile-card h2')).toHaveText('Анна Лесная');
  await page.reload();
  await expect(page.locator('.top-location')).toContainText('Тихий архив');
  await expect(page.getByLabel('Открыть профиль')).toHaveText('АЛ');
  await page.getByLabel('Открыть профиль').click();
  await expect(page.getByLabel('Ваше имя', { exact: true })).toHaveValue('Анна Лесная');
  await page.locator('[data-nav="files"]').click();
  await expect(page.locator('.file-row')).toHaveCount(5);
  await page.locator('.file-name').filter({ hasText: 'Заметки.txt' }).click();
  await expect(page.locator('#text-preview')).toHaveText('Планы на осень\nСохранить важное.');
});

test('mobile dock, file selection and preview remain usable at narrow widths', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/');
  await expect(page.locator('.file-row')).toHaveCount(4);
  await page.locator('#file-input').setInputFiles(textFile);
  await page.getByLabel('Выбрать Заметки.txt', { exact: true }).check();
  const selection = page.locator('.selection-bar');
  await expect(selection).toBeVisible();
  const selectionBox = await selection.boundingBox();
  const dockBox = await page.locator('.mobile-dock').boundingBox();
  expect(selectionBox.y + selectionBox.height).toBeLessThan(dockBox.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.getByLabel('Снять выделение').click();
  await page.locator('.file-name').filter({ hasText: 'Заметки.txt' }).click();
  await expect(page.getByRole('dialog', { name: 'Предпросмотр' })).toBeVisible();
  await expect(page.getByLabel('Закрыть предпросмотр')).toBeFocused();
  expect(await page.locator('.mobile-dock').evaluate(el => el.inert)).toBe(true);
  await page.getByRole('button', { name: 'Поделиться', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Поделиться', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Поделиться', exact: true })).not.toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Предпросмотр' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Поделиться', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Закрыть предпросмотр')).toBeFocused();
  await page.getByLabel('Закрыть предпросмотр').click();
  expect(await page.locator('.mobile-dock').evaluate(el => el.inert)).toBe(false);
  expect(await page.locator('.sidebar').evaluate(el => el.inert)).toBe(true);
  await page.getByLabel('Открыть навигацию').click();
  expect(await page.locator('main').evaluate(el => el.inert)).toBe(true);
  await page.getByRole('link', { name: 'Личное облако, главная' }).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('.sidebar .profile')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('Открыть навигацию')).toBeFocused();
  expect(await page.locator('main').evaluate(el => el.inert)).toBe(false);
  await page.getByLabel('Открыть навигацию').click();
  await page.setViewportSize({width:1440,height:1000});
  expect(await page.locator('main').evaluate(el => el.inert)).toBe(false);
  await page.setViewportSize({width:320,height:740});
  await page.locator('[data-mobile-nav="links"]').click();
  await expect(page.getByRole('heading', { name: 'Общие ссылки', exact: true })).toBeVisible();
  await page.locator('.mobile-dock [data-action="settings"]').click();
  await expect(page.getByRole('heading', { name: 'Личный профиль', exact: true })).toBeVisible();
});

test('image tiles, sharing and desktop-to-mobile preview transitions', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  const image = { name: 'Обложка.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><rect width="200" height="120" fill="#d89b7a"/></svg>') };
  await page.locator('#file-input').setInputFiles([textFile, image]);
  await expect(page.locator('.upload-status')).toContainText('2 файла');
  await page.getByLabel('Плитки', { exact: true }).click();
  const tile = page.locator('.file-row').filter({ hasText: 'Обложка.svg' });
  await expect(tile.locator('img')).toBeVisible();
  expect(await tile.locator('img').evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  await tile.locator('.file-name').click();
  await expect(page.locator('.preview-media img')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('dialog', { name: 'Предпросмотр' })).toBeVisible();
  expect(await page.locator('.file-panel').evaluate(el => el.inert)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.locator('.preview-panel')).not.toHaveAttribute('aria-modal');
  expect(await page.locator('.file-panel').evaluate(el => el.inert)).toBe(false);
  await page.locator('.preview-panel [data-share]').click();
  await page.getByRole('button', { name: 'Подготовить к обмену' }).click();
  await page.locator('[data-nav="links"]').click();
  await expect(page.locator('.file-name').filter({ hasText: 'Обложка.svg' })).toBeVisible();
  await expect(page.locator('.notice')).toContainText('публичные ссылки появятся после подключения сервера');
});

test('reduced motion, long names and dialogs across desktop, tablet and phone sizes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('.file-row')).toHaveCount(4);
  await page.locator('#file-input').setInputFiles({ ...textFile, name: 'Очень длинное название документа с важными заметками и планами на новый год.txt' });
  for (const width of [320, 390, 700, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow at ${width}px`).toBe(true);
    await page.getByRole('button', { name: 'Новая папка', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const bounds = await dialog.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
  }
  expect(await page.locator('.page-heading').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  expect(errors).toEqual([]);
});
