import http.client
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


if __name__ == '__main__': unittest.main()
