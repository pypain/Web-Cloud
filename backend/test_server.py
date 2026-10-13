import http.client
import hashlib
import json
import pathlib
import socket
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from server import Store, Handler, UnixServer


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, 'test-token', quota=100, max_file=80, reserve=0)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = self.store
        self.server.origin = 'https://hxry.ru'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, auth=True):
        h = {'X-Cloud-Request': '1'}
        if auth: h['X-Cloud-Token'] = 'test-token'
        if isinstance(body, dict):
            body = json.dumps(body); h['Content-Type'] = 'application/json'
        h.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request(method, path, body=body, headers=h)
        response = connection.getresponse(); content = response.read()
        result = response.status, dict(response.getheaders()), content
        connection.close()
        return result

    def entry(self, kind='file', name='hello.txt', parent=None, content=b'hello world'):
        e = {'id': str(uuid.uuid4()), 'kind': kind, 'name': name, 'parent': parent, 'modified': 1, 'mime': 'text/plain'}
        if kind == 'file': self.assertEqual(self.request('PUT', '/files/'+e['id'], content)[0], 201)
        return e

    def save(self, entries, version=0):
        return self.request('PUT', '/state', {'version': version, 'entries': entries})

    def test_auth_origin_and_csrf(self):
        self.assertEqual(self.request('GET', '/state', auth=False)[0], 403)
        for headers in ({'Origin': 'https://evil.example'}, {'X-Cloud-Request': ''}):
            self.assertEqual(self.request('PUT', '/state', {}, headers)[0], 403)

    def test_upload_download_ranges_and_restart(self):
        e = self.entry(); self.assertEqual(self.save([e])[0], 200)
        status, headers, body = self.request('GET', '/files/'+e['id'], headers={'Range':'bytes=2-5'})
        self.assertEqual((status, body), (206, b'llo '))
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/11')
        self.assertEqual(self.request('GET', '/files/'+e['id'], headers={'Range':'bytes=99-'})[0], 416)
        self.server.store = Store(self.temp.name, 'test-token', reserve=0)
        self.assertEqual(self.request('GET', '/files/'+e['id'])[2], b'hello world')
        self.assertEqual(json.loads(self.request('GET', '/state')[2])['entries'][0]['name'], e['name'])

    def test_conflict_cannot_erase_files(self):
        e = self.entry(); self.assertEqual(self.save([e])[0], 200)
        self.assertEqual(self.save([], 0)[0], 409)
        self.assertEqual(self.request('GET', '/files/'+e['id'])[0], 200)
        self.assertEqual(self.save([], 1)[0], 200)
        self.assertEqual(self.request('GET', '/files/'+e['id'])[0], 404)
        self.assertEqual(self.store.used(), 0)

    def test_limits_and_immutable_content(self):
        e = self.entry(content=b'x'*60)
        self.assertEqual(self.request('PUT','/files/'+e['id'], b'new')[0], 409)
        self.assertEqual(self.request('PUT','/files/'+str(uuid.uuid4()), b'x'*81)[0], 413)
        self.assertEqual(self.request('PUT','/files/'+str(uuid.uuid4()), b'x'*41)[0], 413)
        self.assertEqual(self.store.used(), 60)

    def test_hierarchy_and_traversal(self):
        self.assertEqual(self.request('PUT','/files/../../escape',b'x')[0],400)
        folder = self.entry('folder','Folder')
        folder['parent'] = folder['id']
        self.assertEqual(self.save([folder])[0], 400)
        folder['parent'] = None
        e = self.entry(parent=folder['id'])
        self.assertEqual(self.save([e])[0], 400)
        self.assertEqual(self.save([folder,e])[0], 200)

    def test_trash_counts_and_unsafe_mime(self):
        e = self.entry(name='danger.html',content=b'<script>alert(1)</script>')
        e['mime']='text/html'; e['deleted']=1
        self.assertEqual(self.save([e])[0], 200)
        status, headers, body = self.request('GET','/files/'+e['id'])
        self.assertEqual(status,200)
        self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertGreater(self.store.used(),0)

    def test_private_unix_transport(self):
        address = str(pathlib.Path(self.temp.name)/'test.sock')
        server = UnixServer(address, Handler)
        server.store = self.store; server.origin = 'https://hxry.ru'
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            connection = http.client.HTTPConnection('localhost')
            connection.sock = socket.socket(socket.AF_UNIX)
            connection.sock.connect(address)
            connection.request('GET','/health',headers={'X-Cloud-Token':'test-token'})
            response = connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertTrue(json.loads(response.read())['ok'])
            connection.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_streamed_upload_larger_than_256_mib(self):
        self.store.quota = 400*1024**2
        self.store.max_file = 5*1024**3
        ident = str(uuid.uuid4()); size = 257*1024**2
        chunk = b'x' * 1024**2
        status = self.request('PUT','/files/'+ident,
                              (chunk for _ in range(257)),
                              {'Content-Length':str(size)})[0]
        self.assertEqual(status,201)
        path = self.store.path(ident)
        self.assertEqual(path.stat().st_size,size)
        with path.open('rb') as stream:
            self.assertEqual(stream.read(8),b'xxxxxxxx')
            stream.seek(-8,2)
            self.assertEqual(stream.read(),b'xxxxxxxx')

    def test_host_disk_reserve(self):
        self.store.reserve=10**20
        self.assertEqual(self.request('PUT','/files/'+str(uuid.uuid4()),b'x')[0],507)
        self.assertEqual(self.store.used(),0)

    def session(self, size, name='resumable.txt', parent=None):
        self.store.quota=100*1024**2; self.store.max_file=10*1024**2
        ident=str(uuid.uuid4())
        meta={'name':name,'size':size,'parent':parent,'mime':'text/plain','fingerprint':'a'*64}
        self.assertEqual(self.request('PUT','/uploads/'+ident,meta)[0],200)
        return ident,meta

    def chunk(self, ident, offset, content, digest=None):
        return self.request('PUT',f'/uploads/{ident}/chunk?offset={offset}',content,
                            {'X-Chunk-SHA256':digest or hashlib.sha256(content).hexdigest()})

    def test_resume_restart_publish_and_idempotency(self):
        chunk=b'x'*(4*1024**2); ident,meta=self.session(len(chunk)+5)
        self.assertEqual(self.store.used(),len(chunk)+5)  # Full reservation, not just received bytes.
        self.assertEqual(self.chunk(ident,0,chunk)[0],200)
        self.assertEqual(self.request('GET','/files/'+ident)[0],404)
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],409)
        self.server.store=Store(self.temp.name,'test-token',reserve=0)
        self.assertEqual(json.loads(self.request('GET','/uploads/'+ident)[2])['offset'],len(chunk))
        self.assertEqual(self.chunk(ident,0,chunk)[0],409)  # Lost response: do not append twice.
        self.assertEqual(self.chunk(ident,len(chunk),b'hello')[0],200)
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],200)
        version=self.server.store.state()['version']
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],200)
        self.assertEqual(self.server.store.state()['version'],version)
        self.assertEqual(self.request('GET','/files/'+ident,headers={'Range':f'bytes={len(chunk)}-'})[2],b'hello')

    def test_bad_chunk_cancel_and_expiry(self):
        ident,meta=self.session(5)
        self.assertEqual(self.chunk(ident,0,b'hello','b'*64)[0],400)
        self.assertEqual(self.chunk(ident,0,b'hel')[0],400)
        self.assertEqual(json.loads(self.request('GET','/uploads/'+ident)[2])['offset'],0)
        self.assertEqual(self.request('DELETE','/uploads/'+ident)[0],200)
        self.assertEqual(self.store.used(),0)
        self.assertEqual(self.request('PUT','/uploads/'+ident,meta)[0],410)
        ident,meta=self.session(5)
        self.assertEqual(self.chunk(ident,0,b'hello')[0],200)
        with self.store.connect() as db: db.execute('UPDATE uploads SET updated=0 WHERE id=?',(ident,))
        self.assertEqual(self.request('GET','/uploads/'+ident)[0],404)
        self.assertFalse((self.store.parts/ident).exists())

    def test_cancel_before_create_and_empty_file(self):
        ident=str(uuid.uuid4())
        self.assertEqual(self.request('DELETE','/uploads/'+ident)[0],200)
        meta={'name':'a','size':0,'parent':None,'mime':'text/plain','fingerprint':'a'*64}
        self.assertEqual(self.request('PUT','/uploads/'+ident,meta)[0],410)
        ident,_=self.session(0)
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],200)
        self.assertEqual(self.request('GET','/files/'+ident)[2],b'')

    def test_partial_request_does_not_advance_offset(self):
        ident,_=self.session(5)
        connection=socket.create_connection(self.server.server_address)
        headers=(f'PUT /uploads/{ident}/chunk?offset=0 HTTP/1.0\r\n'
                 'X-Cloud-Token: test-token\r\nX-Cloud-Request: 1\r\nContent-Length: 5\r\n'
                 f'X-Chunk-SHA256: {hashlib.sha256(b"hello").hexdigest()}\r\n\r\n')
        connection.sendall(headers.encode()+b'he'); connection.shutdown(socket.SHUT_WR)
        while connection.recv(4096): pass
        connection.close()
        self.assertEqual(json.loads(self.request('GET','/uploads/'+ident)[2])['offset'],0)

    def test_adaptive_chunk_sizes_and_integrity(self):
        self.store.max_file=64*1024**2;self.store.quota=100*1024**2
        ident=str(uuid.uuid4()); chunks=[b'a'*(8*1024**2),b'b'*(16*1024**2),b'final']
        meta={'name':'adaptive.bin','size':sum(map(len,chunks)),'parent':None,'mime':'application/octet-stream','fingerprint':'a'*64,'protocol':2}
        response=self.request('PUT','/uploads/'+ident,meta)
        self.assertEqual(response[0],200)
        self.assertEqual(json.loads(response[2])['chunkSize'],32*1024**2)
        offset=0
        for chunk in chunks:
            self.assertEqual(self.chunk(ident,offset,chunk)[0],200);offset+=len(chunk)
        self.server.store=Store(self.temp.name,'test-token',reserve=0)
        state=json.loads(self.request('GET','/uploads/'+ident)[2])
        self.assertEqual([chunk['size'] for chunk in state['hashes']],list(map(len,chunks)))
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],200)
        self.assertEqual(self.request('GET','/files/'+ident,headers={'Range':f'bytes={len(chunks[0])-2}-{len(chunks[0])+1}'})[2],b'aabb')

    def test_old_sessions_keep_four_mib_protocol(self):
        chunk=b'x'*(4*1024**2);ident,meta=self.session(len(chunk)+1)
        self.assertEqual(self.chunk(ident,0,chunk)[0],200)
        response=self.request('PUT','/uploads/'+ident,{**meta,'protocol':2})
        self.assertEqual(response[0],200)
        state=json.loads(response[2])
        self.assertEqual((state['protocol'],state['chunkSize'],state['offset']),(1,4*1024**2,len(chunk)))
        self.assertIsInstance(state['hashes'][0],str)
        self.assertEqual(self.chunk(ident,len(chunk),b'z')[0],200)
        self.assertEqual(self.request('PUT','/uploads/'+ident+'/complete')[0],200)


if __name__ == '__main__': unittest.main()
