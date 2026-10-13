import { createIcons, Cloud, FolderClosed, Link, Trash2, HardDrive, ArrowUpRight, Settings2, Menu, CircleHelp, FolderPlus, Upload, Search, X, ArrowDownUp, ChevronDown, List, LayoutGrid, House, ChevronRight, ArrowDown, ArrowUp, Ellipsis, UploadCloud, RotateCcw, Download, FolderInput, Info, Check, CircleAlert, Eye, Pencil, Calendar, AArrowDown, Folder, Image, FileText, Sheet, Archive, Film, Music, FolderOpen, SearchX } from 'lucide';
const icons = { Cloud, FolderClosed, Link, Trash2, HardDrive, ArrowUpRight, Settings2, Menu, CircleHelp, FolderPlus, Upload, Search, X, ArrowDownUp, ChevronDown, List, LayoutGrid, House, ChevronRight, ArrowDown, ArrowUp, Ellipsis, UploadCloud, RotateCcw, Download, FolderInput, Info, Check, CircleAlert, Eye, Pencil, Calendar, AArrowDown, Folder, Image, FileText, Sheet, Archive, Film, Music, FolderOpen, SearchX };
import '@fontsource/manrope/cyrillic-400.css';
import '@fontsource/manrope/cyrillic-500.css';
import '@fontsource/manrope/cyrillic-600.css';
import '@fontsource/manrope/cyrillic-700.css';
import '@fontsource/manrope/latin-400.css';
import '@fontsource/manrope/latin-500.css';
import '@fontsource/manrope/latin-600.css';
import '@fontsource/manrope/latin-700.css';
import './style.css';

