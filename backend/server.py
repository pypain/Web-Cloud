#!/usr/bin/env python3
"""Single-owner file storage. Private Unix socket, authenticated nginx in front."""
import hmac
import hashlib
import base64
import struct
import secrets
import html
import json
import os
import pathlib
import re
import shutil
import socket
import socketserver
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote

ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
SAFE_TYPES = {'text/plain', 'application/pdf', 'image/png', 'image/jpeg', 'image/gif',
              'image/webp', 'image/avif', 'audio/mpeg', 'audio/ogg', 'audio/wav',
              'video/mp4', 'video/webm', 'audio/mp4', 'video/ogg'}


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Store:
    def __init__(self, root, token, quota=20*1024**3, max_file=5*1024**3, reserve=10*1024**3):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.root / 'files'
        self.files.mkdir(exist_ok=True, mode=0o700)
        self.parts = self.root / 'uploads'
        self.parts.mkdir(exist_ok=True, mode=0o700)
        self.token, self.quota, self.max_file, self.reserve = token, quota, max_file, reserve
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, entries TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO state VALUES (1, 0, '[]')")
            db.execute('CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, meta TEXT NOT NULL, total INTEGER NOT NULL, offset INTEGER NOT NULL, hashes TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cancelled_uploads (id TEXT PRIMARY KEY, updated REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, settings TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO profile VALUES (1,0,?)',(json.dumps({'name':'Личное пространство','bio':'','collection':'Мой архив','accent':'copper','motion':True,'avatar':''}),))
            db.execute('CREATE TABLE IF NOT EXISTS shares (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, file_id TEXT NOT NULL, expires INTEGER NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.root / 'state.sqlite', timeout=30)

    def state(self):
        with self.connect() as db:
            version, entries = db.execute('SELECT version, entries FROM state WHERE id=1').fetchone()
        return {'version': version, 'entries': json.loads(entries), 'quota': self.quota, 'maxFile': self.max_file, 'chunkSize': 4*1024**2}

    def path(self, ident):
        if not ID.fullmatch(ident):
            raise Problem(400, 'Invalid file ID')
        return self.files / ident

    def visible_file(self, ident, entries=None):
        entries=self.state()['entries'] if entries is None else entries
        by_id={e['id']:e for e in entries}; entry=by_id.get(ident); cursor=entry
        if not entry or entry['kind']!='file': return None
        while cursor:
            if cursor.get('deleted'): return None
            cursor=by_id.get(cursor['parent'])
        return entry

    def share_records(self):
        with self.connect() as db: rows=list(db.execute('SELECT id,token,file_id,expires FROM shares ORDER BY expires DESC'))
        entries=self.state()['entries'];visibility={}
        for _,_,file_id,_ in rows:
            if file_id not in visibility:visibility[file_id]=bool(self.visible_file(file_id,entries))
        return [{'id':ident,'fileId':file_id,'path':'/cloud/share/'+token,'expires':expires,
                 'active':expires>time.time() and visibility[file_id]}
                for ident,token,file_id,expires in rows]

    def used(self):
        with self.connect() as db:
            pending = dict(db.execute('SELECT id, total FROM uploads'))
        return sum(p.stat().st_size for p in self.files.iterdir() if p.is_file() and p.name not in pending) + sum(pending.values())

    def cleanup_uploads(self):
        live = {e['id'] for e in self.state()['entries']}
        with self.connect() as db:
            db.execute('DELETE FROM cancelled_uploads WHERE updated<?',(time.time()-7*86400,))
            expired = list(db.execute('SELECT id FROM uploads WHERE updated<?', (time.time()-7*86400,)))
            for (ident,) in expired:
                (self.parts / ident).unlink(missing_ok=True)
                if ident not in live: self.path(ident).unlink(missing_ok=True)
                db.execute('DELETE FROM uploads WHERE id=?', (ident,))

    def upload(self, ident):
        self.path(ident)
        existing = next((e for e in self.state()['entries'] if e['id']==ident), None)
        if existing: return {'status':'done','offset':existing['size'],'entry':existing}
        with self.connect() as db:
            row = db.execute('SELECT meta,total,offset,hashes FROM uploads WHERE id=?',(ident,)).fetchone()
        if not row: raise Problem(404, 'Upload expired or not found')
        meta=json.loads(row[0]); protocol=meta.get('_protocol',1)
        return {'status':'pending','meta':meta,'total':row[1], 'offset':row[2], 'hashes':json.loads(row[3]), 'protocol':protocol, 'chunkSize':(32 if protocol==2 else 4)*1024**2}

    def create_upload(self, ident, meta):
        self.cleanup_uploads()
        self.path(ident)
        if not isinstance(meta,dict): raise Problem(400,'Invalid upload')
        meta=dict(meta)
        protocol=meta.pop('protocol',1)
        meta.pop('_protocol',None)
        if type(protocol) is not int or protocol not in (1,2): raise Problem(400,'Invalid upload protocol')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM cancelled_uploads WHERE id=?',(ident,)).fetchone(): raise Problem(410,'Upload cancelled; start a new upload')
        try:
            existing = self.upload(ident)
            if existing['status']=='done': return existing
            if {key:value for key,value in existing['meta'].items() if key not in ('_protocol','protocol')} != meta: raise Problem(409, 'Upload identity changed')
            return existing
        except Problem as error:
            if error.code!=404: raise
        if not isinstance(meta,dict): raise Problem(400,'Invalid upload')
        name, size, parent = meta.get('name'), meta.get('size'), meta.get('parent')
        if not isinstance(name,str) or not name.strip() or len(name)>200 or re.search(r'[/\\\x00-\x1f\x7f]',name) or name in ('.','..'):
            raise Problem(400,'Invalid name')
        if type(size) is not int or size<0 or size>self.max_file: raise Problem(413,'File too large')
        if not isinstance(meta.get('fingerprint'),str) or not re.fullmatch(r'[a-f0-9]{64}',meta['fingerprint']): raise Problem(400,'Invalid fingerprint')
        entries = self.state()['entries']
        if parent is not None and not any(e['id']==parent and e['kind']=='folder' and not e.get('deleted') for e in entries):
            raise Problem(400,'Destination folder unavailable')
        if self.used()+size>self.quota: raise Problem(413,'Storage quota exceeded')
        if shutil.disk_usage(self.root).free-size<self.reserve: raise Problem(507,'Host disk reserve reached')
        with self.connect() as db:
            if db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]>=10000: raise Problem(413,'Too many pending uploads')
            stored_meta={**meta,'_protocol':2} if protocol==2 else meta
            db.execute('INSERT INTO uploads (id,meta,total,offset,hashes,updated) VALUES (?,?,?,0,?,?)',(ident,json.dumps(stored_meta),size,'[]',time.time()))
        return self.upload(ident)

    def append_upload(self, ident, offset, body, digest):
        item=self.upload(ident)
        if item['status']=='done': return item
        if offset!=item['offset']: raise Problem(409,'Chunk offset changed; check upload status')
        expected=min(item['chunkSize'],item['total']-offset)
        if not len(body) or len(body)>expected or (item['protocol']==1 and len(body)!=expected): raise Problem(400,'Invalid chunk length')
        if not hmac.compare_digest(hashlib.sha256(body).hexdigest(),digest): raise Problem(400,'Chunk checksum mismatch')
        if shutil.disk_usage(self.root).free-len(body)<self.reserve: raise Problem(507,'Host disk reserve reached')
        path=self.parts/ident
        with path.open('r+b' if path.exists() else 'w+b') as stream:
            if os.fstat(stream.fileno()).st_size<offset: raise Problem(409,'Upload data missing; start a new upload')
            stream.truncate(offset); stream.seek(offset); stream.write(body); stream.flush(); os.fsync(stream.fileno())
        with self.connect() as db:
            db.execute('UPDATE uploads SET offset=?,hashes=?,updated=? WHERE id=?',
                       (offset+len(body),json.dumps(item['hashes']+[{'size':len(body),'sha256':digest} if item['protocol']==2 else digest]),time.time(),ident))
        return self.upload(ident)

    def finish_upload(self, ident):
        item=self.upload(ident)
        if item['status']=='done': return item
        if item['offset']!=item['total']: raise Problem(409,'Upload is incomplete')
        path=self.parts/ident; final=self.path(ident)
        if not path.exists() and final.exists(): path=final  # Retry after a crash between rename and SQLite commit.
        if item['total']==0 and not path.exists(): path.touch(mode=0o600)
        with path.open('rb') as stream:
            for digest in item['hashes']:
                size=digest['size'] if isinstance(digest,dict) else 4*1024**2
                expected=digest['sha256'] if isinstance(digest,dict) else digest
                if hashlib.sha256(stream.read(size)).hexdigest()!=expected: raise Problem(409,'Stored chunk failed integrity check')
            if stream.read(1) or path.stat().st_size!=item['total']: raise Problem(409,'Stored file length changed')
        if path!=final: os.replace(path,final)
        state=self.state(); meta=item['meta']; name=meta['name']; parent=meta.get('parent')
        if parent is not None and not any(e['id']==parent and e['kind']=='folder' and not e.get('deleted') for e in state['entries']):
            raise Problem(409,'Destination folder changed; restore it before retrying')
        names={e['name'] for e in state['entries'] if e['parent']==parent and not e.get('deleted')}
        base,ext=os.path.splitext(name); number=1
        while name in names:
            suffix=f' ({number})'; ending=ext[:max(0,200-len(suffix)-1)]
            name=f'{base[:200-len(suffix)-len(ending)]}{suffix}{ending}'; number+=1
        mime=meta.get('mime'); mime=mime if isinstance(mime,str) and mime in SAFE_TYPES else 'application/octet-stream'
        entry={'id':ident,'name':name,'kind':'file','parent':parent,'modified':int(time.time()*1000),'size':item['total'],'mime':mime,'_stored':True}
        state['entries'].append(entry)
        directory=os.open(self.files,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        with self.connect() as db:
            db.execute('UPDATE state SET version=?,entries=? WHERE id=1',(state['version']+1,json.dumps(state['entries'])))
            db.execute('DELETE FROM uploads WHERE id=?',(ident,))
        return {'status':'done','offset':item['total'],'entry':entry}

    def cancel_upload(self, ident):
        try: item=self.upload(ident)
        except Problem as error:
            if error.code!=404: raise
            item={'status':'pending'}
        if item['status']=='done': return item
        (self.parts/ident).unlink(missing_ok=True)
        self.path(ident).unlink(missing_ok=True)
        with self.connect() as db:
            db.execute('DELETE FROM uploads WHERE id=?',(ident,))
            db.execute('INSERT OR REPLACE INTO cancelled_uploads VALUES (?,?)',(ident,time.time()))
        return {'status':'cancelled'}

    def cleanup_staged(self):
        live = {e['id'] for e in self.state()['entries'] if e['kind'] == 'file'}
        with self.connect() as db: live.update(ident for (ident,) in db.execute('SELECT id FROM uploads'))
        cutoff = time.time() - 24*3600
        for path in self.files.iterdir():
            if path.name not in live and path.stat().st_mtime < cutoff:
                path.unlink()

    def validate(self, entries):
        if not isinstance(entries, list) or len(entries) > 10000:
            raise Problem(400, 'Too many entries')
        cleaned, seen = [], set()
        for e in entries:
            if not isinstance(e, dict): raise Problem(400, 'Invalid entry')
            ident, name, kind = e.get('id', ''), e.get('name', ''), e.get('kind')
            if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in seen:
                raise Problem(400, 'Invalid or duplicate ID')
            seen.add(ident)
            if not isinstance(name, str) or not name.strip() or len(name) > 200 or name in ('.', '..') or re.search(r'[/\\\x00-\x1f\x7f]', name):
                raise Problem(400, 'Invalid name')
            if kind not in ('file', 'folder'): raise Problem(400, 'Invalid kind')
            parent = e.get('parent')
            if parent is not None and (not isinstance(parent, str) or not ID.fullmatch(parent)):
                raise Problem(400, 'Invalid parent')
            modified = e.get('modified')
            if type(modified) not in (int, float) or not 0 <= modified <= 1e15:
                raise Problem(400, 'Invalid date')
            item = {'id': ident, 'name': name, 'kind': kind, 'parent': parent, 'modified': modified, 'size': 0}
            if kind == 'file':
                path = self.path(ident)
                if not path.is_file(): raise Problem(400, 'Upload file content first')
                item['size'] = path.stat().st_size
                mime = e.get('mime')
                item['mime'] = mime if isinstance(mime, str) and mime in SAFE_TYPES else 'application/octet-stream'
                item['_stored'] = True
            if 'deleted' in e:
                if type(e['deleted']) not in (int, float) or not 0 < e['deleted'] <= 1e15:
                    raise Problem(400, 'Invalid deletion date')
                item['deleted'] = e['deleted']
            if e.get('shared') is True: item['shared'] = True
            cleaned.append(item)
        by_id = {e['id']: e for e in cleaned}
        names = set()
        for e in cleaned:
            parent = e['parent']
            if parent is not None and (parent not in by_id or by_id[parent]['kind'] != 'folder'):
                raise Problem(400, 'Missing parent folder')
            chain, cursor = set(), e
            while cursor is not None:
                if cursor['id'] in chain: raise Problem(400, 'Folder cycle')
                chain.add(cursor['id'])
                cursor = by_id.get(cursor['parent'])
            if not e.get('deleted'):
                key = (e['parent'], e['name'])
                if key in names: raise Problem(400, 'Duplicate name')
                names.add(key)
        return cleaned


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass  # Never log tokens, filenames or query parameters.

    def setup(self):
        super().setup()
        self.connection.settimeout(90)

    def reply(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def public_page(self, code, title, details='', download='', entry=None, text=None, expires=None):
        from string import Template
        nonce=secrets.token_urlsafe(18)
        preview_url=download.removesuffix('/download')+'/preview' if download else ''
        mime=entry.get('mime','') if entry else ''
        escaped_url=html.escape(preview_url,quote=True)
        if text is not None:
            content='<pre class="text-preview" aria-label="Содержимое файла">'+html.escape(text)+'</pre>'
            if entry['size']>200*1024:content+='<p class="hint">Показаны первые 200 КБ. Полное содержимое доступно при скачивании.</p>'
            kind='Текст'
        elif mime.startswith('image/'):
            content=f'<img class="preview-media image-preview" src="{escaped_url}" alt="Предпросмотр изображения">';kind='Изображение'
        elif mime.startswith('video/'):
            content=f'<video class="preview-media" controls playsinline preload="metadata" src="{escaped_url}"></video>';kind='Видео'
        elif mime.startswith('audio/'):
            content=f'<div class="audio-art" aria-hidden="true">♫</div><audio class="preview-media" controls preload="metadata" src="{escaped_url}"></audio>';kind='Аудио'
        elif mime=='application/pdf':
            content=f'<iframe class="pdf-preview" src="{escaped_url}" title="Предпросмотр PDF" sandbox></iframe><p class="hint">Если браузер не показывает PDF, откройте его отдельно или скачайте файл.</p>';kind='PDF'
        else:
            content='<div class="unsupported"><span aria-hidden="true">▤</span><h2>Предпросмотр недоступен</h2><p>Скачайте файл и откройте его на своём устройстве.</p></div>';kind='Файл'
        size=entry['size'] if entry else 0
        unit='Б';number=float(size)
        for next_unit in ('КБ','МБ','ГБ'):
            if number<1024:break
            number/=1024;unit=next_unit
        size_label=(f'{number:.1f}'.replace('.',',') if unit!='Б' else str(size))+' '+unit
        expiry=time.strftime('%d.%m.%Y, %H:%M МСК',time.gmtime(expires+10800)) if expires else ''
        if entry:
            panel=f'''<section class="preview"><div class="preview-header"><h2>Предпросмотр</h2><span>{kind}</span></div><div class="preview-body">{content}</div><p id="preview-status" class="hint" role="status"></p></section>
            <aside class="file-card"><span class="badge">Общий файл</span><h1>{html.escape(title)}</h1><dl><div><dt>Размер</dt><dd>{size_label}</dd></div><div><dt>Формат</dt><dd>{html.escape(kind)}</dd></div><div><dt>Ссылка действует до</dt><dd>{expiry}</dd></div></dl>
            <a class="download" href="{html.escape(download,quote=True)}"><span aria-hidden="true">↓</span> Скачать файл</a><button class="copy" id="copy-link">Копировать ссылку</button><input id="copy-value" readonly hidden aria-label="Ссылка на файл"><p id="copy-status" class="hint" role="status"></p>
            <details class="actions"><summary>Другие действия</summary>{f'<a href="{escaped_url}" target="_blank" rel="noopener noreferrer">Открыть предпросмотр отдельно ↗</a>' if mime in SAFE_TYPES else ''}<a href="{html.escape(download,quote=True)}">Скачать оригинал</a></details><p class="privacy">Доступ предоставлен только к этому файлу. Владелец может отозвать ссылку.</p></aside>'''
        else:
            panel=f'<section class="error-card"><span class="badge">Общий файл</span><h1>{html.escape(title)}</h1><p>{html.escape(details)}</p><p class="hint">Попросите владельца прислать новую ссылку.</p></section>'
        template=Template('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#1c1d1a"><title>$title · Облако</title><style>
        *{box-sizing:border-box}body{margin:0;background:#1c1d1a;color:#eee8dc;font:14px system-ui,-apple-system,sans-serif}a,button{font:inherit}a{color:inherit;text-decoration:none}button{cursor:pointer}header{border-bottom:1px solid #3b3c34;padding:22px max(24px,calc((100vw - 1180px)/2));display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{font-weight:700;display:flex;gap:12px;align-items:center}.brand-icon{display:grid;place-items:center;width:34px;height:34px;background:#332b24;color:#dfa080;border-radius:10px;font-size:23px}.header-note{color:#a09ba9;font-size:12px}main{max-width:1228px;margin:0 auto;padding:44px 24px 60px}.breadcrumb{color:#aaa89c;font-size:12px;margin-bottom:24px}.grid{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:24px;align-items:start}.preview,.file-card,.error-card{background:#242520;border:1px solid #3b3c34;border-radius:16px;overflow:hidden}.preview-header{padding:18px 22px;border-bottom:1px solid #3b3c34;display:flex;justify-content:space-between;align-items:center}.preview-header h2{margin:0;font-size:14px}.preview-header span,.badge{font-size:11px;color:#dfa080;background:#332b24;padding:5px 9px;border-radius:6px}.preview-body{min-height:360px;padding:20px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#191a17}.image-preview{object-fit:contain;max-height:65vh}.preview-media{width:100%;max-width:100%;border-radius:9px}video{max-height:65vh;background:#000}audio{margin-top:30px}.audio-art{display:grid;place-items:center;background:linear-gradient(135deg,#46365e,#262d42);width:150px;height:150px;border-radius:25px;color:#d9c5ff;font-size:68px}.text-preview{align-self:stretch;margin:0;width:100%;max-height:65vh;overflow:auto;padding:16px;border:1px solid #3b3c34;border-radius:9px;background:#20211c;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.75 ui-monospace,monospace;color:#eee8dc}.pdf-preview{width:100%;height:65vh;min-height:400px;border:0;background:white;border-radius:8px}.unsupported{text-align:center;padding:40px 16px;color:#a7a1b0}.unsupported>span{font-size:60px;color:#dfa080}.unsupported h2{font-size:18px;color:#ddd7e8}.file-card{padding:24px}h1{font-size:24px;line-height:1.35;letter-spacing:-.5px;overflow-wrap:anywhere;margin:18px 0 22px}dl{margin:0 0 26px}dl>div{display:flex;justify-content:space-between;gap:12px;margin:14px 0;font-size:12px}dt{color:#aaa89c;flex-shrink:0}dd{margin:0;text-align:right;overflow-wrap:anywhere}.download,.copy{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:13px;border-radius:9px;font-weight:650}.download{background:#dfa080;color:#211d18}.download:hover{background:#ebbaa0}.copy{margin-top:10px;border:1px solid #45404f;background:#2b2c26;color:#d1cadc}.copy:hover{background:#37352d}.hint,.privacy{color:#aaa89c;font-size:12px;line-height:1.6}.hint{padding:0 20px}.file-card .hint{padding:0}.privacy{margin-top:24px;border-top:1px solid #3b3c34;padding-top:18px}.actions{margin-top:20px;font-size:12px}.actions summary{color:#bdb4ce;cursor:pointer}.actions a{display:block;margin-top:12px;color:#dfa080}#copy-value{width:100%;margin-top:12px;background:#191a17;color:#ddd;border:1px solid #514462;border-radius:6px;padding:10px}.error-card{grid-column:1/-1;max-width:600px;margin:40px auto;padding:32px;text-align:center}.error-card p{line-height:1.7;color:#aaa4b4}.error-card .hint{padding:0}footer{color:#716c7b;font-size:11px;text-align:center;margin-top:32px}a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible{outline:2px solid #dfa080;outline-offset:4px}.grid{animation:appear .25s ease}a,button{transition:background .18s,transform .18s}.download:active{transform:scale(.98)}@keyframes appear{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}@media(max-width:800px){main{padding:24px 14px}.grid{grid-template-columns:1fr}.file-card{grid-row:1}.file-card h1{margin:14px 0;font-size:22px}dl{margin-bottom:18px}.preview-body{min-height:240px;padding:12px}.header-note{display:none}header{padding:18px 20px}.image-preview,video{max-height:55vh}.pdf-preview{height:55vh;min-height:320px}.text-preview{max-height:55vh}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.download:active{transform:none}}

 h1,.brand{font-family:Georgia,"Times New Roman",serif;font-weight:400}.preview,.file-card,.error-card{border-radius:8px}.download,.copy{border-radius:5px}
</style></head><body><header><div class="brand"><span class="brand-icon" aria-hidden="true">☁</span> Личное облако</div><span class="header-note">Файл по ссылке · без регистрации</span></header><main><div class="breadcrumb">Облако / Общий файл</div><div class="grid">$panel</div><footer>Файл доступен по ссылке до истечения срока или отзыва владельцем.</footer></main><script nonce="$nonce">
        const copy=document.getElementById('copy-link');if(copy)copy.addEventListener('click',async()=>{const url=location.origin+location.pathname;const status=document.getElementById('copy-status');try{await navigator.clipboard.writeText(url);status.textContent='Ссылка скопирована';}catch{const input=document.getElementById('copy-value');input.hidden=false;input.value=url;input.focus();input.select();status.textContent='Скопируйте выделенную ссылку вручную';}});
        document.querySelectorAll('.preview-media').forEach(media=>media.addEventListener('error',()=>{document.getElementById('preview-status').textContent='Браузер не смог открыть предпросмотр. Скачайте файл или попробуйте другой браузер.';}));
        </script></body></html>''')
        body=template.substitute(title=html.escape(title),panel=panel,nonce=nonce).encode()
        self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Robots-Tag','noindex, nofollow');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; img-src 'self'; media-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        if self.command!='HEAD': self.wfile.write(body)

    def handle_request(self, write=False):
        try:
            store = self.server.store
            if not hmac.compare_digest(self.headers.get('X-Cloud-Token', '').encode(), store.token.encode()):
                raise Problem(403, 'Forbidden')
            if write and self.headers.get('X-Cloud-Request') != '1':
                raise Problem(403, 'Missing same-origin request header')
            if write and self.headers.get('Origin') and self.headers['Origin'] != self.server.origin:
                raise Problem(403, 'Wrong request origin')
            parsed = urlsplit(self.path)
            path = parsed.path
            share_token=None
            public=self.headers.get('X-Cloud-Public')=='1'
            if public and (write or not path.startswith('/share/')): raise Problem(403,'Forbidden')
            if path.startswith('/share/'):
                if write: raise Problem(405,'Not allowed')
                fields=path[len('/share/'):].split('/')
                if len(fields)>2 or (len(fields)==2 and fields[1] not in ('download','preview')) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',fields[0]): raise Problem(404,'Link unavailable')
                with store.lock, store.connect() as db:
                    row=db.execute('SELECT file_id,expires FROM shares WHERE token=?',(fields[0],)).fetchone()
                    entry=store.visible_file(row[0]) if row and row[1]>time.time() else None
                    if not entry or not store.path(entry['id']).is_file(): raise Problem(404,'Link unavailable')
                    if len(fields)==1:
                        text=None
                        if entry['mime']=='text/plain' or pathlib.Path(entry['name']).suffix.lower() in ('.txt','.md','.json','.csv','.log','.yaml','.yml','.xml','.ini','.conf','.py','.pyw','.js','.mjs','.cjs','.ts','.tsx','.jsx','.c','.h','.cpp','.hpp','.cc','.java','.cs','.go','.rs','.rb','.php','.sh','.bash','.zsh','.sql','.html','.htm','.css','.scss','.svg','.toml','.r','.lua','.swift','.kt'):
                            with store.path(entry['id']).open('rb') as source:text=source.read(200*1024).decode('utf-8',errors='replace')
                        return self.public_page(200,entry['name'],download='/cloud/share/'+fields[0]+'/download',entry=entry,text=text,expires=row[1])
                    share_token=fields[0]
                    if fields[1]=='preview' and entry['mime'] not in SAFE_TYPES:raise Problem(415,'Preview unavailable')
                path='/files/'+entry['id'];parsed=parsed._replace(query='download=1' if fields[1]=='download' else '')
            if path=='/shares' or path.startswith('/shares/'):
                if path=='/shares' and not write: return self.reply(200,{'shares':store.share_records()})
                ident=path[len('/shares/'):]
                if not ID.fullmatch(ident): raise Problem(400,'Invalid share ID')
                if self.command=='DELETE':
                    with store.lock, store.connect() as db: db.execute('DELETE FROM shares WHERE id=?',(ident,))
                    return self.reply(200,{'ok':True})
                with store.lock, store.connect() as db:
                    if not write: raise Problem(404,'Not found')
                    try: data=json.loads(self.read_exact(self.length(4096)))
                    except (ValueError,UnicodeError): raise Problem(400,'Invalid share request')
                    if not isinstance(data,dict) or type(data.get('ttl')) is not int or data['ttl'] not in (3600,86400,604800,2592000): raise Problem(400,'Invalid share lifetime')
                    file_id=data.get('fileId')
                    if not isinstance(file_id,str) or not store.visible_file(file_id): raise Problem(404,'File unavailable')
                    existing=db.execute('SELECT file_id FROM shares WHERE id=?',(ident,)).fetchone()
                    if existing and existing[0]!=file_id: raise Problem(409,'Share ID already used')
                    if not existing:
                        if db.execute('SELECT COUNT(*) FROM shares').fetchone()[0]>=10000: raise Problem(413,'Too many links')
                        db.execute('INSERT INTO shares VALUES (?,?,?,?)',(ident,secrets.token_urlsafe(32),file_id,int(time.time())+data['ttl']))
                return self.reply(200,next(s for s in store.share_records() if s['id']==ident))
            if not write and path == '/health':
                return self.reply(200, {'ok': True})
            if path=='/profile':
                with store.lock, store.connect() as db:
                    version,settings=db.execute('SELECT version,settings FROM profile WHERE id=1').fetchone()
                    if not write: return self.reply(200,{'version':version,'profile':json.loads(settings)})
                    length=self.length(160*1024)
                    try: data=json.loads(self.read_exact(length))
                    except (ValueError,UnicodeError): raise Problem(400,'Invalid profile')
                    if not isinstance(data,dict) or not isinstance(data.get('profile'),dict): raise Problem(400,'Invalid profile')
                    if type(data.get('version')) is not int or data['version']!=version: raise Problem(409,'Profile changed; reload before saving')
                    profile=data['profile'];name=profile.get('name');bio=profile.get('bio','');avatar=profile.get('avatar','');collection=profile.get('collection','Мой архив')
                    if not isinstance(name,str) or not name.strip() or len(name.strip())>40 or re.search(r'[\x00-\x1f\x7f]',name): raise Problem(400,'Invalid profile name')
                    if not isinstance(collection,str) or not collection.strip() or len(collection)>60 or re.search(r'[\x00-\x1f\x7f]',collection): raise Problem(400,'Invalid archive name')
                    if not isinstance(bio,str) or len(bio)>160 or re.search(r'[\x00-\x1f\x7f]',bio): raise Problem(400,'Invalid profile description')
                    if profile.get('accent') not in ('copper','violet','blue','mint','rose','amber','silver') or type(profile.get('motion')) is not bool: raise Problem(400,'Invalid appearance settings')
                    if not isinstance(avatar,str): raise Problem(400,'Invalid avatar')
                    if avatar:
                        if not avatar.startswith('data:image/png;base64,'): raise Problem(400,'Avatar must be PNG')
                        try: raw=base64.b64decode(avatar.split(',',1)[1],validate=True)
                        except ValueError: raise Problem(400,'Invalid avatar data')
                        if len(raw)>100*1024 or len(raw)<24 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[12:16]!=b'IHDR': raise Problem(400,'Invalid PNG avatar')
                        width,height=struct.unpack('>II',raw[16:24])
                        if not (1<=width<=256 and 1<=height<=256): raise Problem(400,'Avatar dimensions too large')
                    clean={'name':name.strip(),'collection':collection.strip(),'bio':bio.strip(),'accent':profile['accent'],'motion':profile['motion'],'avatar':avatar}
                    db.execute('UPDATE profile SET version=?,settings=? WHERE id=1',(version+1,json.dumps(clean)))
                return self.reply(200,{'version':version+1,'profile':clean})
            if path.startswith('/uploads/'):
                fields=path[len('/uploads/'):].split('/')
                if len(fields)>2: raise Problem(404,'Not found')
                ident=fields[0]; action=fields[1] if len(fields)==2 else ''
                with store.lock:
                    store.cleanup_uploads()
                    if self.command=='DELETE' and not action: result=store.cancel_upload(ident)
                    elif not write and not action: result=store.upload(ident)
                    elif write and not action:
                        length=self.length(4096)
                        try: meta=json.loads(self.read_exact(length))
                        except (ValueError,UnicodeError): raise Problem(400,'Invalid JSON')
                        result=store.create_upload(ident,meta)
                    elif write and action=='chunk':
                        length=self.length(32*1024**2)
                        offset=parse_qs(parsed.query).get('offset',[''])[0]
                        if not offset.isdigit(): raise Problem(400,'Invalid offset')
                        digest=self.headers.get('X-Chunk-SHA256','')
                        if not re.fullmatch(r'[a-f0-9]{64}',digest): raise Problem(400,'Missing checksum')
                        result=store.append_upload(ident,int(offset),self.read_exact(length),digest)
                    elif write and action=='complete': result=store.finish_upload(ident)
                    else: raise Problem(404,'Not found')
                return self.reply(200,result)
            if path == '/state':
                if not write: return self.reply(200, store.state())
                length = self.length(4*1024**2)
                if self.headers.get_content_type() != 'application/json': raise Problem(415, 'Expected JSON')
                try: data = json.loads(self.read_exact(length))
                except (ValueError, UnicodeError): raise Problem(400, 'Invalid JSON')
                if not isinstance(data, dict): raise Problem(400, 'Invalid state')
                with store.lock:
                    old = store.state()
                    if type(data.get('version')) is not int or data['version'] != old['version']:
                        raise Problem(409, 'Storage changed on another device; reload before editing')
                    entries = store.validate(data.get('entries'))
                    with store.connect() as db:
                        db.execute('UPDATE state SET version=?, entries=? WHERE id=1',
                                   (old['version']+1, json.dumps(entries)))
                        for (share_id,file_id) in db.execute('SELECT id,file_id FROM shares').fetchall():
                            if not store.visible_file(file_id,entries): db.execute('DELETE FROM shares WHERE id=?',(share_id,))
                    live = {e['id'] for e in entries if e['kind'] == 'file'}
                    # Remove only previously committed files deleted in this change.
                    for e in old['entries']:
                        if e['kind'] == 'file' and e['id'] not in live:
                            store.path(e['id']).unlink(missing_ok=True)
                    return self.reply(200, store.state())
            if path.startswith('/files/'):
                ident = path[len('/files/'):]
                file = store.path(ident)
                if write:
                    length = self.length(store.max_file)
                    with store.lock:
                        store.cleanup_staged()
                        if file.exists(): raise Problem(409, 'File contents are immutable')
                        if store.used()+length > store.quota: raise Problem(413, 'Storage quota exceeded')
                        if shutil.disk_usage(store.root).free-length < store.reserve: raise Problem(507, 'Host disk reserve reached')
                        fd, temporary = tempfile.mkstemp(prefix='upload-', dir=store.root)
                        try:
                            with os.fdopen(fd, 'wb') as stream:
                                remaining = length
                                while remaining:
                                    chunk = self.rfile.read(min(remaining, 1024*1024))
                                    if not chunk: raise Problem(400, 'Incomplete upload')
                                    stream.write(chunk); remaining -= len(chunk)
                                stream.flush(); os.fsync(stream.fileno())
                            os.replace(temporary, file)
                        finally:
                            pathlib.Path(temporary).unlink(missing_ok=True)
                    return self.reply(201, {'id': ident})
                with store.lock:
                    if share_token:
                        with store.connect() as db: active_share=db.execute('SELECT file_id,expires FROM shares WHERE token=?',(share_token,)).fetchone()
                        entry=store.visible_file(ident) if active_share and active_share[0]==ident and active_share[1]>time.time() else None
                    else: entry = next((e for e in store.state()['entries'] if e['id'] == ident and e['kind'] == 'file'), None)
                    if entry is None or not file.is_file(): raise Problem(404, 'File not found')
                    stream = file.open('rb')
                with stream:
                    size = os.fstat(stream.fileno()).st_size
                    start, end, code = 0, size-1, 200
                    range_header = self.headers.get('Range')
                    if range_header:
                        match = re.fullmatch(r'bytes=(\d+)-(\d*)', range_header)
                        if not match: raise Problem(416, 'Unsupported range')
                        start = int(match[1]); end = min(int(match[2]) if match[2] else size-1, size-1)
                        if start > end: raise Problem(416, 'Range outside file')
                        code = 206
                    self.send_response(code)
                    self.send_header('Content-Type', entry['mime'])
                    self.send_header('Content-Length', str(max(0, end-start+1)))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', 'sandbox')
                    self.send_header('Referrer-Policy','no-referrer')
                    disposition = 'attachment' if 'download' in parse_qs(parsed.query) or entry['mime'] == 'application/octet-stream' else 'inline'
                    self.send_header('Content-Disposition', f"{disposition}; filename*=UTF-8''{quote(entry['name'], safe='')}")
                    if code == 206: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    if self.command=='HEAD': return
                    stream.seek(start); remaining = max(0, end-start+1)
                    while remaining:
                        chunk = stream.read(min(remaining, 1024*1024))
                        if not chunk: break
                        self.wfile.write(chunk); remaining -= len(chunk)
                return
            raise Problem(404, 'Not found')
        except Problem as error:
            if self.headers.get('X-Cloud-Public')=='1': self.public_page(error.code,'Ссылка недоступна','Срок действия истёк, файл удалён или владелец отозвал доступ.')
            else: self.reply(error.code, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.reply(500, {'error': 'Storage operation failed'})

    def length(self, limit):
        value = self.headers.get('Content-Length', '')
        if not value.isdigit() or self.headers.get('Transfer-Encoding'):
            raise Problem(411, 'Content-Length required')
        length = int(value)
        if length > limit: raise Problem(413, 'File or request too large')
        return length

    def read_exact(self, length):
        body = self.rfile.read(length)
        if len(body) != length: raise Problem(400, 'Incomplete request')
        return body

    def do_GET(self): self.handle_request()
    def do_HEAD(self):
        if not urlsplit(self.path).path.startswith('/share/'): return self.reply(405,{'error':'Not allowed'})
        self.handle_request()
    def do_PUT(self): self.handle_request(write=True)
    def do_DELETE(self):
        if not urlsplit(self.path).path.startswith(('/uploads/','/shares/')): return self.reply(405,{'error':'Not allowed'})
        self.handle_request(write=True)


class UnixServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = 'localhost', 0


if __name__ == '__main__':
    import grp
    token = pathlib.Path(os.environ['CLOUD_TOKEN_FILE']).read_text().strip()
    if len(token) < 32: raise SystemExit('Invalid backend token')
    address = os.environ.get('CLOUD_SOCKET', '/run/hxry-cloud/backend.sock')
    pathlib.Path(address).unlink(missing_ok=True)
    server = UnixServer(address, Handler)
    os.chown(address, -1, grp.getgrnam('www-data').gr_gid)
    os.chmod(address, 0o660)
    server.store = Store(os.environ.get('CLOUD_DATA', '/var/lib/hxry-cloud'), token,
                         quota=int(os.environ.get('CLOUD_QUOTA', 20*1024**3)),
                         max_file=int(os.environ.get('CLOUD_MAX_FILE', 5*1024**3)))
    server.origin = os.environ.get('CLOUD_ORIGIN', 'https://hxry.ru')
    def housekeeping():
        while True:
            try:
                with server.store.lock:
                    server.store.cleanup_uploads()
                    server.store.cleanup_staged()
            except Exception:
                print('Cloud housekeeping failed; check storage availability', flush=True)
            time.sleep(3600)
    threading.Thread(target=housekeeping, daemon=True).start()
    server.serve_forever()
