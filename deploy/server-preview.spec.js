import { test, expect } from '@playwright/test';
test('file uploaded on one browser is previewed and downloaded on a separate browser', async ({ browser, page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/cloud/');
  await expect(page.getByRole('heading', { name: 'Мои файлы', exact: true })).toBeVisible();
  await expect(page.locator('.local-tag')).toContainText('На сервере');
  await page.locator('#file-input').setInputFiles({ name: 'server-file.txt', mimeType: 'text/plain', buffer: Buffer.from('Stored on the server') });
  await expect(page.locator('.upload-item').filter({ hasText: 'server-file.txt' })).toContainText('Загружен');
  const context = await browser.newContext();
  const other = await context.newPage();
  await other.goto('http://127.0.0.1:4178/cloud/');
  await other.locator('.file-name').filter({ hasText: 'server-file.txt' }).click();
  await expect(other.locator('#text-preview')).toHaveText('Stored on the server');
  const download = other.waitForEvent('download');
  await other.getByRole('button', { name: 'Скачать файл', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('server-file.txt');
  await other.getByLabel('Закрыть предпросмотр').click();
  await other.getByLabel('Действия: server-file.txt').click();
  await other.getByRole('menuitem', { name: 'В корзину', exact: true }).click();
  await expect(other.locator('.toast')).toContainText('Перемещено в корзину');
  await page.reload();
  await expect(page.locator('.file-name').filter({ hasText: 'server-file.txt' })).toHaveCount(0);
  await page.locator('[data-nav="trash"]').click();
  await expect(page.locator('.file-name').filter({ hasText: 'server-file.txt' })).toBeVisible();
  expect(errors).toEqual([]);
  await context.close();
});
test('queue resumes after a reload from the confirmed chunk and rejects a different source file', async ({ page }) => {
  const data=Buffer.alloc(4*1024*1024+100,65);
  await page.goto('/cloud/');
  await page.route('**/uploads/*/chunk?offset=4194304',route=>route.abort('internetdisconnected'));
  await page.locator('#file-input').setInputFiles({name:'resume.bin',mimeType:'application/octet-stream',buffer:data});
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('hxry-upload-queue-v1')).find(q=>q.name==='resume.bin')?.offset)).toBe(4194304);
  await expect(page.locator('.file-name').filter({hasText:'resume.bin'})).toHaveCount(0);
  await page.reload();
  await page.unroute('**/uploads/*/chunk?offset=4194304');
  const row=page.locator('.upload-item').filter({hasText:'resume.bin'});
  await expect(row.getByRole('button',{name:'Выбрать файл'})).toBeVisible();
  let chooser=page.waitForEvent('filechooser'); await row.getByRole('button',{name:'Выбрать файл'}).click();
  const wrong=Buffer.from(data);wrong[0]=66;
  await (await chooser).setFiles({name:'resume.bin',mimeType:'application/octet-stream',buffer:wrong});
  await expect(row).toContainText('Выбран другой файл');
  const offsets=[];
  page.on('request',request=>{const match=request.url().match(/\/chunk\?offset=(\d+)/);if(match)offsets.push(Number(match[1]));});
  chooser=page.waitForEvent('filechooser');await row.getByRole('button',{name:'Выбрать файл'}).click();
  await (await chooser).setFiles({name:'resume.bin',mimeType:'application/octet-stream',buffer:data});
  await expect(row).toContainText('Загружен');
  expect(offsets).toEqual([4194304]);
  await expect(page.locator('.file-name').filter({hasText:'resume.bin'})).toBeVisible();
});
test('multiple files expose progress, pause, cancellation and completion independently', async ({ page }) => {
  await page.goto('/cloud/');
  await page.route('**/uploads/*/chunk?offset=*',async route=>{
    await new Promise(resolve=>setTimeout(resolve,200));
    try {await route.continue();} catch {}
  });
  const buffer=Buffer.alloc(8*1024*1024+30,67);
  await page.locator('#file-input').setInputFiles([
    {name:'queue-first.bin',mimeType:'application/octet-stream',buffer},
    {name:'queue-second.bin',mimeType:'application/octet-stream',buffer},
    {name:'queue-third.txt',mimeType:'text/plain',buffer:Buffer.from('third')},
  ]);
  const first=page.locator('.upload-item').filter({hasText:'queue-first.bin'});
  const second=page.locator('.upload-item').filter({hasText:'queue-second.bin'});
  await expect(page.locator('.upload-item')).toHaveCount(3);
  await expect(first.locator('progress')).toBeVisible();
  await first.getByRole('button',{name:'Пауза',exact:true}).click();
  await expect(first).toContainText('Пауза');
  await second.getByRole('button',{name:'Отменить',exact:true}).click();
  await expect(second).toContainText('Отменён');
  await expect(page.locator('.upload-item').filter({hasText:'queue-third.txt'})).toContainText('Загружен');
  await first.getByRole('button',{name:'Продолжить',exact:true}).click();
  await expect(first).toContainText('Загружен');
  await expect(first.locator('progress')).toHaveAttribute('value','100');
  await expect(page.locator('.file-name').filter({hasText:'queue-second.bin'})).toHaveCount(0);
});
