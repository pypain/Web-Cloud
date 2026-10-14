"""One read-only laptop mirror credential, immutable snapshot inventories and confirmations."""
from contextlib import ExitStack
import hashlib,hmac,json,os,pathlib,re,secrets,shutil,sqlite3,tarfile,threading,time,uuid
from urllib.parse import parse_qs

UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
DIGEST=re.compile(r'^[0-9a-f]{64}$')
BUDGET=200_000_000_000

def sha256(path):
    digest=hashlib.sha256()
    with pathlib.Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

class Replica:
    def __init__(self,store,accounts,problem):
        self.store,self.accounts,self.problem=store,accounts,problem
        self.root=store.root/'replica';self.root.mkdir(mode=0o700,exist_ok=True)
        self.lock=threading.RLock();self.building=False;self.build_error='';self.last_build=0
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS device(id INTEGER PRIMARY KEY CHECK(id=1),key_hash TEXT NOT NULL,
            seen REAL,completed REAL,metadata_sig TEXT,report TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS cache(owner TEXT,id TEXT,size INTEGER,sig TEXT,digest TEXT,PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS acks(owner TEXT,id TEXT,digest TEXT,PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,at REAL,sig TEXT,size INTEGER,digest TEXT,files INTEGER,bytes INTEGER);''')
    def connect(self):
        db=sqlite3.connect(self.root/'replica.sqlite',timeout=30);db.row_factory=sqlite3.Row;return db
    def users(self):
        with self.accounts.connect() as db:return db.execute('SELECT * FROM users ORDER BY id').fetchall()
    def signature(self):
        digest=hashlib.sha256()
        for user in self.users():
            store=self.accounts.storage(user)
            with store.connect() as db:
                versions=(db.execute('SELECT version FROM state WHERE id=1').fetchone()[0],db.execute('SELECT version FROM profile WHERE id=1').fetchone()[0])
            values=[user[key] for key in ('id','username','password','role','enabled','quota','legacy')]+list(versions)
            digest.update(json.dumps(values,ensure_ascii=False).encode())
        return digest.hexdigest()
    @staticmethod
    def file_sig(path):
        if path.is_symlink():raise OSError('Source file is a symlink')
        stat=path.stat();return f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'
    def rotate(self):
        key=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO device(id,key_hash) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET key_hash=excluded.key_hash,seen=NULL,completed=NULL,metadata_sig=NULL,report='{}'",(hashlib.sha256(key.encode()).hexdigest(),))
            db.execute('DELETE FROM acks')
        return {'key':key,'budget':BUDGET}
    def authenticate(self,key):
        with self.connect() as db:row=db.execute('SELECT key_hash FROM device WHERE id=1').fetchone()
        if not row or not isinstance(key,str) or len(key)!=43 or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(),row['key_hash']):raise self.problem(401,'Invalid laptop credential')
        with self.connect() as db:db.execute('UPDATE device SET seen=? WHERE id=1',(time.time(),))
    def status(self):
        count=total=done_count=done_bytes=0;live=set()
        with self.connect() as db:
            device=db.execute('SELECT * FROM device WHERE id=1').fetchone()
            for user in self.users():
                store=self.accounts.storage(user)
                for entry in store.state()['entries']:
                    if entry['kind']!='file':continue
                    ident=entry['id'];owner=user['id'];live.add((owner,ident));count+=1;total+=entry['size']
                    row=db.execute('SELECT c.digest,c.sig,a.digest AS ack FROM cache c LEFT JOIN acks a ON a.owner=c.owner AND a.id=c.id WHERE c.owner=? AND c.id=?',(owner,ident)).fetchone()
                    try:valid=row and row['ack']==row['digest'] and row['sig']==self.file_sig(store.path(ident))
                    except OSError:valid=False
                    if valid:done_count+=1;done_bytes+=entry['size']
            deletes=sum((row['owner'],row['id']) not in live for row in db.execute('SELECT owner,id FROM acks'))
        report=json.loads(device['report']) if device else {}
        seen=device['seen'] if device else None
        return {'configured':bool(device),'online':bool(seen and time.time()-seen<600),'lastSeen':seen,
                'lastComplete':device['completed'] if device else None,'totalFiles':count,'totalBytes':total,
                'copiedFiles':done_count,'copiedBytes':done_bytes,'pendingFiles':count-done_count,'pendingBytes':total-done_bytes,
                'pendingPercent':round(100*((total-done_bytes)/total if total else (count-done_count)/count if count else 0),1),
                'pendingDeletes':deletes,'metadataPending':not device or device['metadata_sig']!=self.signature(),
                'budget':BUDGET,'report':report,'preparing':self.building,'prepareError':self.build_error}
    def snapshot(self,force=False):
        current=self.signature();now=time.time()
        with self.lock,self.connect() as db:
            row=db.execute('SELECT * FROM snapshots ORDER BY at DESC LIMIT 1').fetchone()
            if row and row['sig']==current and now-row['at']<3600 and not force and (self.root/(row['id']+'.tar.gz')).is_file():return dict(row)
            if not self.building and (not self.build_error or now-self.last_build>15):
                self.building=True;self.last_build=now
                threading.Thread(target=self._build,daemon=True).start()
            return None
    def _build(self):
        ident=str(uuid.uuid4());stage=self.root/('building-'+ident);stage.mkdir(mode=0o700)
        try:
            digest=hashlib.sha256()
            # Freeze account mapping and all metadata copies briefly; hash content after releasing locks.
            with self.accounts.lock,ExitStack() as locks:
                users=self.users()
                for user in users:
                    store=self.accounts.storage(user)
                    if not store.lock.acquire(blocking=False):raise OSError('Source busy; retry later')
                    locks.callback(store.lock.release)
                with self.accounts.connect() as source,sqlite3.connect(stage/'accounts.sqlite') as target:source.backup(target)
                (stage/'stores').mkdir()
                for user in users:
                    store=self.accounts.storage(user);target_path=stage/'stores'/(user['id']+'.sqlite')
                    with store.lock,store.connect() as source,sqlite3.connect(target_path) as target:
                        source.backup(target)
                        target.execute('DELETE FROM uploads');target.execute('DELETE FROM cancelled_uploads');target.commit()
                        versions=(target.execute('SELECT version FROM state WHERE id=1').fetchone()[0],target.execute('SELECT version FROM profile WHERE id=1').fetchone()[0])
                    values=[user[key] for key in ('id','username','password','role','enabled','quota','legacy')]+list(versions)
                    digest.update(json.dumps(values,ensure_ascii=False).encode())
            # Restore-ready metadata never resurrects old login sessions. Rebuild share routing
            # from the captured per-user databases rather than an in-flight cross-DB index.
            with sqlite3.connect(stage/'accounts.sqlite') as account_snapshot:
                account_snapshot.execute('DELETE FROM sessions');account_snapshot.execute('DELETE FROM attempts');account_snapshot.execute('DELETE FROM share_owners')
                for user in users:
                    with sqlite3.connect(stage/'stores'/(user['id']+'.sqlite')) as source:
                        for token, in source.execute('SELECT token FROM shares'):
                            account_snapshot.execute('INSERT INTO share_owners VALUES (?,?)',(token,user['id']))
            sig=digest.hexdigest();count=total=0
            with sqlite3.connect(stage/'inventory.sqlite') as inventory:
                inventory.execute('CREATE TABLE objects(owner TEXT,id TEXT,size INTEGER,digest TEXT,PRIMARY KEY(owner,id))')
                for user in users:
                    store=self.accounts.storage(user)
                    with sqlite3.connect(stage/'stores'/(user['id']+'.sqlite')) as db:entries=json.loads(db.execute('SELECT entries FROM state WHERE id=1').fetchone()[0])
                    for entry in entries:
                        if entry['kind']!='file':continue
                        path=store.path(entry['id']);before=self.file_sig(path)
                        if path.stat().st_size!=entry['size']:raise OSError('Source file length mismatch')
                        with self.connect() as db:cached=db.execute('SELECT * FROM cache WHERE owner=? AND id=?',(user['id'],entry['id'])).fetchone()
                        file_hash=cached['digest'] if cached and cached['sig']==before else sha256(path)
                        if self.file_sig(path)!=before:raise OSError('Source changed during hashing')
                        with self.connect() as db:db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)',(user['id'],entry['id'],entry['size'],before,file_hash))
                        inventory.execute('INSERT INTO objects VALUES (?,?,?,?)',(user['id'],entry['id'],entry['size'],file_hash))
                        count+=1;total+=entry['size']
            archive=self.root/(ident+'.tar.gz')
            with tarfile.open(archive,'w:gz') as output:
                for path in sorted(stage.rglob('*.sqlite')):output.add(path,arcname=str(path.relative_to(stage)),recursive=False)
            with archive.open('rb') as handle:os.fsync(handle.fileno())
            archive_hash=sha256(archive)
            os.replace(stage/'inventory.sqlite',self.root/(ident+'.sqlite'))
            with self.connect() as db:db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',(ident,time.time(),sig,archive.stat().st_size,archive_hash,count,total))
            self.build_error=''
            # Keep the last three complete metadata snapshots. Files themselves stay in their normal stores.
            with self.connect() as db:
                for row in db.execute('SELECT id FROM snapshots ORDER BY at DESC LIMIT -1 OFFSET 3').fetchall():
                    (self.root/(row['id']+'.tar.gz')).unlink(missing_ok=True);(self.root/(row['id']+'.sqlite')).unlink(missing_ok=True);db.execute('DELETE FROM snapshots WHERE id=?',(row['id'],))
        except Exception:
            self.build_error='Не удалось подготовить снимок. Источник мог измениться; агент повторит попытку.'
            (self.root/(ident+'.tar.gz')).unlink(missing_ok=True)
            (self.root/(ident+'.sqlite')).unlink(missing_ok=True)
        finally:
            shutil.rmtree(stage,ignore_errors=True)
            with self.lock:self.building=False
    def report(self,data):
        if not isinstance(data,dict):raise self.problem(400,'Invalid report')
        acks=data.get('acks',[]);missing=data.get('missing',[])
        if not isinstance(acks,list) or not isinstance(missing,list) or len(acks)>500 or len(missing)>500:raise self.problem(400,'Invalid confirmations')
        def valid_id(value):return isinstance(value,str) and UUID.fullmatch(value)
        for item in acks+missing:
            if not isinstance(item,dict) or not valid_id(item.get('owner')) or not valid_id(item.get('id')):raise self.problem(400,'Invalid confirmation ID')
        with self.connect() as db:
            for item in missing:db.execute('DELETE FROM acks WHERE owner=? AND id=?',(item['owner'],item['id']))
            for item in acks:
                if not isinstance(item.get('digest'),str) or not DIGEST.fullmatch(item['digest']):raise self.problem(400,'Invalid digest')
                cached=db.execute('SELECT digest FROM cache WHERE owner=? AND id=?',(item['owner'],item['id'])).fetchone()
                if cached and cached['digest']==item['digest']:db.execute('INSERT OR REPLACE INTO acks VALUES (?,?,?)',(item['owner'],item['id'],item['digest']))
            telemetry={}
            for key in ('usedBytes','freeBytes','activeBytes','activeSize'):
                if key in data:
                    if type(data[key]) is not int or not 0<=data[key]<=10**16:raise self.problem(400,'Invalid telemetry')
                    telemetry[key]=data[key]
            state=data.get('state','syncing')
            if state not in ('syncing','complete','error'):raise self.problem(400,'Invalid state')
            telemetry['state']=state
            if isinstance(data.get('error'),str):telemetry['error']=data['error'][:300]
            db.execute('UPDATE device SET report=?,seen=? WHERE id=1',(json.dumps(telemetry),time.time()))
            if data.get('complete'):
                row=db.execute('SELECT sig,files FROM snapshots WHERE id=?',(data.get('generation'),)).fetchone()
                if not row:raise self.problem(409,'Snapshot expired; retry')
                with sqlite3.connect(self.root/(data['generation']+'.sqlite')) as inventory:
                    for item in inventory.execute('SELECT owner,id,digest FROM objects'):
                        ack=db.execute('SELECT digest FROM acks WHERE owner=? AND id=?',item[:2]).fetchone()
                        if not ack or ack['digest']!=item[2]:raise self.problem(409,'Not all files have been confirmed')
                if db.execute('SELECT COUNT(*) FROM acks').fetchone()[0]!=row['files']:raise self.problem(409,'Obsolete copies have not been cleared')
                if row['sig']!=self.signature():raise self.problem(409,'Source metadata changed; next cycle will catch up')
                db.execute('UPDATE device SET completed=?,metadata_sig=? WHERE id=1',(time.time(),row['sig']))
        return {'ok':True}
    def source_file(self,owner,ident):
        if not UUID.fullmatch(owner) or not UUID.fullmatch(ident):raise self.problem(404,'File unavailable')
        with self.accounts.connect() as db:user=db.execute('SELECT * FROM users WHERE id=?',(owner,)).fetchone()
        if not user:raise self.problem(404,'File unavailable')
        store=self.accounts.storage(user)
        with store.lock:
            entry=next((e for e in store.state()['entries'] if e['id']==ident and e['kind']=='file'),None)
            if not entry:raise self.problem(404,'File unavailable')
            path=store.path(ident)
            if path.is_symlink():raise self.problem(404,'File unavailable')
            try:return path.open('rb')
            except FileNotFoundError:raise self.problem(404,'File unavailable')
    def send_file(self,handler,stream):
        with stream:
            size=os.fstat(stream.fileno()).st_size;start=0;end=size-1;code=200
            if handler.headers.get('Range'):
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',handler.headers['Range'])
                if not match:raise self.problem(416,'Invalid range')
                start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
                if start>=size or start>end:raise self.problem(416,'Invalid range')
                code=206
            handler.send_response(code);handler.send_header('Content-Type','application/octet-stream')
            handler.send_header('Content-Length',str(max(0,end-start+1)));handler.send_header('Accept-Ranges','bytes')
            if code==206:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers()
            stream.seek(start);remaining=end-start+1
            while remaining>0:
                block=stream.read(min(remaining,1024*1024))
                if not block:break
                handler.wfile.write(block);remaining-=len(block)
    def handle(self,handler,path,parsed,write):
        self.authenticate(handler.headers.get('X-Cloud-Replica',''))
        if path=='/replica/report' and handler.command=='PUT':return handler.reply(200,self.report(handler.json_body(256*1024)))
        if handler.command!='GET':raise self.problem(405,'Read-only laptop credential')
        if path=='/replica/snapshot':
            info=self.snapshot(force=parse_qs(parsed.query).get('force')==['1'])
            return handler.reply(200 if info else 202,info or {'preparing':True,'error':self.build_error})
        if path.startswith('/replica/snapshot/'):
            ident=path[len('/replica/snapshot/'):]
            if not UUID.fullmatch(ident):raise self.problem(404,'Snapshot unavailable')
            with self.connect() as db:row=db.execute('SELECT id FROM snapshots WHERE id=?',(ident,)).fetchone()
            if not row:raise self.problem(404,'Snapshot unavailable')
            try:stream=(self.root/(ident+'.tar.gz')).open('rb')
            except FileNotFoundError:raise self.problem(404,'Snapshot unavailable')
            return self.send_file(handler,stream)
        if path.startswith('/replica/files/'):
            fields=path[len('/replica/files/'):].split('/')
            if len(fields)!=2:raise self.problem(404,'File unavailable')
            return self.send_file(handler,self.source_file(*fields))
        raise self.problem(404,'Not found')
