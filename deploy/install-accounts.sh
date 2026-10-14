#!/usr/bin/env bash
# Enable/update website accounts on the existing cloud, with preserved owner data and rollback.
set -euo pipefail
umask 077
[[ $EUID == 0 && ${1:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Run as root with the full commit SHA'; exit 2; }
ref=$1
root=/var/www/hxry-cloud
backend=/opt/hxry-cloud-backend
data=/var/lib/hxry-cloud
snippet=/etc/nginx/snippets/hxry-cloud.conf
for tool in curl python3 nginx systemctl sha256sum tar; do command -v "$tool" >/dev/null; done
[[ -L $root/current && -L $backend/current && -f $snippet && ! -L $snippet && -f $data/state.sqlite ]] || { echo 'Existing cloud backend required'; exit 1; }
[[ $(systemctl show hxry-cloud -p User --value) == hxry-cloud ]] || exit 1
old_ui=$(readlink "$root/current");old_backend=$(readlink "$backend/current")
[[ $old_ui =~ ^releases/[a-f0-9]{40}$ && $old_backend =~ ^releases/[a-f0-9]{40}$ ]] || exit 1
[[ -d $root/$old_ui && -d $backend/$old_backend ]] || exit 1
# Safe retries use a fresh commit. Do not overwrite a release that might be in use.
if [[ $old_ui == releases/$ref || $old_backend == releases/$ref ]];then
  echo 'This release is already active. Use the login page, or the printed restore script if deployment was interrupted.'
  exit 2
fi
backup=$(mktemp -d /root/hxry-accounts-backup-XXXXXXXX)
chmod 700 "$backup"
url=https://raw.githubusercontent.com/pypain/Web-Cloud/$ref
for path in backend/server.py backend/accounts.py backend/manage-accounts.py backend/replica.py deploy/server-dist.tar.gz deploy/hxry-cloud-accounts.conf; do
    curl -fSL "$url/$path" -o "$backup/${path##*/}"
done
printf '%s  %s\n' 'd357c484b068f4e29f41c9ca1c377868ddd01ae76f6c52f9666da6be24ad4fa0' "$backup/server.py" | sha256sum -c -
printf '%s  %s\n' '74f7862e10ec80f624603bd0df60ef200a95cb0ad489de3371b8ec852f4955b9' "$backup/accounts.py" | sha256sum -c -
printf '%s  %s\n' '650bdcd28db96cac08b898d6a0b86aae90a0060feb284f4b16f608644e714f01' "$backup/manage-accounts.py" | sha256sum -c -
printf '%s  %s\n' 'fe08132dd8841538a844b7886cda50658dbca5737b8426c3556a90e59b78947d' "$backup/server-dist.tar.gz" | sha256sum -c -
printf '%s  %s\n' '94eda424e1aaa7bd965f3692157fcdc5f17a69d2e1279d2d8a909410060cbd4b' "$backup/hxry-cloud-accounts.conf" | sha256sum -c -
printf '%s  %s\n' 'f036ada78e7426d8fcaa3eb6464e364a5b1b0dfdd3896f3f0be069e05d4a8929' "$backup/replica.py" | sha256sum -c -
cp -p "$snippet" "$backup/snippet.conf"
printf '%s\n' "$old_ui" > "$backup/ui-link"
printf '%s\n' "$old_backend" > "$backup/backend-link"
python3 - "$backup" <<'PY'
import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1])
with tarfile.open(root/'server-dist.tar.gz') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert not name.is_absolute() and '..' not in name.parts and (member.isfile() or member.isdir())
token=pathlib.Path('/etc/hxry-cloud/backend-token').read_text().strip()
assert len(token)==64 and all(c in '0123456789abcdef' for c in token)
(root/'new-snippet.conf').write_text((root/'hxry-cloud-accounts.conf').read_text().replace('__BACKEND_TOKEN__',token))
(root/'backend-curl.conf').write_text('header = "X-Cloud-Token: '+token+'"\n')
PY
cat > "$backup/restore.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for name in ui backend; do
  if [[ $name == ui ]]; then root=/var/www/hxry-cloud;else root=/opt/hxry-cloud-backend;fi
  target=$(cat "$dir/$name-link")
  [[ $target =~ ^releases/[a-f0-9]{40}$ ]] || exit 1
  ln -s "$target" "$root/accounts-rollback-link"
  mv -Tf "$root/accounts-rollback-link" "$root/current"
done
cp -p "$dir/snippet.conf" /etc/nginx/snippets/hxry-cloud.conf
nginx -t
systemctl restart hxry-cloud
systemctl reload nginx
echo 'Previous code and nginx restored. Existing and newly created account data retained locally.'
RESTORE
chmod 700 "$backup/restore.sh"
echo "Rollback: bash $backup/restore.sh"
rollback(){ result=$?;trap - EXIT INT TERM HUP;if [[ $result == 0 ]];then return;fi;bash "$backup/restore.sh" || echo "Use $backup/restore.sh" >&2;exit "$result"; }
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
for name in ui backend; do
  if [[ $name == ui ]];then directory=$root;else directory=$backend;fi
  if [[ -e $directory/releases/$ref || -L $directory/releases/$ref ]];then
    [[ ! -L $directory/releases/$ref && -d $directory/releases/$ref ]] || { echo 'Unexpected release path';false; }
    mv "$directory/releases/$ref" "$backup/previous-failed-$name"
  fi
done
install -d -m 755 "$root/releases/$ref" "$backend/releases/$ref"
for name in server.py accounts.py manage-accounts.py replica.py; do install -m 644 "$backup/$name" "$backend/releases/$ref/$name";done
tar -C "$root/releases/$ref" -xzf "$backup/server-dist.tar.gz" --no-same-owner
find "$root/releases/$ref" -type d -exec chmod 755 {} +
find "$root/releases/$ref" -type f -exec chmod 644 {} +
# Preserve responses from the existing non-cloud website.
for endpoint in / /account; do
  curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -fsS --max-time 15 "https://hxry.ru$endpoint" > "$backup/site-${endpoint//\//_}-before"
done
systemctl stop hxry-cloud
python3 - "$backup" <<'PY'
import pathlib,sqlite3,sys
backup=pathlib.Path(sys.argv[1]);data=pathlib.Path('/var/lib/hxry-cloud')
paths=[data/'state.sqlite']+list((data/'users').glob('*/state.sqlite'))
if (data/'accounts.sqlite').exists():paths.append(data/'accounts.sqlite')
for i,path in enumerate(paths):
    with sqlite3.connect(path) as source,sqlite3.connect(backup/f'database-{i}.sqlite') as target:source.backup(target)
(backup/'database-paths.txt').write_text('\n'.join(map(str,paths)))
PY
python3 "$backend/releases/$ref/manage-accounts.py" bootstrap "$data" /etc/hxry-cloud/backend-token
chown hxry-cloud:hxry-cloud "$data/accounts.sqlite"
chmod 600 "$data/accounts.sqlite"
for name in ui backend; do
    if [[ $name == ui ]]; then directory=$root;else directory=$backend;fi
    ln -s "releases/$ref" "$directory/accounts-new-link"
    mv -Tf "$directory/accounts-new-link" "$directory/current"
done
systemctl start hxry-cloud
ready=0
for attempt in {1..30}; do
    if curl --noproxy '*' --unix-socket /run/hxry-cloud/backend.sock --config "$backup/backend-curl.conf" -fsS --max-time 2 http://localhost/health > /dev/null 2>/dev/null;then ready=1;break;fi
    sleep .5
done
[[ $ready == 1 ]] || { echo 'Accounts backend did not start';false; }
# Generate a short-lived, host-local check session; no password is sent in command arguments.
python3 "$backend/current/manage-accounts.py" probe "$data" /etc/hxry-cloud/backend-token --output "$backup/session-curl.conf"
curl --noproxy '*' --unix-socket /run/hxry-cloud/backend.sock --config "$backup/backend-curl.conf" --config "$backup/session-curl.conf" -fsS --max-time 15 http://localhost/state > "$backup/state-after.json"
python3 - "$backup" <<'PY'
import pathlib,sqlite3,json,sys
root=pathlib.Path(sys.argv[1])
with sqlite3.connect(root/'database-0.sqlite') as db:before=json.loads(db.execute('SELECT entries FROM state WHERE id=1').fetchone()[0])
with sqlite3.connect('/var/lib/hxry-cloud/state.sqlite') as db:after=json.loads(db.execute('SELECT entries FROM state WHERE id=1').fetchone()[0])
assert before==after,'Owner file metadata changed unexpectedly'
assert json.loads((root/'state-after.json').read_text())['chunkSize']==4194304
print('Original owner metadata preserved.')
PY
install -m 640 -o root -g www-data "$backup/new-snippet.conf" "$snippet"
nginx -t
systemctl reload nginx
python3 - <<'PY'
import subprocess,time
limit=time.monotonic()+25
while True:
    p=subprocess.run(['curl','--noproxy','*','--resolve','hxry.ru:443:127.0.0.1','--max-time','4','-sS','-o','/dev/null','-w','%{http_code}','https://hxry.ru/cloud/auth/'],capture_output=True,text=True,timeout=6)
    if p.returncode==0 and p.stdout=='200':break
    if time.monotonic()>=limit:raise SystemExit('Login route did not become ready')
    time.sleep(.5)
PY
code=$(curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -sS --max-time 15 -o /dev/null -w '%{http_code}' https://hxry.ru/cloud/api/state)
[[ $code == 401 ]] || { echo "Anonymous private API was not denied ($code)";false; }
curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --config "$backup/session-curl.conf" -fsS --max-time 15 https://hxry.ru/cloud/api/auth/me > "$backup/me.json"
python3 - "$backup/me.json" <<'PY'
import json,sys
with open(sys.argv[1]) as source:me=json.load(source)
assert me['accounts'] and me['user']['enabled'] and me['user']['role']=='admin'
print('Administrator session verified through HTTPS/nginx.')
PY
for endpoint in / /account; do
    curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -fsS --max-time 15 "https://hxry.ru$endpoint" > "$backup/site-${endpoint//\//_}-after"
    cmp "$backup/site-${endpoint//\//_}-before" "$backup/site-${endpoint//\//_}-after"
done
rm -f "$backup/backend-curl.conf" "$backup/session-curl.conf" "$backup/me.json"
trap - EXIT INT TERM HUP
echo 'Accounts enabled. Login: https://hxry.ru/cloud/auth/ · Administration: https://hxry.ru/cloud/admin/'
echo 'Existing files are owned by cloudadmin. Website password replaces Basic Auth for cloud routes only.'
echo 'Private metadata backup is local only; do not upload it. Independent off-host backups remain necessary.'
