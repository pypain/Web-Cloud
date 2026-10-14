import { test, expect } from '@playwright/test';
const base='http://127.0.0.1:4179';
async function login(page,username,password){
 await page.goto('/cloud/auth/');
 await page.getByLabel('Логин',{exact:true}).fill(username);
 await page.getByLabel('Пароль',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Войти',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Мои файлы',exact:true})).toBeVisible();
}
test('login gates private files, preserves original owner files and isolates users',async({browser,page})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/cloud/');await expect(page).toHaveURL(/\/cloud\/auth\/$/);
 expect((await page.request.get('/cloud/api/state')).status()).toBe(401);
 await login(page,'cloudadmin','Admin password 123!');
 await expect(page.locator('.file-name').filter({hasText:'original-owner.txt'})).toBeVisible();
 const cookie=(await page.context().cookies()).find(c=>c.name==='__Secure-hxry_session');expect(cookie.httpOnly).toBe(true);expect(cookie.secure).toBe(true);expect(cookie.sameSite).toBe('Strict');
 await page.locator('.file-name').filter({hasText:'original-owner.txt'}).click();await expect(page.locator('#text-preview')).toHaveText('Original owner file');
 const otherContext=await browser.newContext();const other=await otherContext.newPage();
 await login(other,'alice','Alice password 123!');
 await expect(other.locator('.file-name').filter({hasText:'original-owner.txt'})).toHaveCount(0);
 await other.locator('#file-input').setInputFiles({name:'alice-private.txt',mimeType:'text/plain',buffer:Buffer.from('Alice only')});
 await expect(other.locator('.upload-item').filter({hasText:'alice-private.txt'})).toContainText('Загружен');
 await other.getByRole('button',{name:'Убрать завершённые'}).click();
 await other.locator('.file-name').filter({hasText:'alice-private.txt'}).click();await expect(other.locator('#text-preview')).toHaveText('Alice only');
 await other.goto('/cloud/admin/');await expect(other.getByRole('heading',{name:'Доступ закрыт'})).toBeVisible();
 expect((await other.request.get('/cloud/api/admin/users')).status()).toBe(403);
 const state=await(await other.request.get('/cloud/api/state')).json();const file=state.entries.find(e=>e.name==='alice-private.txt');
 expect((await page.request.get('/cloud/api/files/'+file.id)).status()).toBe(404);
 expect(errors).toEqual([]);await otherContext.close();
});
test('administrator creates an account, adjusts quota, resets password and blocks access',async({browser,page})=>{
 await login(page,'cloudadmin','Admin password 123!');await page.goto('/cloud/admin/');
 const create=page.locator('#create-user');await create.locator('[name=username]').fill('newmember');await create.locator('[name=password]').fill('Member password 123!');await create.locator('[name=quota]').fill('1');await create.getByRole('button',{name:'Создать аккаунт'}).click();
 const card=page.locator('.admin-user').filter({has:page.getByRole('heading',{name:'newmember',exact:true})});await expect(card).toBeVisible();
 const context=await browser.newContext({viewport:{width:390,height:844}});const member=await context.newPage();await login(member,'newmember','Member password 123!');
 await member.locator('.mobile-dock [data-action=settings]').click();await expect(member.locator('.profile-card')).not.toContainText('Управление аккаунтами');
 page.on('dialog',dialog=>dialog.accept());
 await card.locator('[name=password]').fill('Changed member password 123!');await card.locator('[name=quota]').fill('2');await card.getByRole('button',{name:'Сохранить аккаунт'}).click();
 await expect(page.locator('#account-status')).toContainText('Аккаунт обновлён');
 await member.reload();await expect(member).toHaveURL(/\/cloud\/auth\/$/);
 await login(member,'newmember','Changed member password 123!');
 expect((await(await member.request.get('/cloud/api/state')).json()).quota).toBe(2*1024**3);
 await card.locator('[name=enabled]').uncheck();await card.getByRole('button',{name:'Сохранить аккаунт'}).click();await expect(card).toContainText('Заблокирован');
 await member.reload();await expect(member).toHaveURL(/\/cloud\/auth\/$/);
 await member.getByLabel('Логин',{exact:true}).fill('newmember');await member.getByLabel('Пароль',{exact:true}).fill('Changed member password 123!');await member.getByRole('button',{name:'Войти',exact:true}).click();await expect(member.locator('#auth-error')).toHaveText('Неверный логин или пароль.');
 await expect(page.locator('#admin-audit')).toContainText('Сброс пароля');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'/workspace/design-review/accounts/admin-mobile.png',fullPage:true,animations:'disabled'});
 await context.close();
});
test('password change revokes other sessions, account-scoped queue survives reload and login fits mobile',async({browser,page})=>{
 const context=await browser.newContext();const other=await context.newPage();await login(other,'bob','Bob password 123!');
 await login(page,'bob','Bob password 123!');
 await page.route('**/uploads/*/chunk?offset=*',route=>route.abort('internetdisconnected'));
 await page.locator('#file-input').setInputFiles({name:'bob-resume.bin',mimeType:'application/octet-stream',buffer:Buffer.alloc(5*1024*1024,65)});
 await expect(page.locator('.upload-item').filter({hasText:'bob-resume.bin'})).toBeVisible();
 await page.reload();await expect(page.locator('.upload-item').filter({hasText:'bob-resume.bin'}).getByRole('button',{name:'Выбрать файл'})).toBeVisible();
 await page.goto('/cloud/auth/?password=1');await page.getByLabel('Текущий пароль',{exact:true}).fill('Bob password 123!');await page.getByLabel('Новый пароль',{exact:true}).fill('New Bob password 123!');await page.getByLabel('Повторите пароль',{exact:true}).fill('New Bob password 123!');await page.getByRole('button',{name:'Сохранить новый пароль'}).click();
 await expect(page.getByRole('heading',{name:'Вход в архив'})).toBeVisible();await other.reload();await expect(other).toHaveURL(/\/cloud\/auth\/$/);
 for(const width of [320,390,700,1440]){await page.setViewportSize({width,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/workspace/design-review/accounts/login-mobile.png',fullPage:true,animations:'disabled'});
 await context.close();
});
