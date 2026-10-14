#!/usr/bin/env python3
import getpass,grp,json,os,pathlib,re,shutil,sys,uuid
from agent import Client

root=pathlib.Path('/var/lib/hxry-cloud-replica')
config=pathlib.Path('/etc/hxry-cloud-replica/config.json')
previous=json.loads(config.read_text()) if config.exists() else {}
if root.is_symlink():raise SystemExit('Replica root must not be a symlink')
if root.exists() and any(root.iterdir()) and not (root/'.replica-root').is_file():raise SystemExit('Existing nonempty directory is not a managed mirror; nothing overwritten')
key=getpass.getpass('Laptop key from the cloud admin page (Enter keeps the existing key): ')
if not key:key=previous.get('key','')
if not re.fullmatch(r'[A-Za-z0-9_-]{43}',key):raise SystemExit('Invalid laptop key. Generate it in /cloud/admin/ and paste it locally.')
url=previous.get('url','https://hxry.ru/cloud/api/replica')
try:Client(url,key).json('snapshot')
except Exception:raise SystemExit('Cloud/key check failed. First update the VPS and generate the laptop key in the admin page.')
if shutil.disk_usage(root if root.exists() else root.parent).free<20_000_000_000:raise SystemExit('Less than 20 GB free; refusing setup')
root.mkdir(mode=0o700,exist_ok=True)
marker=(root/'.replica-root').read_text().strip() if (root/'.replica-root').exists() else str(uuid.uuid4())
if previous and previous.get('marker')!=marker:raise SystemExit('Existing configuration belongs to a different mirror root')
(root/'.replica-root').write_text(marker+'\n')
config.parent.mkdir(mode=0o750,exist_ok=True)
if config.exists():
    backup=config.with_name('config.before-update.json');shutil.copyfile(config,backup);backup.chmod(0o600)
data={'root':str(root),'marker':marker,'url':url,'key':key,'budget':200_000_000_000,'reserve':20_000_000_000}
temporary=config.with_suffix('.new');temporary.write_text(json.dumps(data,indent=2)+'\n');temporary.chmod(0o640)
os.chown(temporary,0,grp.getgrnam('hxry-cloud-replica').gr_gid);os.replace(temporary,config)
print('Configured: 200 GB mirror budget, at least 20 GB free for Arch. Key saved privately; not printed.')
