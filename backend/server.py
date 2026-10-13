#!/usr/bin/env python3
"""Single-owner file storage. Private Unix socket, authenticated nginx in front."""
import hmac
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
        self.token, self.quota, self.max_file, self.reserve = token, quota, max_file, reserve
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, entries TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO state VALUES (1, 0, '[]')")

    def connect(self):
        return sqlite3.connect(self.root / 'state.sqlite', timeout=30)

    def state(self):
        with self.connect() as db:
            version, entries = db.execute('SELECT version, entries FROM state WHERE id=1').fetchone()
        return {'version': version, 'entries': json.loads(entries), 'quota': self.quota, 'maxFile': self.max_file}

    def path(self, ident):
        if not ID.fullmatch(ident):
            raise Problem(400, 'Invalid file ID')
        return self.files / ident

    def used(self):
        return sum(p.stat().st_size for p in self.files.iterdir() if p.is_file())

    def cleanup_staged(self):
        live = {e['id'] for e in self.state()['entries'] if e['kind'] == 'file'}
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
            if not hmac.compare_digest(self.headers.get('X-Cloud-Token', ''), store.token):
                raise Problem(403, 'Forbidden')
            if write and self.headers.get('X-Cloud-Request') != '1':
                raise Problem(403, 'Missing same-origin request header')
            if write and self.headers.get('Origin') and self.headers['Origin'] != self.server.origin:
                raise Problem(403, 'Wrong request origin')
            parsed = urlsplit(self.path)
            path = parsed.path
            if not write and path == '/health':
                return self.reply(200, {'ok': True})
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
    server.serve_forever()