const $ = (s, root = document) => root.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, cls = '') => `<i data-lucide="${name}" class="${cls}" aria-hidden="true"></i>`;
const fmt = n => n < 1024 ? `${n} Б` : n < 1048576 ? `${(n/1024).toLocaleString('ru',{maximumFractionDigits:1})} КБ` : `${(n/1048576).toLocaleString('ru',{maximumFractionDigits:1})} МБ`;
const date = n => new Date(n).toLocaleDateString('ru', {day:'numeric',month:'short',year:'numeric'});
let db, entries = [], page = 'files', folder = null, selected = new Set(), preview = null, query = '', sort = 'name', ascending = true, view = 'list', urls = [];
const uid = () => crypto.randomUUID();
const active = () => entries.filter(x => !x.deleted);
const get = id => entries.find(x => x.id === id);
const type = e => e.kind === 'folder' ? 'folder' : e.mime?.startsWith('image/') ? 'image' : e.mime === 'application/pdf' ? 'file-text' : /\.(xlsx|csv)$/i.test(e.name) ? 'sheet' : /\.(zip|rar)$/i.test(e.name) ? 'archive' : e.mime?.startsWith('video/') ? 'film' : e.mime?.startsWith('audio/') ? 'music' : 'file-text';
function fileIcon(e) { return `<span class="file-icon ${type(e)}">${icon(type(e))}</span>`; }
function objectURL(blob) { const u = URL.createObjectURL(blob); urls.push(u); return u; }
async function init() {
  db = await new Promise((resolve,reject) => { const r = indexedDB.open('personal-cloud',1); r.onupgradeneeded = () => r.result.createObjectStore('entries',{keyPath:'id'}); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  entries = await new Promise((resolve,reject) => { const r = db.transaction('entries').objectStore('entries').getAll(); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  if (!localStorage.getItem('cloud-initialized') && !entries.length) {
    const now = Date.now();
    entries = ['Документы','Фотографии','Проекты'].map(name => ({id:uid(),name,kind:'folder',parent:null,modified:now,size:0}));
    const sample = new Blob(['Добро пожаловать в Личное облако!\n\nЗагрузите файлы кнопкой «Загрузить» или перетащите их в окно.\nСоздавайте папки, перемещайте файлы и восстанавливайте их из корзины.\n\nЭта версия хранит данные в браузере на этом устройстве.\nДля доступа с других устройств и публичных ссылок нужен сервер.'],{type:'text/plain'});
    entries.push({id:uid(),name:'Начните здесь.txt',kind:'file',parent:null,modified:now,size:sample.size,mime:sample.type,blob:sample});
    await persist(); localStorage.setItem('cloud-initialized','1');
  }
  render();
}
async function persist() {
  await new Promise((resolve,reject) => { const tx = db.transaction('entries','readwrite'); const store = tx.objectStore('entries'); store.clear(); entries.forEach(e => store.put(e)); tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error); });
}
async function change(fn, message) { const before = structuredClone(entries); try { fn(); await persist(); render(); if(message) toast(message); } catch(e) { entries = before; render(); toast('Не удалось сохранить. Возможно, хранилище браузера заполнено.',true); } }
function visible() {
  let list = entries.filter(e => page === 'trash' ? e.deleted && !get(e.parent)?.deleted : page === 'links' ? !e.deleted && e.shared : !e.deleted && (query ? true : e.parent === folder));
  if(query) list = list.filter(e => e.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return list.sort((a,b) => (a.kind === b.kind ? 0 : a.kind === 'folder' ? -1 : 1) || (sort === 'name' ? a.name.localeCompare(b.name,'ru') : (a[sort]||0)-(b[sort]||0)) * (ascending ? 1 : -1));
}
function ancestors(id) { const path = []; let e = get(id); const seen = new Set(); while(e && !seen.has(e.id)) { path.unshift(e); seen.add(e.id); e = get(e.parent); } return path; }
function descendant(id,parent) { return ancestors(id).some(e => e.id === parent); }
function nav(p) { page=p; folder=null; selected.clear(); preview=null; query=''; render(); }
function openFolder(id) { folder=id; page='files'; query=''; selected.clear(); preview=null; render(); }
function render() {
  urls.forEach(URL.revokeObjectURL); urls=[];
  const list=visible(), p=get(preview), used=entries.reduce((a,e)=>a+(e.size||0),0), count=active().filter(e=>e.kind==='file').length;
  const titles={files:'Мои файлы',links:'Общие ссылки',trash:'Корзина'};
  $('#app').innerHTML=`<div class="layout">
    <aside class="sidebar" id="sidebar"><a class="brand" href="#" aria-label="Личное облако, главная">${icon('cloud')}<span>Личное<span class="brand-sub">облако</span></span><span class="brand-dot"></span></a>
    <div class="workspace-label">ЛИЧНОЕ ПРОСТРАНСТВО</div><nav aria-label="Основная навигация">${[['files','folder-closed','Мои файлы'],['links','link','Общие ссылки'],['trash','trash-2','Корзина']].map(([id,i,t])=>`<button class="nav-item ${page===id?'active':''}" data-nav="${id}">${icon(i)}<span>${t}</span>${id==='files'?`<span class="nav-count">${count}</span>`:''}</button>`).join('')}</nav>
    <div class="sidebar-bottom"><div class="storage-heading">${icon('hard-drive')}<span>Хранилище браузера</span></div><div class="storage-value">${fmt(used)} <span>занято файлами</span></div><div class="storage-track"><span id="storage-fill"></span></div><p>Доступно на этом устройстве</p><button class="storage-details" data-action="storage">Подробнее ${icon('arrow-up-right')}</button></div>
    <button class="profile" data-action="settings"><span class="avatar">Л</span><span>Личное пространство<small>Локальная версия</small></span>${icon('settings-2')}</button></aside>
    <div class="sidebar-shade" data-action="close-nav"></div>
    <main><header class="topbar"><button class="icon-button mobile-menu" data-action="menu" aria-label="Открыть навигацию">${icon('menu')}</button><div class="top-location">Пространство <span>/</span> <strong>${titles[page]}</strong></div><div class="top-right"><span class="local-tag"><span></span>На этом устройстве</span><button class="icon-button" data-action="help" aria-label="Помощь">${icon('circle-help')}</button><span class="avatar small">Л</span></div></header>
    <div class="content"><div class="page-heading"><div><div class="eyebrow">ВАШЕ ЛИЧНОЕ ОБЛАКО</div><h1>${esc(page==='files' && folder ? get(folder)?.name : titles[page])}</h1><p>${page==='files'?'Всё нужное — на своём месте.':page==='links'?'Файлы, которые вы подготовили для обмена.':'Удалённые файлы можно восстановить.'}</p></div><div class="heading-actions">${page==='files'?`<button class="button secondary" data-action="new-folder">${icon('folder-plus')}<span>Новая папка</span></button><button class="button primary" data-action="upload">${icon('upload')}<span>Загрузить файлы</span></button>`:page==='trash'?`<button class="button secondary danger" data-action="empty-trash" ${list.length?'':'disabled'}>${icon('trash-2')}Очистить корзину</button>`:''}</div></div>
    ${page==='links'?`<div class="notice">${icon('info')}<span>Публичные ссылки появятся после подключения сервера. Здесь можно подготовить список файлов для обмена.</span></div>`:''}
    <div class="file-toolbar"><label class="search">${icon('search')}<input id="search" placeholder="Поиск файлов и папок" aria-label="Поиск файлов и папок" value="${esc(query)}" autocomplete="off">${query?`<button class="icon-button" data-action="clear-search" aria-label="Очистить поиск">${icon('x')}</button>`:`<kbd>/</kbd>`}</label><div class="toolbar-right"><button class="sort-button" data-action="sort">${icon('arrow-down-up')}<span>${sort==='name'?'По названию':sort==='size'?'По размеру':'По дате'}</span>${icon('chevron-down')}</button><div class="view-toggle"><button class="icon-button ${view==='list'?'on':''}" data-view="list" aria-label="Список" aria-pressed="${view==='list'}">${icon('list')}</button><button class="icon-button ${view==='grid'?'on':''}" data-view="grid" aria-label="Плитки" aria-pressed="${view==='grid'}">${icon('layout-grid')}</button></div></div></div>
    <div class="breadcrumb"><button data-folder="">${icon('house')}<span>${page==='files'?'Все файлы':titles[page]}</span></button>${page==='files'?ancestors(folder).map(e=>`${icon('chevron-right')}<button data-folder="${e.id}">${esc(e.name)}</button>`).join(''):''}<span class="item-count">${list.length} ${plural(list.length,'элемент','элемента','элементов')}${query?' найдено':''}</span></div>
    <div class="files-area ${p?'with-preview':''}"><section class="file-panel" aria-label="Файлы"><div class="table-head"><label class="check"><input type="checkbox" id="select-all" aria-label="Выбрать все файлы" ${list.length&&list.every(e=>selected.has(e.id))?'checked':''}></label><button data-sort="name">Название ${icon(sort==='name'&&!ascending?'arrow-down':'arrow-up')}</button><button class="size-col" data-sort="size">Размер</button><button class="date-col" data-sort="modified">Изменено</button><span></span></div><div class="file-items ${view}">${list.map(e=>`<div class="file-row ${selected.has(e.id)?'selected':''} ${preview===e.id?'previewed':''}" data-id="${e.id}" tabindex="0" role="group" aria-label="${esc(e.name)}"><label class="check"><input type="checkbox" data-select="${e.id}" aria-label="Выбрать ${esc(e.name)}" ${selected.has(e.id)?'checked':''}></label><button class="file-name" data-open="${e.id}">${fileIcon(e)}<span class="file-label">${esc(e.name)}<small>${e.kind==='folder'?'Папка':fmt(e.size)}</small></span>${e.shared?icon('link','shared-mark'):''}</button><span class="size-col">${e.kind==='folder'?'—':fmt(e.size)}</span><span class="date-col">${date(e.modified)}</span><button class="icon-button row-more" data-more="${e.id}" aria-label="Действия: ${esc(e.name)}">${icon('ellipsis')}</button></div>`).join('')||`<div class="empty">${icon(query?'search-x':page==='trash'?'trash-2':page==='links'?'link':'folder-open')}<h2>${query?'Ничего не найдено':page==='trash'?'Корзина пуста':page==='links'?'Пока нет файлов для обмена':'Здесь пока пусто'}</h2><p>${query?'Попробуйте другое название.':page==='trash'?'Удалённые файлы появятся здесь.':page==='links'?'Выберите файл и нажмите «Поделиться».':'Загрузите файлы или создайте первую папку.'}</p>${page==='files'&&!query?'<button class="button secondary" data-action="upload">Загрузить файлы</button>':''}</div>`}</div><div class="drop-hint">${icon('upload-cloud')}<span>Перетащите файлы сюда для загрузки</span></div></section>${p?previewHTML(p):''}</div>
    <footer class="content-footer"><span>${icon('hard-drive')} Файлы хранятся в этом браузере</span><button data-action="help">Как это работает ${icon('arrow-up-right')}</button></footer></div>
    ${selected.size?`<div class="selection-bar"><button class="icon-button" data-action="deselect" aria-label="Снять выделение">${icon('x')}</button><span class="selection-count">Выбрано: <strong>${selected.size}</strong></span><div class="selection-actions">${page==='trash'?`<button data-action="restore">${icon('rotate-ccw')}<span>Восстановить</span></button><button class="danger" data-action="delete">${icon('trash-2')}<span>Удалить навсегда</span></button>`:`<button data-action="download">${icon('download')}<span>Скачать</span></button><button data-action="share">${icon('link')}<span>Поделиться</span></button><button data-action="move">${icon('folder-input')}<span>Переместить</span></button><button data-action="trash">${icon('trash-2')}<span>В корзину</span></button>`}</div></div>`:''}
    </main></div><input id="file-input" type="file" multiple hidden><div id="overlay-root"></div><div id="toast-root" role="status" aria-live="polite"></div><div id="drop-overlay">${icon('upload-cloud')}<h2>Отпустите файлы для загрузки</h2><p>В текущую папку</p></div>`;
  bind(); createIcons({icons});
  navigator.storage?.estimate().then(({quota})=>{const fill=$('#storage-fill');if(fill)fill.style.width=`${quota?Math.min(100,used/quota*100):0}%`;}).catch(()=>{});
}
function plural(n,a,b,c) {return n%10===1&&n%100!==11?a:n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?b:c;}
function previewHTML(e) {
 let media='';
 if(e.blob) { const u=objectURL(e.blob); media=e.mime?.startsWith('image/')?`<img src="${u}" alt="${esc(e.name)}">`:e.mime?.startsWith('video/')?`<video controls src="${u}"></video>`:e.mime?.startsWith('audio/')?`<audio controls src="${u}"></audio>`:e.mime==='application/pdf'?`<iframe src="${u}" title="${esc(e.name)}"></iframe>`:e.mime?.startsWith('text/')||/\.(md|json|csv|log)$/i.test(e.name)?'<pre id="text-preview">Загрузка…</pre>':`<div class="unsupported">${fileIcon(e)}<p>Предпросмотр недоступен.<br>Скачайте файл, чтобы открыть его.</p></div>`; }
 return `<aside class="preview-panel" aria-label="Предпросмотр"><div class="preview-heading"><span>ПРЕДПРОСМОТР</span><button class="icon-button" data-action="close-preview" aria-label="Закрыть предпросмотр">${icon('x')}</button></div><div class="preview-media">${media}</div><h2>${esc(e.name)}</h2><span class="file-type">${esc(e.mime||'Файл')}</span><dl><div><dt>Размер</dt><dd>${fmt(e.size)}</dd></div><div><dt>Изменено</dt><dd>${date(e.modified)}</dd></div><div><dt>Расположение</dt><dd>${esc(get(e.parent)?.name||'Все файлы')}</dd></div></dl><button class="button primary full" data-download="${e.id}">${icon('download')}Скачать файл</button>${!e.deleted?`<button class="button secondary full" data-share="${e.id}">${icon('link')}Поделиться</button>`:''}</aside>`;
}
function bind() {
 document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>nav(b.dataset.nav));
 $('.brand').onclick=e=>{e.preventDefault();nav('files');};
 document.querySelectorAll('[data-action]').forEach(b=>b.onclick=e=>action(b.dataset.action,e));
 document.querySelectorAll('[data-folder]').forEach(b=>b.onclick=()=>openFolder(b.dataset.folder||null));
 document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;render();});
 document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>setSort(b.dataset.sort));
 document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>openEntry(b.dataset.open));
 document.querySelectorAll('[data-select]').forEach(b=>b.onchange=()=>{b.checked?selected.add(b.dataset.select):selected.delete(b.dataset.select);render();});
 document.querySelectorAll('[data-more]').forEach(b=>b.onclick=e=>context(e,b.dataset.more));
 document.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download([b.dataset.download]));
 document.querySelectorAll('[data-share]').forEach(b=>b.onclick=()=>share([b.dataset.share]));
 document.querySelectorAll('.file-row').forEach(r=>{r.oncontextmenu=e=>{e.preventDefault();context(e,r.dataset.id);};r.onkeydown=e=>{if(e.target===r&&e.key==='Enter')openEntry(r.dataset.id);};});
 $('#select-all').onchange=e=>{visible().forEach(x=>e.target.checked?selected.add(x.id):selected.delete(x.id));render();};
 $('#search').oninput=e=>{const pos=e.target.selectionStart;query=e.target.value;selected.clear();render();$('#search').focus();$('#search').setSelectionRange(pos,pos);};
 $('#file-input').onchange=e=>upload([...e.target.files]);
 const p=get(preview); if($('#text-preview')&&p?.blob) p.blob.slice(0,200000).text().then(t=>{if(preview===p.id&&$('#text-preview'))$('#text-preview').textContent=t+(p.size>200000?'\n… Показаны первые 200 КБ':'');});
}
function openEntry(id) {const e=get(id);if(e.kind==='folder'){if(e.deleted)return toast('Сначала восстановите папку');openFolder(id);}else{preview=id;render();}}
function setSort(key){ascending=sort===key?!ascending:true;sort=key;render();}
function action(a,event) {
 const ids=[...selected];
 if(a==='upload')$('#file-input').click();
 if(a==='new-folder')nameDialog();
 if(a==='deselect'){selected.clear();render();}
 if(a==='close-preview'){preview=null;render();}
 if(a==='download')download(ids);
 if(a==='share')share(ids);
 if(a==='move')moveDialog(ids);
 if(a==='trash')trash(ids);
 if(a==='restore')restore(ids);
 if(a==='delete')deleteDialog(ids);
 if(a==='empty-trash')deleteDialog(entries.filter(e=>e.deleted).map(e=>e.id));
 if(a==='clear-search'){query='';render();$('#search').focus();}
 if(a==='menu')$('.layout').classList.add('nav-open');
 if(a==='close-nav')$('.layout').classList.remove('nav-open');
 if(a==='sort')menu(event,[['name','По названию','a-arrow-down'],['modified','По дате изменения','calendar'],['size','По размеру','hard-drive']].map(([k,l,i])=>({label:l,icon:i,run:()=>setSort(k)})));
 if(['help','settings','storage'].includes(a))infoDialog(a);
}
function toast(message,error=false) {const root=$('#toast-root');root.innerHTML=`<div class="toast ${error?'error':''}">${icon(error?'circle-alert':'check')}${esc(message)}</div>`;createIcons({icons});setTimeout(()=>root.replaceChildren(),4500);}
function modal(title,body,buttons,setup) {
 const previous=document.activeElement;
 $('#overlay-root').innerHTML=`<div class="modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-heading"><h2 id="modal-title">${title}</h2><button class="icon-button" data-close aria-label="Закрыть">${icon('x')}</button></div><div class="modal-body">${body}</div><div class="modal-actions">${buttons}</div></section></div>`;
 const close=()=>{$('#overlay-root').replaceChildren();previous?.focus();};
 document.querySelectorAll('[data-close]').forEach(b=>b.onclick=close);
 $('.modal-backdrop').onclick=e=>{if(e.target===e.currentTarget)close();};
 $('.modal').onkeydown=e=>{if(e.key==='Escape')close();if(e.key==='Tab'){const els=[...$('.modal').querySelectorAll('button:not(:disabled),input,select,a[href]')];const first=els[0],last=els.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};
 setup?.(close);createIcons({icons});setTimeout(()=>($('.modal input')||$('.modal button'))?.focus(),0);
}
function nameDialog(id) {
 const e=get(id);
 modal(e?'Переименовать':'Новая папка',`<label class="field-label" for="entry-name">Название</label><input class="text-input" id="entry-name" maxlength="200" value="${esc(e?.name||'')}" placeholder="Например, Документы"><p class="form-error" id="name-error"></p>`,`<button class="button secondary" data-close>Отмена</button><button class="button primary" id="save-name">${e?'Сохранить':'Создать папку'}</button>`,close=>{
  const save=async()=>{const name=$('#entry-name').value.trim();if(!name||/[\/\\\x00-\x1f]/.test(name)||name==='.'||name==='..')return $('#name-error').textContent='Введите название без слешей и служебных символов.';if(active().some(x=>x.id!==id&&x.parent===(e?e.parent:folder)&&x.name===name))return $('#name-error').textContent='Такое название уже есть в этой папке.';close();await change(()=>{if(e){e.name=name;e.modified=Date.now();}else entries.push({id:uid(),name,kind:'folder',parent:folder,modified:Date.now(),size:0});},e?'Название изменено':'Папка создана');};$('#save-name').onclick=save;$('#entry-name').onkeydown=ev=>{if(ev.key==='Enter')save();};$('#entry-name').select();
 });
}
function uniqueName(name,parent) {let result=name,n=1;const dot=name.lastIndexOf('.'),base=dot>0?name.slice(0,dot):name,ext=dot>0?name.slice(dot):'';while(active().some(e=>e.parent===parent&&e.name===result))result=`${base} (${n++})${ext}`;return result;}
async function upload(files) {
 if(page!=='files')return toast('Откройте «Мои файлы» для загрузки');
 if(!files.length)return;
 await change(()=>files.forEach(file=>entries.push({id:uid(),name:uniqueName(file.name,folder),kind:'file',parent:folder,modified:Date.now(),size:file.size,mime:file.type,blob:file})),`Загружено файлов: ${files.length}`);
}
function download(ids) {const files=ids.map(get).filter(e=>e?.blob);if(!files.length)return toast('Откройте папку и выберите файлы для скачивания');for(const e of files){const u=URL.createObjectURL(e.blob),a=document.createElement('a');a.href=u;a.download=e.name;a.click();setTimeout(()=>URL.revokeObjectURL(u),10000);}if(files.length<ids.length)toast('Скачаны файлы. Папки нужно открыть отдельно.');}
function trash(ids) {change(()=>{entries.forEach(e=>{if(ids.some(id=>descendant(e.id,id)))e.deleted=Date.now();});selected.clear();preview=null;},'Перемещено в корзину');}
function restore(ids) {change(()=>{const restored=entries.filter(e=>ids.some(id=>descendant(e.id,id)));restored.forEach(e=>delete e.deleted);ids.forEach(id=>{const e=get(id);if(e&&(!get(e.parent)||get(e.parent)?.deleted))e.parent=null;if(e)e.name=uniqueRestoredName(e);});selected.clear();},'Восстановлено');}
function uniqueRestoredName(e){const name=e.name;e.name='';const unique=uniqueName(name,e.parent);e.name=name;return unique;}
function deleteDialog(ids) {modal('Удалить навсегда?',`<p>Будет удалено элементов: <strong>${ids.length}</strong>. Содержимое выбранных папок тоже будет удалено. Восстановить их не получится.</p>`,`<button class="button secondary" data-close>Отмена</button><button class="button destructive" id="confirm-delete">Удалить навсегда</button>`,close=>{$('#confirm-delete').onclick=()=>{close();change(()=>{const remove=entries.filter(e=>ids.some(id=>descendant(e.id,id))).map(e=>e.id);entries=entries.filter(e=>!remove.includes(e.id));selected.clear();preview=null;},'Удалено навсегда');};});}
function moveDialog(ids) {
 const folders=active().filter(e=>e.kind==='folder'&&!ids.some(id=>descendant(e.id,id)));
 modal('Переместить',`<p>Выберите папку назначения.</p><label class="field-label" for="destination">Папка</label><select class="text-input" id="destination"><option value="">Все файлы</option>${folders.map(e=>`<option value="${e.id}">${esc(ancestors(e.id).map(x=>x.name).join(' / '))}</option>`).join('')}</select><p class="form-error" id="move-error"></p>`,`<button class="button secondary" data-close>Отмена</button><button class="button primary" id="confirm-move">Переместить</button>`,close=>{$('#confirm-move').onclick=()=>{const dest=$('#destination').value||null;const moving=ids.map(get).filter(e=>e&&!ids.some(id=>id!==e.id&&descendant(e.id,id)));if(moving.some(e=>active().some(x=>x.parent===dest&&x.name===e.name&&x.id!==e.id)))return $('#move-error').textContent='В папке назначения уже есть элемент с таким названием.';close();change(()=>{moving.forEach(e=>{e.parent=dest;e.modified=Date.now();});selected.clear();},'Перемещено');};});
}
function share(ids) {
 const items=ids.map(get).filter(Boolean),allShared=items.every(e=>e.shared);
 modal('Поделиться',`<div class="dialog-symbol">${icon('link')}</div><p><strong>${items.length===1?esc(items[0].name):`Выбрано элементов: ${items.length}`}</strong></p><p>Сейчас файлы находятся только в этом браузере. Публичную ссылку можно будет создать после подключения серверного хранилища.</p><p>Пока можно ${allShared?'убрать их из':'добавить их в'} раздел «Общие ссылки» или скачать файл и отправить его самостоятельно.</p>`,`<button class="button secondary" data-close>Закрыть</button><button class="button primary" id="prepare-share">${allShared?'Убрать из списка':'Подготовить к обмену'}</button>`,close=>{$('#prepare-share').onclick=()=>{close();change(()=>items.forEach(e=>e.shared=!allShared),allShared?'Убрано из общих ссылок':'Добавлено в общие ссылки');};});
}
function infoDialog(kind) {
 const used=entries.reduce((n,e)=>n+(e.size||0),0);
 modal(kind==='storage'?'Хранилище':kind==='settings'?'Личное пространство':'Как это работает',`<div class="dialog-symbol">${icon(kind==='storage'?'hard-drive':'cloud')}</div><p>Это локальная версия личного облака. Файлы сохраняются в браузере на этом устройстве и остаются после обновления страницы.</p><dl class="info-list"><div><dt>Занято файлами</dt><dd>${fmt(used)}</dd></div><div><dt>Файлов</dt><dd>${entries.filter(e=>e.kind==='file').length}</dd></div><div><dt>Хранение</dt><dd>В этом браузере</dd></div></dl><p>Очистка данных сайта удалит сохранённые файлы. Корзина тоже занимает место. Для синхронизации устройств и публичных ссылок нужно подключить backend на VPS.</p>${kind==='help'?'<p><kbd>/</kbd> — поиск · <kbd>Esc</kbd> — закрыть окно или снять выделение. Файлы можно загружать перетаскиванием.</p>':''}`,`<button class="button primary" data-close>Понятно</button>`);
}
function context(event,id) {
 const e=get(id);const ids=selected.has(id)?[...selected]:[id];
 menu(event,e.deleted?[{label:'Восстановить',icon:'rotate-ccw',run:()=>restore(ids)},{label:'Удалить навсегда',icon:'trash-2',danger:true,run:()=>deleteDialog(ids)}]:[
 {label:e.kind==='folder'?'Открыть папку':'Предпросмотр',icon:e.kind==='folder'?'folder-open':'eye',run:()=>openEntry(id)},
 ...(e.kind==='file'?[{label:'Скачать',icon:'download',run:()=>download(ids)}]:[]),
 {label:'Поделиться',icon:'link',run:()=>share(ids)},
 {label:'Переименовать',icon:'pencil',run:()=>nameDialog(id)},
 {label:'Переместить',icon:'folder-input',run:()=>moveDialog(ids)},
 {label:'В корзину',icon:'trash-2',danger:true,run:()=>trash(ids)}]);
}
function menu(event,items) {
 $('.context-menu')?.remove();const el=document.createElement('div');el.className='context-menu';el.setAttribute('role','menu');el.innerHTML=items.map((x,i)=>`<button role="menuitem" class="${x.danger?'danger':''}" data-menu-index="${i}">${icon(x.icon)}${x.label}</button>`).join('');document.body.append(el);const rect=event.currentTarget?.getBoundingClientRect();const x=event.type==='contextmenu'?event.clientX:rect?.right||event.clientX,y=event.type==='contextmenu'?event.clientY:rect?.bottom||event.clientY;el.style.left=`${Math.max(8,Math.min(x-200,innerWidth-220))}px`;el.style.top=`${Math.max(8,Math.min(y+5,innerHeight-el.offsetHeight-10))}px`;el.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{el.remove();items[i].run();});el.onkeydown=e=>{const bs=[...el.querySelectorAll('button')];let i=bs.indexOf(document.activeElement);if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();bs[(i+(e.key==='ArrowDown'?1:bs.length-1))%bs.length].focus();}if(e.key==='Escape'){el.remove();event.currentTarget?.focus();}};createIcons({icons});el.querySelector('button').focus();setTimeout(()=>document.addEventListener('click',function dismiss(e){if(!el.contains(e.target)){el.remove();document.removeEventListener('click',dismiss);}}, {capture:true}),0);
}
let dragDepth=0;
document.addEventListener('dragenter',e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();dragDepth++;if(page==='files')$('#drop-overlay')?.classList.add('show');}});
document.addEventListener('dragover',e=>{if(e.dataTransfer?.types.includes('Files'))e.preventDefault();});
document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#drop-overlay')?.classList.remove('show');}});
document.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('#drop-overlay')?.classList.remove('show');upload([...e.dataTransfer.files]);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){if($('.modal'))return;$('.context-menu')?.remove();$('.layout')?.classList.remove('nav-open');selected.clear();preview=null;render();}if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('.modal')){e.preventDefault();$('#search')?.focus();}});
init().catch(()=>{$('#app').innerHTML='<div class="fatal"><h1>Хранилище браузера недоступно</h1><p>Разрешите хранение данных сайта и обновите страницу.</p><button onclick="location.reload()">Попробовать снова</button></div>';});
