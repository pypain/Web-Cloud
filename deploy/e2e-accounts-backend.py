import json,pathlib,sys,tempfile,uuid
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'backend'))
from server import Store,Handler,Problem
from accounts import Accounts
from replica import Replica
with tempfile.TemporaryDirectory(prefix='cloud-accounts-e2e-') as root:
    server=ThreadingHTTPServer(('127.0.0.1',18993),Handler)
    server.store=Store(root,'test-only-token',reserve=0)
    ident=str(uuid.uuid4());content=b'Original owner file'
    server.store.path(ident).write_bytes(content)
    entry={'id':ident,'name':'original-owner.txt','kind':'file','parent':None,'modified':1,'size':len(content),'mime':'text/plain','_stored':True}
    with server.store.connect() as db:db.execute('UPDATE state SET version=1,entries=? WHERE id=1',(json.dumps([entry]),))
    server.accounts=Accounts(server.store,Problem)
    server.accounts.create('cloudadmin','Admin password 123!',role='admin',legacy=True)
    server.accounts.create('alice','Alice password 123!')
    server.accounts.create('bob','Bob password 123!')
    server.replica=Replica(server.store,server.accounts,Problem)
    server.origin='http://127.0.0.1:4179'
    server.serve_forever()
