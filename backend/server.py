#!/usr/bin/env python3
"""Single-owner file storage. Private Unix socket, authenticated nginx in front."""
import hmac
import hashlib
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
            if not write and path == '/health':
                return self.reply(200, {'ok': True})
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
                    entry = next((e for e in store.state()['entries'] if e['id'] == ident and e['kind'] == 'file'), None)
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
                    disposition = 'attachment' if 'download' in parse_qs(parsed.query) or entry['mime'] == 'application/octet-stream' else 'inline'
                    self.send_header('Content-Disposition', f"{disposition}; filename*=UTF-8''{quote(entry['name'], safe='')}")
                    if code == 206: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    stream.seek(start); remaining = max(0, end-start+1)
                    while remaining:
                        chunk = stream.read(min(remaining, 1024*1024))
                        if not chunk: break
                        self.wfile.write(chunk); remaining -= len(chunk)
                return
            raise Problem(404, 'Not found')
        except Problem as error:
            self.reply(error.code, {'error': error.message})
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
    def do_PUT(self): self.handle_request(write=True)
    def do_DELETE(self):
        if not urlsplit(self.path).path.startswith('/uploads/'): return self.reply(405,{'error':'Not allowed'})
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
