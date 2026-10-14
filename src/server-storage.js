export const serverStorage = import.meta.env.VITE_SERVER_STORAGE === '1';
const api = `${import.meta.env.BASE_URL}api/`;
let version = 0;
export let serverQuota = 20 * 1024 ** 3;
export let serverMaxFile = 5 * 1024 ** 3;
export async function request(path, options = {}) {
  let response;
  try { response = await fetch(api + path, { ...options, headers: { 'X-Cloud-Request': '1', ...options.headers } }); }
  catch(error) {
    if(error.name==='AbortError')throw error;
    const failure=new Error('Соединение прервано. Подтверждённые части сохранены на сервере.'); failure.network=true; throw failure;
  }
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try { const data = await response.json(); message = data.error || message; } catch {}
    if (response.status === 409) message = path==='profile' ? 'Профиль изменён в другой вкладке. Обновите страницу перед сохранением.' : 'Хранилище изменилось в другой вкладке. Обновите страницу перед редактированием.';
    if (response.status === 413) message = 'Превышен лимит файла или объём хранилища.';
    if (response.status === 507) message = 'Недостаточно свободного места на сервере.';
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response;
}
export async function loadServer() {
  const data = await (await request('state')).json();
  version = data.version; serverQuota = data.quota; serverMaxFile = data.maxFile;
  const shares=await loadShares();
  for(const entry of data.entries) { if(shares.some(link=>link.fileId===entry.id&&link.active))entry.shared=true;else delete entry.shared; }
  return data;
}
export async function loadShares() { return (await (await request('shares')).json()).shares; }
export async function createShare(fileId,ttl) { return (await request(`shares/${crypto.randomUUID()}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId,ttl})})).json(); }
export async function revokeShare(id) { await request(`shares/${id}`,{method:'DELETE'}); }
export async function saveServer(entries) {
  for (const entry of entries) {
    if (entry.kind === 'file' && entry.blob && !entry._stored) {
      if (entry.blob.size > serverMaxFile) throw new Error(`Файл «${entry.name}» превышает лимит ${Math.round(serverMaxFile / 1024 ** 3)} ГБ.`);
      await request(`files/${entry.id}`, { method: 'PUT', body: entry.blob });
      entry._stored = true;
    }
  }
  const metadata = entries.map(({ blob, ...entry }) => entry);
  const result = await (await request('state', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version, entries: metadata }) })).json();
  version = result.version;
}
export function serverFile(entry, download = false) {
  return api + `files/${entry.id}` + (download ? '?download=1' : '');
}
export async function serverText(entry) {
  const response = await request(`files/${entry.id}`, { headers: entry.size ? { Range: 'bytes=0-199999' } : {} });
  return response.text();
}
