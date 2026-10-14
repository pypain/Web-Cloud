import { request, serverStorage, setSessionAuth } from './server-storage.js';
export let account = null;
const base=import.meta.env.BASE_URL;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gb=1024**3;
const pretty=n=>`${(n/gb).toLocaleString('ru',{maximumFractionDigits:2})} ГБ`;
const put=(path,data)=>request(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function shell(title,body,admin=false){
 document.querySelector('#app').innerHTML=`<main class="account-shell ${admin?'admin-shell':''}"><header class="account-header"><a href="${base}" class="account-brand">Личное облако</a><nav>${account?`<a href="${base}">Мои файлы</a>${account.role==='admin'?`<a href="${base}admin/">Админка</a>`:''}<button id="account-logout" class="button secondary">Выйти</button>`:''}</nav></header><div class="account-body"><span class="eyebrow">${admin?'УПРАВЛЕНИЕ ОБЛАКОМ':'ЛИЧНОЕ ПРОСТРАНСТВО'}</span><h1>${title}</h1>${body}</div><div id="account-status" role="status" class="account-status"></div></main>`;
 document.querySelector('#account-logout')?.addEventListener('click',logout);
}
function error(target,message){target.textContent=message;}
export async function logout(){
 if(!confirm('Выйти из аккаунта? Незавершённые загрузки можно продолжить после повторного входа и выбора исходных файлов.'))return;
 try{await put('auth/logout',{});location.replace(base+'auth/');}
 catch(e){if(e.status===401){location.replace(base+'auth/');return;}error(document.querySelector('#account-status')||document.querySelector('#toast-root'),e.message);}
}
function authPage(change=false){
 if(change&&account){
  shell('Сменить пароль',`<form id="password-form" class="auth-card"><p>После смены пароля все сеансы будут завершены.</p><label for="current-password">Текущий пароль</label><input class="text-input" id="current-password" type="password" autocomplete="current-password" required maxlength="256"><label for="new-password">Новый пароль</label><input class="text-input" id="new-password" type="password" autocomplete="new-password" required minlength="12" maxlength="256"><label for="repeat-password">Повторите пароль</label><input class="text-input" id="repeat-password" type="password" autocomplete="new-password" required minlength="12" maxlength="256"><p class="field-note">От 12 символов. Используйте уникальный пароль.</p><p id="auth-error" role="alert" class="form-error"></p><button class="button primary" type="submit">Сохранить новый пароль</button></form>`);
  document.querySelector('#password-form').onsubmit=async event=>{
   event.preventDefault();const form=event.currentTarget,button=form.querySelector('button');const password=document.querySelector('#new-password').value;
   if(password!==document.querySelector('#repeat-password').value)return error(document.querySelector('#auth-error'),'Пароли не совпадают.');
   button.disabled=true;
   try{await put('auth/password',{currentPassword:document.querySelector('#current-password').value,password});location.replace(base+'auth/?changed=1');}
   catch(e){error(document.querySelector('#auth-error'),e.message);button.disabled=false;}
  };return;
 }
 shell('Вход в архив',`<form id="login-form" class="auth-card"><p>${new URLSearchParams(location.search).has('changed')?'Пароль изменён. Войдите снова.':'Войдите, чтобы открыть ваши файлы.'}</p><label for="login-name">Логин</label><input class="text-input" id="login-name" autocomplete="username" autocapitalize="none" spellcheck="false" required maxlength="32"><label for="login-password">Пароль</label><input class="text-input" id="login-password" type="password" autocomplete="current-password" required maxlength="256"><label class="password-toggle"><input id="show-password" type="checkbox"> Показать пароль</label><p id="auth-error" role="alert" class="form-error"></p><button type="submit" class="button primary full">Войти</button><p class="field-note">Аккаунты создаёт администратор. Для восстановления доступа обратитесь к нему.</p></form>`);
 document.querySelector('#show-password').onchange=event=>{document.querySelector('#login-password').type=event.target.checked?'text':'password';};
 document.querySelector('#login-form').onsubmit=async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;error(document.querySelector('#auth-error'),'');
  try{await put('auth/login',{username:document.querySelector('#login-name').value.trim().toLowerCase(),password:document.querySelector('#login-password').value});location.replace(base);}
  catch(e){error(document.querySelector('#auth-error'),e.message);button.disabled=false;}
 };
}
async function adminPage(){
 if(account.role!=='admin'){shell('Доступ закрыт','<p>Этот раздел доступен только администратору.</p>');return;}
 shell('Аккаунты',`<p class="admin-intro">Создавайте личные хранилища и управляйте доступом. Файлы пользователей из админки не открываются.</p><section class="admin-create"><h2>Создать аккаунт</h2><form id="create-user"><label>Логин<input class="text-input" name="username" pattern="[a-z0-9][a-z0-9_.\\-]{2,31}" required minlength="3" maxlength="32" autocomplete="off"></label><label>Первый пароль<input class="text-input" name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label><label>Лимит, ГБ<input class="text-input" name="quota" type="number" value="20" min="1" max="100" required></label><label>Роль<select class="text-input" name="role"><option value="user">Пользователь</option><option value="admin">Администратор</option></select></label><button class="button primary">Создать аккаунт</button><p class="form-error" id="create-error" role="alert"></p></form></section><section><h2>Пользователи</h2><div id="admin-users" class="admin-users"></div></section><section class="audit-section"><h2>Журнал действий</h2><button id="refresh-audit" class="button secondary">Обновить журнал</button><div id="admin-audit"></div></section>`,true);
 const refresh=async()=>{
  const {users}=await (await request('admin/users')).json();
  document.querySelector('#admin-users').innerHTML=users.map(user=>`<form class="admin-user" data-user="${user.id}"><div class="admin-user-heading"><h3>${esc(user.username)} ${user.id===account.id?'<small>· ваш аккаунт</small>':''}</h3><span class="account-badge">${user.enabled?'Активен':'Заблокирован'}</span></div><p>${pretty(user.used)} из ${pretty(user.quota)} · ${user.last_login?'Последний вход: '+esc(new Date(user.last_login*1000).toLocaleString('ru')):'Ещё не входил'}</p><div class="admin-fields"><label>Роль<select class="text-input" name="role" ${user.id===account.id?'disabled':''}><option value="user" ${user.role==='user'?'selected':''}>Пользователь</option><option value="admin" ${user.role==='admin'?'selected':''}>Администратор</option></select></label><label>Лимит, ГБ<input class="text-input" name="quota" type="number" min="1" max="100" step="0.01" value="${user.quota/gb}" required></label><label>Новый пароль (необязательно)<input class="text-input" name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password"></label><label class="password-toggle"><input type="checkbox" name="enabled" ${user.enabled?'checked':''} ${user.id===account.id?'disabled':''}> Аккаунт активен</label><button class="button secondary">Сохранить аккаунт</button></div><p class="field-note">Сброс пароля или смена роли завершат все сеансы. Блокировка также отзовёт публичные ссылки; файлы сохранятся.</p><p class="form-error" role="alert"></p></form>`).join('');
  document.querySelectorAll('.admin-user').forEach(form=>form.onsubmit=async event=>{
   event.preventDefault();const button=form.querySelector('button'),target=form.querySelector('.form-error');button.disabled=true;target.textContent='';
   const data={role:form.elements.role.value,enabled:form.elements.enabled.checked,quota:Math.round(Number(form.elements.quota.value)*gb)};
   if(form.elements.password.value)data.password=form.elements.password.value;
   if((!data.enabled||data.password)&&!confirm('Завершить сеансы этого аккаунта? Блокировка также отзовёт его публичные ссылки.')){button.disabled=false;return;}
   try{await put('admin/users/'+form.dataset.user,data);await refresh();await audit();error(document.querySelector('#account-status'),'Аккаунт обновлён.');}
   catch(e){error(target,e.message);button.disabled=false;}
  });
 };
 const labels={'account.created':'Создание аккаунта','account.updated':'Изменение аккаунта','password.reset':'Сброс пароля','password.changed':'Смена пароля','login.success':'Успешный вход','login.failed':'Неудачная попытка входа'};
 const audit=async()=>{
  const {events}=await(await request('admin/audit')).json();
  document.querySelector('#admin-audit').innerHTML=`<ol class="audit-list">${events.map(event=>`<li><time>${esc(new Date(event.at*1000).toLocaleString('ru'))}</time><span>${esc(event.actor)} · ${esc(labels[event.event]||event.event)} · ${esc(event.target)}</span></li>`).join('')}</ol>`;
 };
 document.querySelector('#refresh-audit').onclick=()=>audit().catch(e=>error(document.querySelector('#account-status'),e.message));
 document.querySelector('#create-user').onsubmit=async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector('button');button.disabled=true;error(document.querySelector('#create-error'),'');
  try{await put('admin/users',{username:form.elements.username.value,password:form.elements.password.value,role:form.elements.role.value,quota:Math.round(Number(form.elements.quota.value)*gb)});form.reset();await refresh();await audit();error(document.querySelector('#account-status'),'Аккаунт создан. Передайте логин и пароль пользователю лично.');}
  catch(e){error(document.querySelector('#create-error'),e.message);}
  finally{button.disabled=false;}
 };
 await refresh();await audit();
}
export async function accountGate(){
 if(!serverStorage)return false;
 const authPath=location.pathname.replace(/\/$/,'')===base+'auth';
 let result;
 try{result=await(await request('auth/me')).json();}
 catch(e){if(e.status!==401)throw e;if(!authPath){location.replace(base+'auth/');return true;}authPage();return true;}
 if(!result.accounts)return false;
 account=result.user;setSessionAuth(result.user,result.csrf);
 if(authPath){if(new URLSearchParams(location.search).has('password'))authPage(true);else location.replace(base);return true;}
 if(location.pathname.replace(/\/$/,'')===base+'admin'){await adminPage();return true;}
 return false;
}
