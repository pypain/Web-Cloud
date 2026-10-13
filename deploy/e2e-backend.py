import pathlib
import sys
import tempfile
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'backend'))
from server import Store, Handler
with tempfile.TemporaryDirectory(prefix='cloud-e2e-') as directory:
    server = ThreadingHTTPServer(('127.0.0.1', 18992), Handler)
    server.store = Store(directory, 'test-only-token', reserve=0)
    server.origin = 'http://127.0.0.1:4178'
    server.serve_forever()
