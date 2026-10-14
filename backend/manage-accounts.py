#!/usr/bin/env python3
"""Offline recovery/bootstrap; run locally on the host as hxry-cloud or root."""
import argparse
import getpass
import hashlib
import pathlib
import secrets
import time
from accounts import Accounts, COOKIE, password_hash
from server import Store, Problem

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['bootstrap','reset-admin','probe'])
parser.add_argument('root')
parser.add_argument('token_file')
parser.add_argument('--output')
args=parser.parse_args()
store=Store(args.root,pathlib.Path(args.token_file).read_text().strip())
accounts=Accounts(store,Problem)
with accounts.connect() as db:admin=db.execute('SELECT * FROM users WHERE legacy=1').fetchone()
if args.action=='bootstrap':
    if admin:print('Existing administrator retained; password unchanged.')
    else:
        print('Create cloudadmin website account. Your existing files stay in this account.')
        password=getpass.getpass('New website password (12+ characters): ')
        if password!=getpass.getpass('Repeat website password: '):raise SystemExit('Passwords differ')
        try:accounts.create('cloudadmin',password,role='admin',legacy=True)
        except Problem as error:raise SystemExit(error.message)
        print('Administrator created; existing files and public links retained.')
elif args.action=='reset-admin':
    if not admin:raise SystemExit('No original administrator found; run bootstrap first')
    password=getpass.getpass('New cloudadmin password (12+ characters): ')
    if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
    try:encoded=password_hash(password)
    except ValueError as error:raise SystemExit(str(error))
    with accounts.connect() as db:
        db.execute("UPDATE users SET password=?,role='admin',enabled=1 WHERE id=?",(encoded,admin['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(admin['id'],))
        accounts.audit(db,'local-console','administrator.recovered',admin['username'])
    print('Administrator recovered. All previous sessions revoked.')
else:
    with accounts.connect() as db:admin=db.execute("SELECT * FROM users WHERE role='admin' AND enabled=1 ORDER BY legacy DESC,created LIMIT 1").fetchone()
    if not admin:raise SystemExit('No active administrator; use reset-admin from the console')
    if not args.output:raise SystemExit('--output is required')
    token=secrets.token_urlsafe(32);now=int(time.time())
    with accounts.connect() as db:
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),admin['id'],secrets.token_urlsafe(32),now,now,now+120))
        accounts.audit(db,'local-console','deployment.probe',admin['username'])
    target=pathlib.Path(args.output)
    with target.open('x') as output:output.write('header = "Cookie: '+COOKIE+'='+token+'"\n')
    target.chmod(0o600)
    print('Short-lived local deployment check session created (120 seconds).')
