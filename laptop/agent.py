#!/usr/bin/env python3
"""Resumable, verified pull mirror. Never prune without a complete validated source inventory."""
import argparse,fcntl,hashlib,json,os,pathlib,re,shutil,sqlite3,tarfile,tempfile,time,urllib.error,urllib.parse,urllib.request

UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
HASH=re.compile(r'^[0-9a-f]{64}$')
MAX_META=2*1024**3

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('Redirect refused; update the configured cloud URL')

class Client:
    def __init__(self,url,key,allow_http=False):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme not in (('https','http') if allow_http else ('https',)) or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('A direct HTTPS cloud URL is required')
        self.url=url.rstrip('/');self.origin=parsed.scheme+'://'+parsed.netloc;self.key=key
        self.opener=urllib.request.build_opener(NoRedirect)
    def open(self,path,data=None,headers=None):
        merged={'X-Cloud-Replica':self.key,'X-Cloud-Request':'1','Origin':self.origin,**(headers or {})}
        body=None
        if data is not None:body=json.dumps(data).encode();merged['Content-Type']='application/json'
        request=urllib.request.Request(self.url+'/'+path,data=body,headers=merged,method='PUT' if data is not None else 'GET')
        return self.opener.open(request,timeout=30)
    def json(self,path,data=None):
        with self.open(path,data) as response:
            body=response.read(256*1024+1)
            if len(body)>256*1024:raise RuntimeError('Unexpected response size')
            return response.status,json.loads(body)

