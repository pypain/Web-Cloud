#!/usr/bin/env python3
"""Exercise the production account snippet with real TLS, PROXY protocol and a Unix backend."""
import argparse,json,pathlib,subprocess,sys,tempfile,threading,time,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'backend'))
from server import Store,Handler,UnixServer,Problem
from accounts import Accounts
from replica import Replica
parser=argparse.ArgumentParser();parser.add_argument('--nginx',required=True);args=parser.parse_args()
repo=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='cloud-nginx-') as directory:
    root=pathlib.Path(directory)
    certificate=root/'certificate.pem';key=root/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(certificate),'-days','1','-subj','/CN=hxry.ru','-addext','subjectAltName=DNS:hxry.ru'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    backend=UnixServer(str(root/'backend.sock'),Handler)
    backend.store=Store(root/'data','test-nginx-token',reserve=0)
    backend.accounts=Accounts(backend.store,Problem)
    admin=backend.accounts.create('cloudadmin','Admin password 123!',role='admin',legacy=True)
    alice=backend.accounts.create('alice','Alice password 123!')
    backend.replica=Replica(backend.store,backend.accounts,Problem)
    replica_key=backend.replica.rotate()['key']
    backend.origin='https://hxry.ru:18443'
    thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
    snippet=(repo/'deploy/hxry-cloud-accounts.conf').read_text().replace('__BACKEND_TOKEN__','test-nginx-token').replace('/run/hxry-cloud/backend.sock',str(root/'backend.sock')).replace('/var/www/hxry-cloud/current',str(repo/'dist'))
    (root/'snippet.conf').write_text(snippet)
    (root/'nginx.conf').write_text(f'''pid {root}/nginx.pid; error_log {root}/error.log;
    events {{ worker_connections 128; }}
    http {{ access_log off; client_body_temp_path {root}/body; proxy_temp_path {root}/proxy; fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi; scgi_temp_path {root}/scgi;
      types {{ text/html html; application/javascript js; text/css css; application/wasm wasm; }}
      server {{ listen 127.0.0.1:18443 ssl proxy_protocol; server_name hxry.ru;
        ssl_certificate {certificate};ssl_certificate_key {key};
        location = / {{ return 200 "Original site"; }}
        location = /account {{ return 200 "Original account"; }}
        include {root}/snippet.conf;
      }}
    }}''')
    def call(method,path,data=None,cookie='',csrf='',public_headers=None):
        headers={'Origin':backend.origin}
        if cookie:headers['Cookie']=cookie
        if csrf:headers['X-Cloud-CSRF']=csrf
        if method!='GET':headers['X-Cloud-Request']='1'
        if isinstance(data,dict):headers['Content-Type']='application/json'
        headers.update(public_headers or {})
        config=root/'curl.conf';config.write_text('\n'.join('header = '+json.dumps(key+': '+value) for key,value in headers.items()))
        command=['curl','--noproxy','*','--haproxy-protocol','--cacert',str(certificate),'--resolve','hxry.ru:18443:127.0.0.1','--silent','--show-error','--max-time','10','--config',str(config),'-X',method,'-D',str(root/'headers'),'-w','\n%{http_code}',backend.origin+path]
        if data is not None:
            content=json.dumps(data).encode() if isinstance(data,dict) else data
            (root/'body-input').write_bytes(content);command+=['--data-binary','@'+str(root/'body-input')]
        response=subprocess.run(command,capture_output=True,check=True).stdout
        body,status=response.rsplit(b'\n',1)
        return int(status),body,(root/'headers').read_text()
    nginx=None
    try:
        subprocess.run([args.nginx,'-p',str(root)+'/', '-c',str(root/'nginx.conf'),'-t'],check=True)
        nginx=subprocess.Popen([args.nginx,'-p',str(root)+'/', '-c',str(root/'nginx.conf'),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for attempt in range(50):
            try:
                if call('GET','/cloud/auth/')[0]==200:break
            except subprocess.CalledProcessError:time.sleep(.05)
        else:
            print((root/'error.log').read_text())
            if nginx.poll() is not None:print(nginx.stderr.read().decode())
            raise AssertionError('nginx not ready')
        assert call('GET','/')[1]==b'Original site'
        assert call('GET','/account')[1]==b'Original account'
        assert call('GET','/cloud/api/state')[0]==401
        assert call('GET','/cloud/api/admin/users')[0]==401
        assert call('GET','/cloud/api/replica/snapshot')[0]==401
        assert call('GET','/cloud/api/replica/snapshot',public_headers={'X-Cloud-Replica':replica_key})[0] in (200,202)
        assert call('GET','/cloud/python-runtime/pyodide.js')[0]==200
        code,body,headers=call('PUT','/cloud/api/auth/login',{'username':'alice','password':'Alice password 123!'})
        assert code==200,(code,body)
        cookie=next(line.split(': ',1)[1].split(';')[0] for line in headers.splitlines() if line.lower().startswith('set-cookie:'))
        for flag in ['HttpOnly','Secure','SameSite=Strict']:assert flag in headers
        code,body,_=call('GET','/cloud/api/auth/me',cookie=cookie);me=json.loads(body);assert me['user']['username']=='alice'
        csrf=me['csrf']
        assert call('PUT','/cloud/api/state',{},cookie=cookie)[0]==403
        assert call('PUT','/cloud/api/state',{},cookie=cookie,csrf=csrf,public_headers={'Origin':'https://evil.example'})[0]==403
        assert call('GET','/cloud/api/admin/users',cookie=cookie)[0]==403
        assert call('GET','/cloud/api/admin/replica',cookie=cookie)[0]==403
        assert call('GET','/cloud/api/state',public_headers={'X-Cloud-Public':'1'})[0]==401
        ident=str(uuid.uuid4());assert call('PUT','/cloud/api/files/'+ident,b'Private tenant file',cookie,csrf)[0]==201
        state=json.loads(call('GET','/cloud/api/state',cookie=cookie)[1])
        entry={'id':ident,'name':'tenant.txt','kind':'file','parent':None,'modified':1,'mime':'text/plain'}
        result=call('PUT','/cloud/api/state',{'version':state['version'],'entries':[entry]},cookie,csrf);assert result[0]==200,result[:2]
        code,body,_=call('PUT','/cloud/api/shares/'+str(uuid.uuid4()),{'fileId':ident,'ttl':3600},cookie,csrf);assert code==200
        link=json.loads(body)['path']
        assert call('GET',link)[0]==200
        assert call('GET',link+'/download')[1]==b'Private tenant file'
        assert call('PUT','/cloud/api/auth/logout',{},cookie,csrf)[0]==200
        assert call('GET','/cloud/api/state',cookie=cookie)[0]==401
        print('PASS: real nginx TLS/PROXY/Unix routes, login cookies, CSRF, tenant files, public share, logout and existing site.')
    finally:
        if nginx:
            nginx.terminate()
            try:nginx.wait(timeout=5)
            except subprocess.TimeoutExpired:nginx.kill();nginx.wait()
        backend.shutdown();backend.server_close();thread.join()
