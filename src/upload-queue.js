import { request, serverMaxFile } from './server-storage.js';
const KEY = 'hxry-upload-queue-v1';
const API = `${import.meta.env.BASE_URL}api/`;
const CHUNK = 4 * 1024 ** 2;
// Aim for about two seconds per request, bounded to 4–32 MiB.
function preferredChunk(bytesPerSecond, maximum) {
  const target=bytesPerSecond*2;
  let size=CHUNK;
  while(size*2<=maximum&&size*2<=target)size*=2;
  return Math.min(size,maximum);
}
const labels = { queued: 'В очереди', uploading: 'Загрузка', checking: 'Проверка файла', finishing: 'Проверка на сервере', paused: 'Пауза', retrying: 'Повтор соединения', offline: 'Нет соединения', source: 'Выберите исходный файл', done: 'Загружен', cancelled: 'Отменён', error: 'Ошибка' };
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const bytes = n => n >= 1024 ** 3 ? `${(n/1024**3).toFixed(2)} ГБ` : `${(n/1024**2).toFixed(1)} МБ`;
async function hash(blob) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))].map(n => n.toString(16).padStart(2,'0')).join(''); }
async function fingerprint(file) {
  return hash(new Blob([String(file.size), file.slice(0,1024**2), file.slice(Math.max(0,file.size-1024**2))]));
}
export class UploadQueue {
  constructor(onCommitted) {
    this.onCommitted = onCommitted; this.files = new Map(); this.running = false; this.active = null; this.xhr = null; this.abort = null;
    try { this.items = JSON.parse(localStorage.getItem(KEY) || '[]').filter(item => item && /^[a-f0-9-]{36}$/.test(item.id) && typeof item.name==='string' && Number.isSafeInteger(item.size) && item.size>=0 && /^[a-f0-9]{64}$/.test(item.fingerprint)); } catch { this.items = []; }
    for (const item of this.items) if (!['done','cancelled'].includes(item.status)) { item.status='source'; item.offset=Number.isSafeInteger(item.offset)?Math.max(0,Math.min(item.offset,item.size)):0; item.loaded=item.offset; }
    this.panel = document.createElement('section'); this.panel.className = 'upload-queue'; this.panel.setAttribute('aria-label','Очередь загрузок'); document.body.append(this.panel);
    this.input=document.createElement('input'); this.input.type='file'; this.input.hidden=true; document.body.append(this.input);
    this.input.onchange=async()=>{
      const item=this.items.find(q=>q.id===this.input.dataset.id), file=this.input.files[0]; this.input.value='';
      if (!item || !file) return;
      try {
        if(file.size!==item.size || await fingerprint(file)!==item.fingerprint) throw new Error('Выбран другой файл. Нужен исходный файл загрузки.');
        this.files.set(item.id,file); item.status='queued'; item.error=''; this.save(); this.work();
      } catch(error) { item.error=error.message; this.save(); }
    };
    this.panel.onclick=event=>{
      const button=event.target.closest('button'); if(!button)return;
      if(button.dataset.action==='clear') { this.items=this.items.filter(q=>!['done','cancelled'].includes(q.status)); this.save(); return; }
      const item=this.items.find(q=>q.id===button.dataset.id); if(!item)return;
      if(button.dataset.action==='pause') this.pause(item);
      if(button.dataset.action==='cancel') this.cancel(item);
      if(button.dataset.action==='resume') {
        if(!this.files.has(item.id)) { this.input.dataset.id=item.id; this.input.click(); }
        else { item.status='queued'; item.error=''; this.save(); this.work(); }
      }
    };
    window.addEventListener('offline',()=>{ if(this.active)this.pause(this.active,'offline'); });
    window.addEventListener('online',()=>{
      for(const item of this.items) if(item.status==='offline'&&this.files.has(item.id))item.status='queued';
      this.save(); this.work();
    });
    window.addEventListener('beforeunload',event=>{
      if(this.items.some(q=>!['done','cancelled'].includes(q.status))) { event.preventDefault(); event.returnValue=''; }
    });
    this.save();
    this.reconcile();
  }
  async reconcile() {
    let changed=false;
    for(const item of this.items.filter(q=>q.status==='source')) {
      try {
        const state=await (await request(`uploads/${item.id}`)).json();
        if(item.status!=='source')continue;
        item.offset=state.offset;item.loaded=state.offset;
        if(state.status==='done'){item.status='done';item.name=state.entry.name;changed=true;}
        this.save();
      } catch(error) {if(error.status===404){item.error='Части отсутствуют или срок хранения истёк. При выборе файла загрузка начнётся заново.';this.save();}}
    }
    if(changed)try{await this.onCommitted();}catch{}
  }
  save() {
    try { localStorage.setItem(KEY,JSON.stringify(this.items.map(({ speed, ...item })=>item))); }
    catch { this.warning='Браузер не сохранил очередь. После закрытия страницы автоматическое восстановление может быть недоступно.'; }
    this.render();
  }
  render() {
    this.panel.hidden=!this.items.length;
    const total=this.items.reduce((n,q)=>n+q.size,0), loaded=this.items.reduce((n,q)=>n+Math.min(q.loaded||0,q.size),0);
    this.panel.innerHTML=`<header><strong>Загрузки · ${this.items.filter(q=>q.status==='done').length}/${this.items.length}</strong><button data-action="clear">Убрать завершённые</button></header><p>${bytes(loaded)} / ${bytes(total)} · ${total?Math.floor(100*loaded/total):0}%</p>${this.warning?`<p role="alert">${esc(this.warning)}</p>`:''}<div class="upload-list">${this.items.map(q=>{
      const percent=q.size?Math.floor(100*Math.min(q.loaded||0,q.size)/q.size):q.status==='done'?100:0;
      const busy=['queued','uploading','checking','finishing','retrying'].includes(q.status);
      return `<div class="upload-item"><strong title="${esc(q.name)}">${esc(q.name)}</strong><span>${labels[q.status]||'Пауза'} · ${percent}%</span><progress max="100" value="${percent}" aria-label="${esc(q.name)}: ${percent}%"></progress><small>${bytes(q.loaded||0)} / ${bytes(q.size)}${q.speed?` · ${bytes(q.speed)}/с · ~${Math.ceil((q.size-(q.loaded||0))/q.speed)} с`:''}</small>${q.error?`<p role="alert">${esc(q.error)}</p>`:''}<div>${busy?`<button data-action="pause" data-id="${q.id}">Пауза</button>`:''}${!busy&&!['done','cancelled'].includes(q.status)?`<button data-action="resume" data-id="${q.id}">${this.files.has(q.id)?'Продолжить':'Выбрать файл'}</button>`:''}${!['done','cancelled'].includes(q.status)?`<button data-action="cancel" data-id="${q.id}">Отменить</button>`:''}</div></div>`;
    }).join('')}</div><small>Незавершённые части хранятся 7 дней. После закрытия страницы потребуется повторно выбрать исходные файлы.</small>`;
  }
  async add(files,parent) {
    for(const file of files) {
      const identity=await fingerprint(file);
      let item=this.items.find(q=>!['done','cancelled'].includes(q.status)&&q.fingerprint===identity&&q.size===file.size&&!this.files.has(q.id));
      if(!item) {
        item={id:crypto.randomUUID(),name:file.name,parent,size:file.size,mime:file.type,fingerprint:identity,offset:0,loaded:0,status:'queued'};
        this.items.push(item);
      }
      if(file.size>serverMaxFile) { item.status='error'; item.error='Файл превышает лимит сервера.'; }
      else { this.files.set(item.id,file); item.status='queued'; item.error=''; }
      this.save();
    }
    this.work();
  }
  pause(item,status='paused') {
    item.status=status; item.speed=0;
    if(this.active?.id===item.id) { this.xhr?.abort(); this.abort?.abort(); }
    this.save();
  }
  async cancel(item) {
    this.pause(item);
    try {
      const result=await (await request(`uploads/${item.id}`,{method:'DELETE'})).json();
      if(result.status==='done') { item.status='done'; item.offset=item.size; item.loaded=item.size; await this.onCommitted(); }
      else item.status='cancelled';
      item.error=''; this.files.delete(item.id);
    } catch(error) {
      if(error.status===404) { item.status='cancelled'; this.files.delete(item.id); }
      else { item.error='Не удалось отменить на сервере. Повторите при восстановлении соединения.'; }
    }
    this.save();
  }
  chunk(item,blob,digest) {
    return new Promise((resolve,reject)=>{
      const xhr=this.xhr=new XMLHttpRequest();
      xhr.open('PUT',API+`uploads/${item.id}/chunk?offset=${item.offset}`);
      xhr.setRequestHeader('X-Cloud-Request','1'); xhr.setRequestHeader('X-Chunk-SHA256',digest); xhr.timeout=120000;
      let previous=0,stamp=performance.now();
      xhr.upload.onprogress=event=>{
        const now=performance.now(); item.loaded=item.offset+Math.min(blob.size,event.loaded);
        if(now-stamp>250) { item.speed=(event.loaded-previous)*1000/(now-stamp); previous=event.loaded; stamp=now; }
        this.render();
      };
      xhr.onload=()=>{
        let result; try {result=JSON.parse(xhr.responseText);} catch {return reject(new Error('Некорректный ответ сервера'));}
        if(xhr.status>=200&&xhr.status<300)resolve(result);
        else {const error=new Error(result.error||`Ошибка загрузки (${xhr.status})`);error.status=xhr.status;reject(error);}
      };
      xhr.onerror=()=>{const error=new Error('Соединение прервано. Подтверждённые части сохранены на сервере.');error.network=true;reject(error);};
      xhr.ontimeout=xhr.onerror;
      xhr.onabort=()=>reject(new DOMException('Пауза','AbortError'));
      xhr.send(blob);
    });
  }
  async work() {
    if(this.running)return;
    this.running=true;
    try {
      let item;
      while((item=this.items.find(q=>q.status==='queued'&&this.files.has(q.id)))) {
        this.active=item; this.abort=new AbortController();
        const signal=this.abort.signal, file=this.files.get(item.id);
        try {
          if(!navigator.onLine) {item.status='offline';this.save();continue;}
          item.status='checking'; item.error=''; this.save();
          let state=await (await request(`uploads/${item.id}`,{method:'PUT',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({name:item.name,size:item.size,parent:item.parent,mime:item.mime,fingerprint:item.fingerprint,protocol:2})})).json();
          if(state.status!=='done') {
            let verifiedOffset=0;
            for(let n=0;n<state.hashes.length;n++) {
              if(signal.aborted)throw new DOMException('Пауза','AbortError');
              const record=state.hashes[n], length=typeof record==='string'?Math.min(CHUNK,item.size-verifiedOffset):record.size;
              const digest=typeof record==='string'?record:record.sha256;
              if(await hash(file.slice(verifiedOffset,verifiedOffset+length))!==digest)throw new Error('Содержимое исходного файла изменилось. Отмените загрузку и начните новую.');
              verifiedOffset+=length;
            }
            if(verifiedOffset!==state.offset)throw new Error('Сервер вернул некорректную позицию загрузки.');
            item.offset=state.offset; item.loaded=state.offset; this.save();
            const modern=state.protocol===2, maximum=state.chunkSize;
            let nextSize=modern?Math.min(8*1024**2,maximum):CHUNK;
            const prepare=(offset,size)=>{
              const blob=file.slice(offset,Math.min(offset+size,item.size));
              // Settle prefetch errors even if a pause means this promise is never consumed.
              return hash(blob).then(digest=>({blob,digest}),error=>({error}));
            };
            let prepared=item.offset<item.size?prepare(item.offset,nextSize):null;
            while(item.offset<item.size) {
              if(signal.aborted)throw new DOMException('Пауза','AbortError');
              item.status='uploading';
              const ready=await prepared;if(ready.error)throw ready.error;
              const {blob,digest}=ready;
              if(signal.aborted)throw new DOMException('Пауза','AbortError');
              const followingOffset=item.offset+blob.size;
              prepared=followingOffset<item.size?prepare(followingOffset,nextSize):null;
              const started=performance.now();
              state=await this.chunk(item,blob,digest);
              const effectiveSpeed=blob.size*1000/Math.max(1,performance.now()-started);
              nextSize=modern?preferredChunk(effectiveSpeed,maximum):CHUNK;
              item.speed=effectiveSpeed;item.offset=state.offset;item.loaded=state.offset;item.retries=0; this.save();
            }
            if(signal.aborted)throw new DOMException('Пауза','AbortError');
            item.status='finishing'; item.speed=0; this.save();
            state=await (await request(`uploads/${item.id}/complete`,{method:'PUT',signal})).json();
          }
          item.status='done';item.offset=item.size;item.loaded=item.size;item.speed=0;item.name=state.entry.name;this.files.delete(item.id);this.save();
          await this.onCommitted();
        } catch(error) {
          if(item.status==='done') item.error='Файл сохранён на сервере. Обновите страницу, чтобы обновить список.';
          else if(error.name!=='AbortError') {
            item.status=navigator.onLine?'error':'offline';item.error=error.message;
            if(error.network&&navigator.onLine&&(item.retries||0)<3) {
              item.retries=(item.retries||0)+1;item.status='retrying';
              setTimeout(()=>{if(item.status==='retrying'){item.status='queued';this.save();this.work();}},2000*item.retries);
            }
          }
          item.loaded=item.offset;item.speed=0;this.save();
        } finally {this.active=null;this.xhr=null;this.abort=null;}
      }
    } finally {this.running=false;}
  }
}