class Mirror:
    def __init__(self,root,marker,client,budget=200_000_000_000,reserve=20_000_000_000):
        self.root=pathlib.Path(root);self.client=client;self.budget=budget;self.reserve=reserve
        if self.root.is_symlink() or not self.root.is_dir() or (self.root/'.replica-root').read_text().strip()!=marker:raise RuntimeError('Mirror root marker does not match; refusing to touch files')
        for name in ('objects','parts','work','metadata'):
            path=self.root/name
            if path.is_symlink():raise RuntimeError('Mirror directory is a symlink')
            path.mkdir(mode=0o700,exist_ok=True)
        self.index=sqlite3.connect(self.root/'index.sqlite')
        self.index.row_factory=sqlite3.Row
        self.index.executescript('''CREATE TABLE IF NOT EXISTS files(owner TEXT,id TEXT,digest TEXT,sig TEXT,verified REAL,PRIMARY KEY(owner,id));
        CREATE TABLE IF NOT EXISTS parts(owner TEXT,id TEXT,digest TEXT,PRIMARY KEY(owner,id,digest));''')
        self.used=self.usage();self.last_report=0;self.acks=[];self.missing=[];self.active_bytes=0;self.active_size=0
    def usage(self):
        total=0
        for path in self.root.rglob('*'):
            if path.is_symlink():
                if path==self.root/'current' and path.resolve().parent==self.root/'metadata':continue
                raise RuntimeError('Unexpected symlink inside mirror')
            if path.is_file():total+=path.stat().st_size
        return total
    def space(self,additional):
        if self.used+additional+4*1024**2>self.budget:raise RuntimeError('Лимит копии 200 ГБ исчерпан.')
        if shutil.disk_usage(self.root).free-additional<self.reserve:raise RuntimeError('Недостаточно свободного места: системе оставлены 20 ГБ.')
    def report(self,state='syncing',force=False,**extra):
        if not force and len(self.acks)<100 and len(self.missing)<100 and time.time()-self.last_report<15:return
        data={'acks':self.acks[:500],'missing':self.missing[:500],'state':state,'usedBytes':self.used,
              'freeBytes':shutil.disk_usage(self.root).free,'activeBytes':self.active_bytes,'activeSize':self.active_size,**extra}
        try:self.client.json('report',data)
        except urllib.error.HTTPError as error:
            if error.code in (400,401,403):raise RuntimeError('Ключ ноутбука отклонён или отчёт некорректен; проверьте подключение в админке.')
            self.last_report=time.time();return False
        except (OSError,TimeoutError,ValueError):
            self.last_report=time.time();return False
        self.acks=self.acks[500:];self.missing=self.missing[500:];self.last_report=time.time();return True
    @staticmethod
    def signature(path):
        stat=path.stat();return f'{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_ino}'
    def remove(self,path):
        if path.is_symlink():raise RuntimeError('Refusing to remove a symlink')
        if path.exists():self.used-=path.stat().st_size;path.unlink();fsync_dir(path.parent)
    def download(self,path,target,size,expected,progress=True):
        if target.is_symlink():raise RuntimeError('Download path is a symlink')
        offset=target.stat().st_size if target.exists() else 0
        if offset>size:self.remove(target);offset=0
        self.space(size-offset)
        self.active_size=size;self.active_bytes=offset
        if offset<size:
            with self.client.open(path,headers={'Range':f'bytes={offset}-'} if offset else {}) as response:
                if offset and (response.status!=206 or response.headers.get('Content-Range')!=f'bytes {offset}-{size-1}/{size}'):raise RuntimeError('Invalid resume response; local copy retained')
                if int(response.headers.get('Content-Length','-1'))!=size-offset:raise RuntimeError('Unexpected source length; retry a new snapshot')
                with target.open('ab' if offset else 'wb') as output:
                    checkpoint=offset
                    while True:
                        block=response.read(1024*1024)
                        if not block:break
                        if offset+len(block)>size:raise RuntimeError('Source returned too many bytes')
                        self.space(len(block))
                        output.write(block);offset+=len(block);self.used+=len(block);self.active_bytes=offset
                        if offset-checkpoint>=4*1024**2:output.flush();os.fsync(output.fileno());checkpoint=offset
                        if progress:self.report()
                    output.flush();os.fsync(output.fileno())
            if offset!=size:raise RuntimeError('Connection interrupted; partial file retained')
        elif not target.exists():target.touch(mode=0o600)
        if digest(target)!=expected:
            self.remove(target);raise RuntimeError('Checksum mismatch; invalid download removed, retry will start again')
        fsync_dir(target.parent)
    def extract(self,archive,target):
        if target.exists():shutil.rmtree(target)
        target.mkdir(mode=0o700)
        names=set();expanded=0
        with tarfile.open(archive,'r:gz') as source:
            for member in source:
                name=member.name
                allowed=name in ('inventory.sqlite','accounts.sqlite') or re.fullmatch(r'stores/[0-9a-f-]{36}\.sqlite',name)
                if not allowed or not member.isfile() or name in names or member.size<0:raise RuntimeError('Invalid metadata archive; copies retained')
                if name.startswith('stores/') and not UUID.fullmatch(name[7:-7]):raise RuntimeError('Invalid metadata owner')
                names.add(name);expanded+=member.size
                if expanded>MAX_META:raise RuntimeError('Metadata archive exceeds safety limit')
                self.space(member.size)
                path=target/name;path.parent.mkdir(mode=0o700,exist_ok=True)
                with source.extractfile(member) as data,path.open('wb') as output:
                    for block in iter(lambda:data.read(1024*1024),b''):output.write(block);self.used+=len(block)
                    output.flush();os.fsync(output.fileno())
        if not {'inventory.sqlite','accounts.sqlite'}<=names:raise RuntimeError('Incomplete archive; no copies removed')
    def validate(self,stage,info):
        db=sqlite3.connect(stage/'inventory.sqlite');db.row_factory=sqlite3.Row
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Corrupt inventory; no copies removed')
        total=count=0
        for row in db.execute('SELECT owner,id,size,digest FROM objects'):
            if not UUID.fullmatch(row['owner']) or not UUID.fullmatch(row['id']) or type(row['size']) is not int or not 0<=row['size']<=100*1024**3 or not HASH.fullmatch(row['digest']):raise RuntimeError('Invalid inventory row; no copies removed')
            count+=1;total+=row['size']
        if count!=info['files'] or total!=info['bytes']:raise RuntimeError('Incomplete inventory; no copies removed')
        with sqlite3.connect(stage/'accounts.sqlite') as accounts:users=accounts.execute('SELECT id FROM users').fetchall()
        owners=set()
        if len(users)>100:raise RuntimeError('Too many metadata owners')
        expected_count=0
        for owner, in users:
            if not UUID.fullmatch(owner) or owner in owners:raise RuntimeError('Invalid owner mapping')
            owners.add(owner)
            path=stage/'stores'/(owner+'.sqlite')
            if not path.is_file():raise RuntimeError('Missing owner metadata; no copies removed')
            with sqlite3.connect(path) as state:
                if state.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Corrupt owner metadata')
                entries=json.loads(state.execute('SELECT entries FROM state WHERE id=1').fetchone()[0])
            for entry in entries:
                if entry['kind']!='file':continue
                expected_count+=1;row=db.execute('SELECT size FROM objects WHERE owner=? AND id=?',(owner,entry['id'])).fetchone()
                if not row or row['size']!=entry['size']:raise RuntimeError('Inventory does not match source metadata; no copies removed')
        if expected_count!=count:raise RuntimeError('Unexpected inventory objects; no copies removed')
        return db
    def prune(self,inventory):
        # Only entries created by this agent are eligible; untracked files are never removed.
        for row in self.index.execute('SELECT * FROM files').fetchall():
            current=inventory.execute('SELECT digest FROM objects WHERE owner=? AND id=?',(row['owner'],row['id'])).fetchone()
            if not current or current['digest']!=row['digest']:
                self.remove(self.root/'objects'/row['owner']/row['id'])
                self.index.execute('DELETE FROM files WHERE owner=? AND id=?',(row['owner'],row['id']))
                self.missing.append({'owner':row['owner'],'id':row['id']});self.report()
        for row in self.index.execute('SELECT * FROM parts').fetchall():
            current=inventory.execute('SELECT digest FROM objects WHERE owner=? AND id=?',(row['owner'],row['id'])).fetchone()
            if not current or current['digest']!=row['digest']:
                self.remove(self.root/'parts'/f"{row['owner']}-{row['id']}-{row['digest']}.part")
                self.index.execute('DELETE FROM parts WHERE owner=? AND id=? AND digest=?',(row['owner'],row['id'],row['digest']))
        self.index.commit();self.report(force=True)
    def sync(self):
        _,info=self.client.json('snapshot')
        if info.get('preparing'):
            self.report(force=True);return False
        if not UUID.fullmatch(info.get('id','')) or not HASH.fullmatch(info.get('digest','')) or type(info.get('size')) is not int or not 0<=info['size']<=MAX_META:raise RuntimeError('Invalid source snapshot; copies retained')
        generation=info['id'];archive=self.root/'work'/f'{generation}.tar.gz.part'
        self.download('snapshot/'+generation,archive,info['size'],info['digest'])
        stage=self.root/'work'/'extracted'
        if stage.exists():shutil.rmtree(stage);self.used=self.usage()
        self.extract(archive,stage)
        inventory=self.validate(stage,info)
        try:
            self.prune(inventory)
            if info['bytes']>self.budget:raise RuntimeError('На VPS больше файлов, чем помещается в лимит копии 200 ГБ.')
            for row in inventory.execute('SELECT * FROM objects ORDER BY owner,id'):
                owner,ident,expected=row['owner'],row['id'],row['digest'];directory=self.root/'objects'/owner
                if directory.is_symlink():raise RuntimeError('Object directory is a symlink')
                directory.mkdir(mode=0o700,exist_ok=True);target=directory/ident
                old=self.index.execute('SELECT * FROM files WHERE owner=? AND id=?',(owner,ident)).fetchone()
                valid=False;cached_valid=False
                if target.is_file() and not target.is_symlink() and target.stat().st_size==row['size']:
                    sig=self.signature(target)
                    valid=bool(old and old['digest']==expected and old['sig']==sig and time.time()-old['verified']<7*86400)
                    cached_valid=valid
                    if not valid:valid=digest(target)==expected
                if not valid:
                    self.missing.append({'owner':owner,'id':ident});self.report(force=True)
                    if target.exists():self.remove(target)
                    partial=self.root/'parts'/f'{owner}-{ident}-{expected}.part'
                    self.index.execute('INSERT OR IGNORE INTO parts VALUES (?,?,?)',(owner,ident,expected));self.index.commit()
                    self.download(f'files/{owner}/{ident}',partial,row['size'],expected)
                    os.replace(partial,target);fsync_dir(directory)
                    self.index.execute('DELETE FROM parts WHERE owner=? AND id=? AND digest=?',(owner,ident,expected))
                self.index.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)',(owner,ident,expected,self.signature(target),old['verified'] if cached_valid else time.time()))
                self.index.commit();self.acks.append({'owner':owner,'id':ident,'digest':expected});self.report()
            self.active_size=self.active_bytes=0
        finally:inventory.close()
        destination=self.root/'metadata'/generation
        if destination.exists():
            for path in stage.rglob('*.sqlite'):
                target=destination/path.relative_to(stage);target.parent.mkdir(mode=0o700,exist_ok=True)
                if not target.exists() or digest(path)!=digest(target):os.replace(path,target);fsync_dir(target.parent)
            shutil.rmtree(stage)
        else:os.replace(stage,destination)
        fsync_dir(destination.parent)
        temporary=self.root/'current.new'
        if temporary.exists() and not temporary.is_symlink():raise RuntimeError('Unexpected metadata pointer; refusing overwrite')
        temporary.unlink(missing_ok=True);temporary.symlink_to('metadata/'+generation)
        os.replace(temporary,self.root/'current');fsync_dir(self.root)
        for path in (self.root/'metadata').iterdir():
            if path.name!=generation and UUID.fullmatch(path.name) and path.is_dir() and not path.is_symlink():shutil.rmtree(path)
        for path in (self.root/'work').glob('*.tar.gz.part'):
            if UUID.fullmatch(path.name[:-12]) and path.name!=generation+'.tar.gz.part':self.remove(path)
        self.used=self.usage()
        while self.acks or self.missing:
            if not self.report(force=True):break
        self.report(state='complete',force=True,complete=not(self.acks or self.missing),generation=generation)
        print('Mirror cycle complete: verified files and current metadata; obsolete tracked copies removed.')
        return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(pathlib.Path(args.config).read_text())
    root=pathlib.Path(config['root'])
    # Do not create the root: a missing mount must never become a new empty mirror.
    if not root.is_dir():raise SystemExit('Mirror volume unavailable')
    with (root/'.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Another mirror cycle is already running.');return
        mirror=Mirror(root,config['marker'],Client(config['url'],config['key']),int(config['budget']),int(config['reserve']))
        try:mirror.sync()
        except Exception as error:
            # Do not log URL credentials, tokens, server bodies or private filenames.
            if isinstance(error,RuntimeError) and 'Checksum mismatch' in str(error):
                try:mirror.client.json('snapshot?force=1')
                except Exception:pass
            message=str(error) if isinstance(error,RuntimeError) else 'Соединение или источник недоступны; подтверждённые копии сохранены.'
            try:mirror.report(state='error',force=True,error=message)
            except Exception:pass
            print(message);raise SystemExit(1)
        finally:mirror.index.close()

if __name__=='__main__':main()
