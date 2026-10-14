import {request} from './server-storage.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const size=n=>n<1024?`${n} Б`:n<1024**2?`${(n/1024).toLocaleString('ru',{maximumFractionDigits:1})} КБ`:n<1024**3?`${(n/1024**2).toLocaleString('ru',{maximumFractionDigits:1})} МБ`:`${(n/1024**3).toLocaleString('ru',{maximumFractionDigits:2})} ГБ`;
const when=value=>value?new Date(value*1000).toLocaleString('ru'):'Пока нет';
export async function mountReplica(root){
 root.innerHTML='<h2>Копия на ноутбуке</h2><div id="replica-status" role="status">Получаем состояние…</div><div class="replica-controls"><button class="button secondary" id="replica-refresh">Обновить статус</button><button class="button secondary" id="replica-key">Подключить ноут / заменить ключ</button></div><div id="replica-key-panel"></div><p class="field-note">Ноут сам забирает данные по HTTPS. Окончательное удаление в облаке удаляет копию при следующей синхронизации; корзина сохраняется. Это копия без истории удалений.</p>';
 const target=root.querySelector('#replica-status');
 const refresh=async()=>{
  try{
   const state=await(await request('admin/replica')).json();const report=state.report||{};
   target.innerHTML=`<div class="replica-summary"><strong>${state.pendingPercent}% ещё не подтверждено ноутбуком</strong><span class="account-badge">${!state.configured?'Не подключён':state.online?'На связи':'Нет связи'}</span></div><progress max="100" value="${100-state.pendingPercent}" aria-label="Скопировано на ноутбук"></progress><p>${state.copiedFiles} из ${state.totalFiles} файлов · ${size(state.copiedBytes)} из ${size(state.totalBytes)}</p><dl class="replica-details"><div><dt>Ожидают копирования</dt><dd>${state.pendingFiles} файлов · ${size(state.pendingBytes)}</dd></div><div><dt>Копии к удалению</dt><dd>${state.pendingDeletes}</dd></div><div><dt>Метаданные аккаунтов и папок</dt><dd>${state.metadataPending?'Ожидают синхронизации':'Подтверждены'}</dd></div><div><dt>Последняя связь</dt><dd>${when(state.lastSeen)}</dd></div><div><dt>Последний завершённый цикл</dt><dd>${when(state.lastComplete)}</dd></div><div><dt>Лимит на ноуте</dt><dd>200 ГБ</dd></div>${report.usedBytes!==undefined?`<div><dt>Занято копией</dt><dd>${size(report.usedBytes)}</dd></div><div><dt>Свободно на диске ноута</dt><dd>${size(report.freeBytes)}</dd></div>`:''}</dl>${report.activeSize?`<p>Сейчас копируется файл: ${Math.floor(100*report.activeBytes/report.activeSize)}%</p>`:''}${state.preparing?'<p>Готовится список файлов и снимок метаданных…</p>':''}${report.error||state.prepareError?`<p class="form-error">${esc(report.error||state.prepareError)}</p>`:''}<small class="field-note">Проценты показывают последние подтверждённые копии. Без связи новые копии и удаления ждут подключения.</small>`;
  }catch(error){target.textContent=error.message;}
 };
 root.querySelector('#replica-refresh').onclick=refresh;
 root.querySelector('#replica-key').onclick=async()=>{
  if(!confirm('Создать новый ключ чтения всех файлов для вашего ноутбука? Старый ключ перестанет работать; локальные копии останутся.'))return;
  const button=root.querySelector('#replica-key');button.disabled=true;
  try{
   const {key}=await(await request('admin/replica/key',{method:'PUT',headers:{'Content-Type':'application/json'},body:'{}'})).json();
   const panel=root.querySelector('#replica-key-panel');panel.innerHTML=`<label class="field-label" for="replica-secret">Ключ подключения · показывается только сейчас</label><input class="text-input" id="replica-secret" readonly value="${esc(key)}"><button class="button secondary" id="replica-copy">Скопировать ключ</button><button class="button secondary" id="replica-hide">Скрыть ключ</button><p class="field-note">Вставьте его в установщик на ноуте. Ключ разрешает чтение всех резервируемых данных; не отправляйте его в чат и не публикуйте.</p>`;
   panel.querySelector('#replica-copy').onclick=async()=>{try{await navigator.clipboard.writeText(key);panel.querySelector('#replica-copy').textContent='Скопировано';}catch{panel.querySelector('input').select();}};
   panel.querySelector('#replica-hide').onclick=()=>panel.replaceChildren();
   await refresh();
  }catch(error){target.textContent=error.message;}
  finally{button.disabled=false;}
 };
 await refresh();
 const timer=setInterval(()=>{if(!root.isConnected)clearInterval(timer);else if(!document.hidden)refresh();},15000);
}
