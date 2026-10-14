import hashlib
import json
import time
import unittest
import uuid
import pathlib
import runpy
import sys
from unittest.mock import patch
from accounts import Accounts, COOKIE
from server import Problem
import test_server as storage_tests

class AccountTests(unittest.TestCase):
    tearDown=storage_tests.StorageTests.tearDown
    entry=storage_tests.StorageTests.entry
    save=storage_tests.StorageTests.save
    def setUp(self):
        self.cookie='';self.csrf=''
        storage_tests.StorageTests.setUp(self)
        self.old_file=self.entry(name='owner.txt')
        self.assertEqual(self.save([self.old_file])[0],200)
        sid=str(uuid.uuid4())
        self.old_share=json.loads(self.request('PUT','/shares/'+sid,{'fileId':self.old_file['id'],'ttl':3600})[2])
        self.store.quota=20*1024**3
        self.accounts=Accounts(self.server.store,Problem);self.server.accounts=self.accounts
        self.admin=self.accounts.create('cloudadmin','Admin password 123!',role='admin',legacy=True)
        self.alice=self.accounts.create('alice','Alice password 123!')
        self.bob=self.accounts.create('bob','Bob password 123!')
    def request(self,method,path,body=None,headers=None,auth=True):
        h={'Origin':self.server.origin,'Cookie':self.cookie,'X-Cloud-CSRF':self.csrf,'X-Cloud-Client':'192.0.2.1'}
        h.update(headers or {})
        return storage_tests.StorageTests.request(self,method,path,body,h,auth)
    def login(self,username='cloudadmin',password='Admin password 123!'):
        code,headers,body=self.request('PUT','/auth/login',{'username':username,'password':password})
        self.assertEqual(code,200,body);self.cookie=headers['Set-Cookie'].split(';')[0]
        me=self.request('GET','/auth/me');self.assertEqual(me[0],200);self.csrf=json.loads(me[2])['csrf']
        return headers
    def test_session_cookie_csrf_logout_and_password_change(self):
        self.assertEqual(self.request('GET','/state')[0],401)
        headers=self.login()
        for flag in ('HttpOnly','Secure','SameSite=Strict','Path=/cloud/'):
            self.assertIn(flag,headers['Set-Cookie'])
        token=self.cookie.split('=',1)[1]
        with self.accounts.connect() as db:
            self.assertIsNotNone(db.execute('SELECT token FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone())
            self.assertIsNone(db.execute('SELECT token FROM sessions WHERE token=?',(token,)).fetchone())
        for attack in ({'X-Cloud-CSRF':''},{'Origin':'https://evil.example'},{'Origin':''},{'X-Cloud-Request':''}):
            self.assertEqual(self.request('PUT','/state',{},attack)[0],403)
        self.assertEqual(self.request('PUT','/auth/password',{'currentPassword':'wrong','password':'New password 123!'})[0],403)
        self.assertEqual(self.request('PUT','/auth/password',{'currentPassword':'Admin password 123!','password':'New password 123!'})[0],200)
        self.assertEqual(self.request('GET','/state')[0],401)
        self.login(password='New password 123!')
        self.assertEqual(self.request('PUT','/auth/logout',{})[0],200)
        self.assertEqual(self.request('GET','/auth/me')[0],401)
    def test_owner_migration_and_cross_user_storage_profile_upload_isolation(self):
        self.login();state=json.loads(self.request('GET','/state')[2]);self.assertEqual(state['entries'][0]['id'],self.old_file['id'])
        self.login('alice','Alice password 123!')
        self.assertEqual(json.loads(self.request('GET','/state')[2])['entries'],[])
        self.assertEqual(self.request('GET','/files/'+self.old_file['id'])[0],404)
        file=self.entry(name='alice.txt');self.assertEqual(self.save([file])[0],200)
        upload=str(uuid.uuid4())
        meta={'name':'part.bin','size':3,'parent':None,'mime':'application/octet-stream','fingerprint':'a'*64}
        self.assertEqual(self.request('PUT','/uploads/'+upload,meta)[0],200)
        alice_profile=json.loads(self.request('GET','/profile')[2]);alice_profile['profile']['name']='Alice archive'
        self.assertEqual(self.request('PUT','/profile',alice_profile)[0],200)
        share=json.loads(self.request('PUT','/shares/'+str(uuid.uuid4()),{'fileId':file['id'],'ttl':3600})[2])
        self.login('bob','Bob password 123!')
        self.assertEqual(self.request('GET','/files/'+file['id'])[0],404)
        self.assertEqual(self.request('GET','/uploads/'+upload)[0],404)
        self.assertNotEqual(json.loads(self.request('GET','/profile')[2])['profile']['name'],'Alice archive')
        self.assertEqual(json.loads(self.request('GET','/shares')[2])['shares'],[])
        self.assertEqual(self.request('GET',share['path'].replace('/cloud',''),headers={'X-Cloud-Public':'1'})[0],200)
        self.assertEqual(self.request('GET',self.old_share['path'].replace('/cloud',''),headers={'X-Cloud-Public':'1'})[0],200)
        self.assertEqual(self.request('GET','/state',headers={'X-Cloud-Public':'1'})[0],403)
    def test_admin_roles_quota_disable_reset_and_audit(self):
        self.login('alice','Alice password 123!')
        for path in ('/admin/users','/admin/audit'):
            self.assertEqual(self.request('GET',path)[0],403)
        self.assertEqual(self.request('PUT','/admin/users',{'username':'evil','password':'Password 1234','role':'admin'})[0],403)
        file=self.entry(name='shared-before-disable.txt');self.assertEqual(self.save([file])[0],200)
        share=json.loads(self.request('PUT','/shares/'+str(uuid.uuid4()),{'fileId':file['id'],'ttl':3600})[2])
        alice_cookie=self.cookie;alice_csrf=self.csrf
        self.login()
        self.assertEqual(self.request('PUT','/admin/users',{'username':'newuser','password':'Long password 123!','quota':1024**3})[0],201)
        self.assertEqual(self.request('PUT','/admin/users',{'username':'newuser','password':'Long password 123!'})[0],409)
        self.assertEqual(self.request('PUT','/admin/users/'+self.admin['id'],{'enabled':False})[0],400)
        self.assertEqual(self.request('PUT','/admin/users/'+self.admin['id'],{'role':'user'})[0],400)
        self.assertEqual(self.request('PUT','/admin/users/'+self.alice['id'],{'enabled':False})[0],200)
        self.assertEqual(self.request('GET','/state',headers={'Cookie':alice_cookie,'X-Cloud-CSRF':alice_csrf})[0],401)
        self.assertEqual(self.request('GET',share['path'].replace('/cloud',''),headers={'X-Cloud-Public':'1'})[0],404)
        self.assertEqual(self.request('PUT','/admin/users/'+self.alice['id'],{'enabled':True,'password':'Replacement password 123!','quota':1024**3})[0],200)
        self.login('alice','Replacement password 123!')
        self.assertEqual(self.request('GET','/files/'+file['id'])[2],b'hello world')
        self.assertEqual(self.request('GET',share['path'].replace('/cloud',''),headers={'X-Cloud-Public':'1'})[0],404)
        self.assertEqual(json.loads(self.request('GET','/state')[2])['quota'],1024**3)
        self.login();audit=json.loads(self.request('GET','/admin/audit')[2])['events'];self.assertTrue(any(e['event']=='password.reset' for e in audit))
        self.assertNotIn('Replacement password',json.dumps(audit))
    def test_console_recovery_retains_files_and_revokes_sessions(self):
        self.login();old_cookie=self.cookie
        token_file=pathlib.Path(self.temp.name)/'token';token_file.write_text('test-token')
        cli=str(pathlib.Path(__file__).with_name('manage-accounts.py'))
        arguments=[cli,'reset-admin',self.temp.name,str(token_file)]
        with patch.object(sys,'argv',arguments),patch('getpass.getpass',return_value='Recovered password 123!'):
            runpy.run_path(cli,run_name='__main__')
        self.assertEqual(self.request('GET','/state',headers={'Cookie':old_cookie})[0],401)
        self.login(password='Recovered password 123!')
        self.assertEqual(self.request('GET','/files/'+self.old_file['id'])[2],b'hello world')
        output=pathlib.Path(self.temp.name)/'probe.conf'
        with patch.object(sys,'argv',[cli,'probe',self.temp.name,str(token_file),'--output',str(output)]):
            runpy.run_path(cli,run_name='__main__')
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        self.assertIn(COOKIE,output.read_text())

    def test_rate_limit_expiry_and_role_revocation(self):
        for i in range(10):self.assertEqual(self.request('PUT','/auth/login',{'username':'alice','password':'wrong'})[0],401)
        self.assertEqual(self.request('PUT','/auth/login',{'username':'alice','password':'wrong'})[0],429)
        self.login()
        with self.accounts.connect() as db:db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.request('GET','/state')[0],401)
        self.login()
        self.assertEqual(self.request('PUT','/admin/users/'+self.bob['id'],{'role':'admin'})[0],200)
        self.login('bob','Bob password 123!');bob_cookie=self.cookie
        self.login();self.assertEqual(self.request('PUT','/admin/users/'+self.bob['id'],{'role':'user'})[0],200)
        self.assertEqual(self.request('GET','/admin/users',headers={'Cookie':bob_cookie})[0],401)

if __name__=='__main__':unittest.main()
