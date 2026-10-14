"""Accounts and opaque sessions. Storage is isolated by server-generated user IDs."""
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import sqlite3
import threading
import time
import uuid
from http.cookies import SimpleCookie, CookieError

COOKIE = '__Secure-hxry_session'
KDF = threading.BoundedSemaphore(2)
LOGIN = re.compile(r'^[a-z0-9][a-z0-9_.-]{2,31}$')


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Пароль должен содержать от 12 до 256 символов.')
    salt = secrets.token_hex(16)
    with KDF:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=5).hex()
    return 'scrypt$16384$8$5$' + salt + '$' + digest


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        scheme,n,r,p,salt,expected=encoded.split('$')
        if (scheme,n,r,p)!=('scrypt','16384','8','5') or len(salt)!=32 or len(expected)!=128:return False
        salt_bytes=bytes.fromhex(salt)
    except (ValueError,TypeError):return False
    with KDF:
        digest = hashlib.scrypt(password.encode(), salt=salt_bytes, n=16384, r=8, p=5).hex()
    return hmac.compare_digest(digest, expected)


class Accounts:
    def __init__(self, legacy, problem):
        self.legacy, self.problem = legacy, problem
        self.root = legacy.root
        self.lock = threading.RLock()
        self.stores = {}
        self.max_file=legacy.max_file
        self.dummy = password_hash(secrets.token_urlsafe(32))
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','user')), enabled INTEGER NOT NULL, quota INTEGER NOT NULL,
                legacy INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, last_login INTEGER);
            CREATE UNIQUE INDEX IF NOT EXISTS sole_legacy ON users(legacy) WHERE legacy=1;
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
                created INTEGER NOT NULL, last_seen INTEGER NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, until INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS share_owners(token TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL,
                event TEXT NOT NULL, target TEXT NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.root / 'accounts.sqlite', timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def audit(self, db, actor, event, target=''):
        db.execute('INSERT INTO audit(at,actor,event,target) VALUES (?,?,?,?)', (int(time.time()), actor, event, target))
        db.execute('DELETE FROM audit WHERE id <= (SELECT COALESCE(MAX(id),0)-5000 FROM audit)')

    def public_user(self, row):
        return {**{key: row[key] for key in ('id','username','role','enabled','quota','created','last_login')},'originalOwner':bool(row['legacy'])}

    def create(self, username, password, role='user', quota=None, legacy=False, actor='bootstrap'):
        if not isinstance(username,str) or not LOGIN.fullmatch(username) or role not in ('admin','user'):
            raise self.problem(400, 'Логин: 3–32 символа, строчные латинские буквы, цифры, точка, _ или -.')
        quota = self.legacy.quota if quota is None else quota
        if type(quota) is not int or not 1024**3 <= quota <= 100*1024**3:
            raise self.problem(400,'Лимит хранилища: от 1 до 100 ГБ.')
        try: encoded = password_hash(password)
        except ValueError as error: raise self.problem(400,str(error))
        ident = str(uuid.uuid4())
        with self.lock, self.connect() as db:
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= 100:
                raise self.problem(413,'Достигнут лимит 100 аккаунтов.')
            try: db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?, ?,NULL)',
                            (ident,username,encoded,role,1,quota,int(legacy),int(time.time())))
            except sqlite3.IntegrityError: raise self.problem(409,'Этот логин уже занят.')
            self.audit(db,actor,'account.created',username)
            row=db.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
            if legacy:
                with self.legacy.connect() as storage:
                    for token, in storage.execute('SELECT token FROM shares'):
                        db.execute('INSERT OR REPLACE INTO share_owners VALUES (?,?)',(token,ident))
        return self.public_user(row)

    def storage(self, user):
        with self.lock:
            if user['legacy']:
                self.legacy.quota=user['quota'];self.legacy.max_file=min(self.max_file,user['quota'])
                return self.legacy
            ident=user['id']
            if ident not in self.stores:
                self.stores[ident]=type(self.legacy)(self.root/'users'/ident,self.legacy.token,
                    quota=user['quota'],max_file=min(self.max_file,user['quota']),reserve=self.legacy.reserve)
            store=self.stores[ident]
            store.quota=user['quota'];store.max_file=min(self.max_file,user['quota'])
            return store

    def login(self, username, password, client):
        username=username if isinstance(username,str) else ''
        if not LOGIN.fullmatch(username):username=''
        now=int(time.time())
        buckets=['ip:'+hashlib.sha256(client.encode()).hexdigest(),'name:'+username]
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM attempts WHERE until<=?',(now,))
            db.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<?',(now,now-1800))
            for bucket,limit in zip(buckets,(60,10)):
                row=db.execute('SELECT hits FROM attempts WHERE bucket=?',(bucket,)).fetchone()
                if row and row['hits']>=limit:raise self.problem(429,'Слишком много попыток. Повторите через 15 минут.')
            if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]>20000:
                raise self.problem(429,'Слишком много запросов. Повторите позже.')
            # Reserve attempt before the KDF so concurrent requests cannot bypass throttling.
            for bucket in buckets:
                db.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET hits=hits+1',(bucket,now+900))
            row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        valid=password_matches(password,row['password'] if row else self.dummy)
        if not row or not valid or not row['enabled']:
            with self.connect() as db:self.audit(db,'anonymous','login.failed',username)
            raise self.problem(401,'Неверный логин или пароль.')
        token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest();csrf=secrets.token_urlsafe(32)
        with self.lock,self.connect() as db:
            current=db.execute('SELECT * FROM users WHERE id=?',(row['id'],)).fetchone()
            if not current['enabled'] or current['password']!=row['password']:raise self.problem(401,'Неверный логин или пароль.')
            db.execute('DELETE FROM attempts WHERE bucket=?',(buckets[1],))
            # Bound session count for one account to the last ten logins.
            db.execute('DELETE FROM sessions WHERE token IN (SELECT token FROM sessions WHERE user_id=? ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET 9)',(row['id'],))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',(digest,row['id'],csrf,now,now,now+43200))
            db.execute('UPDATE users SET last_login=? WHERE id=?',(now,row['id']))
            self.audit(db,row['id'],'login.success',row['username'])
        return token

    def session(self, header):
        try:
            cookies=SimpleCookie();cookies.load(header or '')
            token=cookies[COOKIE].value
        except (KeyError,ValueError,CookieError):raise self.problem(401,'Войдите в аккаунт.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):raise self.problem(401,'Войдите в аккаунт.')
        digest=hashlib.sha256(token.encode()).hexdigest();now=int(time.time())
        with self.lock,self.connect() as db:
            row=db.execute('SELECT u.*,s.csrf,s.token AS session_token,s.expires,s.last_seen FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?',(digest,)).fetchone()
            if not row or not row['enabled'] or row['expires']<=now or row['last_seen']<now-1800:
                raise self.problem(401,'Сессия завершена. Войдите снова.')
            if now-row['last_seen']>=60:db.execute('UPDATE sessions SET last_seen=? WHERE token=?',(now,digest))
        return row

    def index_shares(self, user, store):
        with store.connect() as storage,self.connect() as db:
            tokens={row[0] for row in storage.execute('SELECT token FROM shares')}
            old={row[0] for row in db.execute('SELECT token FROM share_owners WHERE user_id=?',(user['id'],))}
            db.executemany('DELETE FROM share_owners WHERE token=?',[(token,) for token in old-tokens])
            db.executemany('INSERT OR IGNORE INTO share_owners VALUES (?,?)',[(token,user['id']) for token in tokens])

    def public_storage(self, token):
        with self.connect() as db:
            user=db.execute('SELECT u.* FROM users u JOIN share_owners s ON s.user_id=u.id WHERE s.token=? AND u.enabled=1',(token,)).fetchone()
        if not user:raise self.problem(404,'Link unavailable')
        return self.storage(user)

    def update(self, actor, ident, data):
        if not isinstance(data,dict):raise self.problem(400,'Invalid account settings')
        with self.lock,self.connect() as db:
            target=db.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
            if not target:raise self.problem(404,'Аккаунт не найден.')
            role=data.get('role',target['role']);enabled=data.get('enabled',bool(target['enabled']));quota=data.get('quota',target['quota'])
            if role not in ('admin','user') or type(enabled) is not bool or type(quota) is not int or not 1024**3<=quota<=100*1024**3:
                raise self.problem(400,'Некорректные настройки аккаунта.')
            if ident==actor['id'] and (not enabled or role!='admin'):
                raise self.problem(400,'Нельзя заблокировать себя или снять собственную роль администратора.')
            if target['role']=='admin' and target['enabled'] and (not enabled or role!='admin') and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                raise self.problem(400,'Нужен хотя бы один активный администратор.')
            store=self.storage(target)
            if store.used()>quota:
                raise self.problem(400,'Лимит меньше объёма уже сохранённых файлов.')
            encoded=None
            if 'password' in data:
                try:encoded=password_hash(data['password'])
                except ValueError as error:raise self.problem(400,str(error))
            db.execute('UPDATE users SET role=?,enabled=?,quota=? WHERE id=?',(role,int(enabled),quota,ident))
            if encoded:db.execute('UPDATE users SET password=? WHERE id=?',(encoded,ident))
            if encoded or not enabled or role!=target['role']:
                db.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
            self.audit(db,actor['id'],'account.updated',target['username'])
            if encoded:self.audit(db,actor['id'],'password.reset',target['username'])
            if not enabled:
                with store.lock,store.connect() as storage:storage.execute('DELETE FROM shares')
                db.execute('DELETE FROM share_owners WHERE user_id=?',(ident,))
            if target['legacy']:
                self.legacy.quota=quota;self.legacy.max_file=min(5*1024**3,quota)
        return {'ok':True}

    def change_password(self,user,current,new):
        if not password_matches(current,user['password']):raise self.problem(403,'Текущий пароль неверен.')
        try:encoded=password_hash(new)
        except ValueError as error:raise self.problem(400,str(error))
        with self.lock,self.connect() as db:
            result=db.execute('UPDATE users SET password=? WHERE id=? AND password=?',(encoded,user['id'],user['password']))
            if result.rowcount!=1:raise self.problem(409,'Пароль уже изменён. Войдите снова.')
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            self.audit(db,user['id'],'password.changed',user['username'])
