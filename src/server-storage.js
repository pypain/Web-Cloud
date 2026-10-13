export const serverStorage = import.meta.env.VITE_SERVER_STORAGE === '1';
const api = `${import.meta.env.BASE_URL}api/`;
let version = 0;
export let serverQuota = 20 * 1024 ** 3;
export let serverMaxFile = 5 * 1024 ** 3;
async function request(path, options = {}) {
  const response = await fetch(api + path, { ...options, headers: { 'X-Cloud-Request': '1', ...options.headers } });
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try { const data = await response.json(); message = data.error || message; } catch {}
    if (response.status === 409) message = 'Хранилище изменилось в другой вкладке. Обновите страницу перед редактированием.';
    if (response.status === 413) message = 'Превышен лимит файла или объём хранилища.';
    if (response.status === 507) message = 'Недостаточно свободного места на сервере.';
    throw new Error(message);
  }
  return response;
}
export async function loadServer() {
  const data = await (await request('state')).json();
  version = data.version; serverQuota = data.quota; serverMaxFile = data.maxFile;
  return data;
}
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
