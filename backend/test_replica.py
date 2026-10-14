import hashlib,json,pathlib,sqlite3,tempfile,time,unittest,urllib.error,uuid,sys
import test_accounts as fixtures
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'laptop'))
from agent import Client,Mirror

class ProxyClient(Client):
    def open(self,path,data=None,headers=None):
        return super().open(path,data,{'X-Cloud-Token':'test-token',**(headers or {})})

class ReplicaTests(unittest.TestCase):
    entry=fixtures.AccountTests.entry
    save=fixtures.AccountTests.save
    login=fixtures.AccountTests.login
    request=fixtures.AccountTests.request
    def setUp(self):
        fixtures.AccountTests.setUp(self)
        self.server.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.store.max_file=self.accounts.max_file=5*1024**3
        self.replica=self.server.replica;self.key=self.replica.rotate()['key']
        self.client=ProxyClient(self.server.origin+'/replica',self.key,allow_http=True)
        self.local=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.local.name)
        (self.root/'.replica-root').write_text('test-marker')
        self.mirror=Mirror(self.root,'test-marker',self.client,budget=100*1024**2,reserve=0)
    def tearDown(self):
        self.mirror.index.close();self.local.cleanup();fixtures.AccountTests.tearDown(self)
    def ready(self):
        for attempt in range(200):
            row=self.replica.snapshot()
            if row:return row
            time.sleep(.025)
        self.fail('Snapshot preparation did not complete: '+self.replica.build_error)
    def target(self):return self.root/'objects'/self.admin['id']/self.old_file['id']
    def test_credential_is_read_only_and_admin_status_is_private(self):
        self.assertEqual(self.request('GET','/replica/snapshot')[0],401)
        headers={'X-Cloud-Replica':self.key}
        self.assertEqual(self.request('GET','/state',headers=headers)[0],401)
        self.assertEqual(self.request('PUT','/replica/files/x',{},headers)[0],405)
        self.login('alice','Alice password 123!')
        self.assertEqual(self.request('GET','/admin/replica')[0],403)
        self.assertEqual(self.request('PUT','/admin/replica/key',{})[0],403)
        self.login();self.assertEqual(self.request('GET','/admin/replica')[0],200)
        self.assertEqual(self.request('PUT','/replica/report',{'acks':[{'owner':'../','id':'bad'}]},headers)[0],400)
        self.replica.rotate()
        self.assertEqual(self.request('GET','/replica/snapshot',headers=headers)[0],401)
    def test_confirmed_files_metadata_trash_and_permanent_deletion(self):
        self.ready();self.assertTrue(self.mirror.sync());self.assertEqual(self.target().read_bytes(),b'hello world')
        status=self.replica.status();self.assertEqual(status['pendingPercent'],0);self.assertFalse(status['metadataPending'])
        with sqlite3.connect(self.root/'current'/'accounts.sqlite') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)
        self.login();self.assertEqual(self.save([{**self.old_file,'deleted':1}],version=1)[0],200)
        self.ready();self.mirror.sync();self.assertTrue(self.target().exists())
        self.assertEqual(self.save([],version=2)[0],200)
        self.assertEqual(self.replica.status()['pendingDeletes'],1)
        self.ready();self.mirror.sync();self.assertFalse(self.target().exists());self.assertEqual(self.replica.status()['pendingDeletes'],0)
    def test_resume_interrupted_transfer_and_repair_corrupt_local_file(self):
        self.login();data=b'A'*(5*1024**2+123);large=self.entry(name='large.bin',content=data)
        self.assertEqual(self.save([self.old_file,large],version=1)[0],200);self.ready()
        parent=self.client
        class InterruptedResponse:
            def __init__(self,response):self.response=response;self.status=response.status;self.headers=response.headers;self.reads=0
            def __enter__(self):return self
            def __exit__(self,*args):self.response.close()
            def read(self,n):
                self.reads+=1
                if self.reads>1:raise OSError('simulated disconnect')
                return self.response.read(n)
        class InterruptedClient:
            json=parent.json
            def open(self,path,**kwargs):
                response=parent.open(path,**kwargs)
                return InterruptedResponse(response) if path.endswith(large['id']) else response
        self.mirror.client=InterruptedClient()
        with self.assertRaisesRegex(OSError,'simulated disconnect'):self.mirror.sync()
        partial=next((self.root/'parts').glob('*'+large['id']+'*'));offset=partial.stat().st_size;self.assertEqual(offset,1024**2)
        offsets=[]
        class ResumedClient:
            json=parent.json
            def open(self,path,**kwargs):
                if path.endswith(large['id']):offsets.append(kwargs.get('headers',{}).get('Range'))
                return parent.open(path,**kwargs)
        self.mirror.client=ResumedClient();self.mirror.sync()
        self.assertEqual(offsets,[f'bytes={offset}-']);self.assertEqual((self.root/'objects'/self.admin['id']/large['id']).read_bytes(),data)
        self.target().write_bytes(b'broken copy');self.mirror.client=parent;self.mirror.sync();self.assertEqual(self.target().read_bytes(),b'hello world')
    def test_incomplete_inventory_or_unavailable_source_never_prunes(self):
        self.ready();self.mirror.sync();self.login();self.assertEqual(self.save([],version=1)[0],200);self.ready()
        parent=self.client
        class IncompleteClient:
            open=parent.open
            def json(self,path,data=None):
                code,value=parent.json(path,data)
                if path=='snapshot':value={**value,'files':value['files']+1}
                return code,value
        self.mirror.client=IncompleteClient()
        with self.assertRaisesRegex(RuntimeError,'Incomplete inventory'):self.mirror.sync()
        self.assertTrue(self.target().exists())
        class OfflineClient:
            def json(self,*args,**kwargs):raise urllib.error.URLError('offline')
        self.mirror.client=OfflineClient()
        with self.assertRaises(urllib.error.URLError):self.mirror.sync()
        self.assertTrue(self.target().exists())
    def test_budget_reserve_and_wrong_root_are_enforced(self):
        self.ready();self.mirror.sync()
        self.mirror.budget=1
        with self.assertRaisesRegex(RuntimeError,'Лимит'):self.mirror.sync()
        self.assertTrue(self.target().exists())
        with self.assertRaisesRegex(RuntimeError,'marker'):Mirror(self.root,'wrong-marker',self.client)
        self.mirror.budget=100*1024**2;self.mirror.reserve=10**16
        with self.assertRaisesRegex(RuntimeError,'свободного'):self.mirror.sync()
        self.assertTrue(self.target().exists())
    def test_busy_source_retries_without_blocking_accounts_and_false_completion_is_denied(self):
        info=self.ready()
        response=self.request('PUT','/replica/report',{'complete':True,'generation':info['id']},{'X-Cloud-Replica':self.key})
        self.assertEqual(response[0],409)
        with self.store.lock:
            self.replica.snapshot(force=True)
            for attempt in range(200):
                if not self.replica.building:break
                time.sleep(.005)
            self.assertFalse(self.replica.building,'Snapshot worker waited on an active upload lock')
            self.assertTrue(self.replica.build_error)
            with self.accounts.lock:pass
        self.assertTrue((self.replica.root/(info['id']+'.tar.gz')).exists())

    def test_metadata_and_all_tenants_restore_with_public_links_and_no_old_sessions(self):
        import shutil
        from server import Store,Problem
        from accounts import Accounts
        self.login('alice','Alice password 123!');file=self.entry(name='alice.txt',content=b'alice data')
        self.assertEqual(self.save([file],version=0)[0],200)
        self.ready();self.mirror.sync()
        with sqlite3.connect(self.root/'current'/'accounts.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
            users=db.execute('SELECT id,legacy FROM users').fetchall()
        with tempfile.TemporaryDirectory() as directory:
            restored=pathlib.Path(directory);shutil.copyfile(self.root/'current'/'accounts.sqlite',restored/'accounts.sqlite')
            for owner,legacy in users:
                target=restored if legacy else restored/'users'/owner;target.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.root/'current'/'stores'/(owner+'.sqlite'),target/'state.sqlite')
                (target/'files').mkdir()
                if (self.root/'objects'/owner).exists():
                    for source in (self.root/'objects'/owner).iterdir():shutil.copyfile(source,target/'files'/source.name)
            store=Store(restored,'restored-test-token',reserve=0);accounts=Accounts(store,Problem)
            accounts.login('cloudadmin','Admin password 123!','local-recovery')
            with accounts.connect() as db:alice=db.execute("SELECT * FROM users WHERE username='alice'").fetchone()
            self.assertEqual(accounts.storage(alice).path(file['id']).read_bytes(),b'alice data')
            token=self.old_share['path'].rsplit('/',1)[1]
            self.assertEqual(accounts.public_storage(token).path(self.old_file['id']).read_bytes(),b'hello world')

    def test_archive_traversal_rejected_and_foreign_files_preserved(self):
        import io,tarfile
        malicious=self.root/'work'/'bad.tar.gz'
        with tarfile.open(malicious,'w:gz') as archive:
            entry=tarfile.TarInfo('../escape');entry.size=3;archive.addfile(entry,io.BytesIO(b'bad'))
        with self.assertRaisesRegex(RuntimeError,'Invalid metadata'):self.mirror.extract(malicious,self.root/'work'/'test-extract')
        self.assertFalse((self.root.parent/'escape').exists())
        self.ready();self.mirror.sync();foreign=self.root/'objects'/'keep-me.txt';foreign.write_text('manual file')
        self.login();self.assertEqual(self.save([],version=1)[0],200);self.ready();self.mirror.sync()
        self.assertEqual(foreign.read_text(),'manual file')

if __name__=='__main__':unittest.main()
