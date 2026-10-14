#!/usr/bin/env bash
# Upgrade only private cloud code; retain existing files, metadata and credentials.
set -euo pipefail
umask 077
[[ $EUID == 0 && ${1:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Run as root with full deployment commit SHA'; exit 2; }
ref=$1
if [[ -f /var/lib/hxry-cloud/accounts.sqlite ]]; then
  echo 'Accounts are enabled; use deploy/install-accounts.sh with this commit SHA.'
  exit 2
fi
url=https://raw.githubusercontent.com/pypain/Web-Cloud/$ref
if [[ ! -e /etc/systemd/system/hxry-cloud.service ]]; then
  echo 'Backend is not installed yet; running the first-install path.'
  installer=$(mktemp /root/hxry-cloud-first-install-XXXXXXXX.sh)
  curl -fSL "$url/deploy/install-backend.sh" -o "$installer"
  exec bash "$installer" "$ref"
fi
root=/var/www/hxry-cloud
backend=/opt/hxry-cloud-backend
[[ -L $root/current && -L $backend/current ]] || { echo 'Unexpected deployment layout'; exit 1; }
[[ $(systemctl show hxry-cloud -p User --value) == hxry-cloud ]] || { echo 'Unexpected service user'; exit 1; }
old_ui=$(readlink "$root/current"); old_backend=$(readlink "$backend/current")
[[ $old_ui =~ ^releases/[a-f0-9]{40}$ && $old_backend =~ ^releases/[a-f0-9]{40}$ ]] || exit 1
[[ -d $root/$old_ui && -d $backend/$old_backend ]] || exit 1
[[ ! -e $root/releases/$ref && ! -e $backend/releases/$ref ]] || { echo 'Release already exists; refusing overwrite'; exit 1; }
backup=$(mktemp -d /root/hxry-upload-upgrade-XXXXXXXX)
chmod 700 "$backup"
curl -fSL "$url/backend/replica.py" -o "$backup/replica.py"
curl -fSL "$url/backend/accounts.py" -o "$backup/accounts.py"
curl -fSL "$url/backend/server.py" -o "$backup/server.py"
curl -fSL "$url/deploy/server-dist.tar.gz" -o "$backup/server-dist.tar.gz"
curl -fSL "$url/deploy/public-shares.conf" -o "$backup/public-shares.conf"
curl -fSL "$url/deploy/python-runtime.conf" -o "$backup/python-runtime.conf"
cp -p /etc/nginx/snippets/hxry-cloud.conf "$backup/snippet.conf"
printf '%s  %s\n' 'd357c484b068f4e29f41c9ca1c377868ddd01ae76f6c52f9666da6be24ad4fa0' "$backup/server.py" | sha256sum -c -
printf '%s  %s\n' 'fe08132dd8841538a844b7886cda50658dbca5737b8426c3556a90e59b78947d' "$backup/server-dist.tar.gz" | sha256sum -c -
printf '%s  %s\n' '74f7862e10ec80f624603bd0df60ef200a95cb0ad489de3371b8ec852f4955b9' "$backup/accounts.py" | sha256sum -c -
printf '%s  %s\n' 'f036ada78e7426d8fcaa3eb6464e364a5b1b0dfdd3896f3f0be069e05d4a8929' "$backup/replica.py" | sha256sum -c -
python3 - "$backup/server-dist.tar.gz" <<'PY'
import pathlib,sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert not name.is_absolute() and '..' not in name.parts and (member.isfile() or member.isdir())
PY
printf '%s\n' "$old_ui" > "$backup/ui-link"
printf '%s\n' "$old_backend" > "$backup/backend-link"
python3 - "$backup" <<'PY'
import pathlib,sys
token=pathlib.Path('/etc/hxry-cloud/backend-token').read_text().strip()
assert len(token)==64 and all(c in '0123456789abcdef' for c in token)
(pathlib.Path(sys.argv[1])/'curl.conf').write_text('header = "X-Cloud-Token: '+token+'"\n')
root=pathlib.Path(sys.argv[1]);text=(root/'snippet.conf').read_text()
assert 'location ^~ /cloud/api/' in text
if 'location ^~ /cloud/share/' not in text:
    public=(root/'public-shares.conf').read_text().replace('__BACKEND_TOKEN__',token)
    text=public+'\n'+text
    marker='    proxy_set_header X-Cloud-Token "'+token+'";'
    # Only the private API block receives an explicit isolation header.
    start=text.index('location ^~ /cloud/api/');tail=text[start:]
    assert marker in tail
    if 'X-Cloud-Public "0"' not in tail:
        tail=tail.replace(marker,marker+'\n    proxy_set_header X-Cloud-Public "0";',1)
    text=text[:start]+tail
(root/'new-snippet.conf').write_text(text)
if 'location ^~ /cloud/python-runtime/' not in text:
    text=(root/'python-runtime.conf').read_text()+'\n'+text
    (root/'new-snippet.conf').write_text(text)
PY
cat > "$backup/restore.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for name in ui backend; do
  if [[ $name == ui ]]; then root=/var/www/hxry-cloud; else root=/opt/hxry-cloud-backend; fi
  target=$(cat "$dir/$name-link")
  [[ $target =~ ^releases/[a-f0-9]{40}$ ]] || exit 1
  ln -s "$target" "$root/rollback-link"
  mv -Tf "$root/rollback-link" "$root/current"
done
cp -p "$dir/snippet.conf" /etc/nginx/snippets/hxry-cloud.conf
nginx -t
systemctl reload nginx
systemctl restart hxry-cloud
echo 'Previous cloud code restored. File data and SQLite metadata retained.'
RESTORE
echo "Rollback: bash $backup/restore.sh"
rollback(){ result=$?; trap - ERR; bash "$backup/restore.sh" || echo "Use $backup/restore.sh" >&2; exit "$result"; }
trap rollback ERR
install -d -m 755 "$root/releases/$ref" "$backend/releases/$ref"
install -m 644 "$backup/server.py" "$backend/releases/$ref/server.py"
install -m 644 "$backup/accounts.py" "$backend/releases/$ref/accounts.py"
install -m 644 "$backup/replica.py" "$backend/releases/$ref/replica.py"
tar -C "$root/releases/$ref" -xzf "$backup/server-dist.tar.gz" --no-same-owner
find "$root/releases/$ref" -type d -exec chmod 755 {} +
find "$root/releases/$ref" -type f -exec chmod 644 {} +
systemctl stop hxry-cloud
python3 - "$backup" <<'PY'
import pathlib,sqlite3,sys
source=sqlite3.connect('/var/lib/hxry-cloud/state.sqlite')
destination=sqlite3.connect(str(pathlib.Path(sys.argv[1])/'metadata-before.sqlite'))
source.backup(destination);destination.close();source.close()
PY
for name in ui backend; do
  if [[ $name == ui ]]; then directory=$root; else directory=$backend; fi
  ln -s "releases/$ref" "$directory/upgrade-link"
  mv -Tf "$directory/upgrade-link" "$directory/current"
done
systemctl start hxry-cloud
ready=0
for attempt in {1..30}; do
  if curl --noproxy '*' --unix-socket /run/hxry-cloud/backend.sock --config "$backup/curl.conf" -fsS --max-time 2 http://localhost/state > "$backup/state-after.json" 2>/dev/null; then ready=1;break;fi
  sleep .5
done
[[ $ready == 1 ]] || { echo 'New backend did not start'; false; }
python3 - "$backup" <<'PY'
import json,pathlib,sqlite3,sys
root=pathlib.Path(sys.argv[1])
with sqlite3.connect(root/'metadata-before.sqlite') as db: before=json.loads(db.execute('SELECT entries FROM state WHERE id=1').fetchone()[0])
after=json.loads((root/'state-after.json').read_text())
assert before==after['entries'],'Committed file metadata changed unexpectedly'
assert after['chunkSize']==4194304
print('Existing metadata preserved; new upload protocol ready')
PY
install -m 640 -o root -g www-data "$backup/new-snippet.conf" /etc/nginx/snippets/hxry-cloud.conf
nginx -t
systemctl reload nginx
python3 - <<'PY'
import subprocess,time
deadline=time.monotonic()+25
while True:
    p=subprocess.run(['curl','--noproxy','*','--resolve','hxry.ru:443:127.0.0.1','--connect-timeout','3','--max-time','4',
                      '-sS','-D','-','-o','/dev/null','-w','%{http_code}',
                      'https://hxry.ru/cloud/share/'+'A'*43],capture_output=True,text=True,timeout=6)
    if p.returncode==0 and p.stdout.strip().endswith('404') and 'x-cloud-share: public' in p.stdout.lower():break
    if time.monotonic()>=deadline:raise SystemExit('Public share route did not become ready')
    time.sleep(.5)
PY
for endpoint in /cloud/ /cloud/api/state; do
  code=$(curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -sS --max-time 15 -o /dev/null -w '%{http_code}' "https://hxry.ru$endpoint")
  [[ $code == 401 ]] || { echo "Private route $endpoint is not protected ($code)"; false; }
done
python3 - <<'PY'
import subprocess,time
deadline=time.monotonic()+25
while True:
    p=subprocess.run(['curl','--noproxy','*','--resolve','hxry.ru:443:127.0.0.1','--connect-timeout','3','--max-time','4',
                      '-H','Origin: null','-sS','-D','-','-o','/dev/null','-w','%{http_code}',
                      'https://hxry.ru/cloud/python-runtime/pyodide.js'],capture_output=True,text=True,timeout=6)
    if p.returncode==0 and p.stdout.strip().endswith('200') and 'access-control-allow-origin: *' in p.stdout.lower():break
    if time.monotonic()>=deadline:raise SystemExit('Python runtime route did not become ready')
    time.sleep(.5)
PY
curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --user cloudadmin -fsS --max-time 15 https://hxry.ru/cloud/api/state > "$backup/authenticated.json"
python3 - "$backup/authenticated.json" <<'PY'
import json,pathlib,sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text())['chunkSize']==4194304
print('Upload-capable API verified through nginx')
PY
rm -f "$backup/curl.conf"
trap - ERR
echo 'Upgrade complete. Refresh the cloud page; existing files and password are retained.'
echo 'Backup contains private metadata; keep it on the host. External backups remain unconfigured.'
