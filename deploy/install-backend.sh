#!/usr/bin/env bash
# Upgrade the existing private frontend to single-owner VPS storage.
set -euo pipefail
umask 077
[[ $EUID == 0 && ${1:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Run as root with the full deployment commit SHA'; exit 2; }
ref=$1
for tool in curl python3 nginx systemctl sha256sum tar useradd getent; do command -v "$tool" >/dev/null; done
root=/var/www/hxry-cloud
backend=/opt/hxry-cloud-backend
snippet=/etc/nginx/snippets/hxry-cloud.conf
unit=/etc/systemd/system/hxry-cloud.service
[[ -L $root/current && -f $snippet && ! -L $snippet && -f /etc/nginx/hxry-cloud.htpasswd ]] || { echo 'Existing private cloud deployment not found'; exit 1; }
[[ ! -e $unit && ! -L $unit && ! -e $backend && ! -L $backend ]] || { echo 'Backend already exists; refusing overwrite'; exit 1; }
previous=$(readlink "$root/current")
[[ $previous =~ ^releases/[a-f0-9]{40}$ && -d $root/$previous ]] || { echo 'Unexpected frontend link'; exit 1; }
[[ $(df --output=avail -B1 / | tail -1) -gt 12884901888 ]] || { echo 'Need at least 12 GiB free before deployment'; exit 1; }
nginx -t
backup=$(mktemp -d /root/hxry-cloud-server-backup-XXXXXXXX)
chmod 700 "$backup"
cp -p "$snippet" "$backup/snippet.conf"
printf '%s\n' "$previous" > "$backup/previous-link"
url=https://raw.githubusercontent.com/pypain/Web-Cloud/$ref
for path in deploy/server-dist.tar.gz backend/server.py deploy/hxry-cloud.service deploy/hxry-cloud-server.conf; do
  curl -fSL "$url/$path" -o "$backup/${path##*/}"
done
printf '%s  %s\n' 'c2574e687bf253b6b5f85c6ca045abc6b2aaeae8f55b3d9231b91c993570bcc0' "$backup/server-dist.tar.gz" | sha256sum -c -
printf '%s  %s\n' 'd5ef83b170e705f05ee55143c96b8213c5d64e2ddf0aca9e0c366ea0c2b65521' "$backup/server.py" | sha256sum -c -
python3 - "$backup/server-dist.tar.gz" <<'PY'
import pathlib,sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert not name.is_absolute() and '..' not in name.parts
        assert member.isfile() or member.isdir()
PY
for endpoint in / /account; do
  label=${endpoint//\//_}
  curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -sS --max-time 15 \
    -o "$backup/before$label.html" -w '%{http_code}' "https://hxry.ru$endpoint" > "$backup/before$label.status"
done
if getent passwd hxry-cloud >/dev/null; then
  python3 - <<'PY'
import pwd
p=pwd.getpwnam('hxry-cloud')
assert p.pw_dir=='/var/lib/hxry-cloud' and p.pw_shell=='/usr/sbin/nologin', 'Unexpected existing account'
PY
else
  useradd --system --user-group --home-dir /var/lib/hxry-cloud --no-create-home --shell /usr/sbin/nologin hxry-cloud
fi
[[ ! -L /etc/hxry-cloud ]] || exit 1
install -d -m 750 -o root -g hxry-cloud /etc/hxry-cloud
if [[ ! -e /etc/hxry-cloud/backend-token ]]; then
  python3 - <<'PY'
import pathlib,secrets
pathlib.Path('/etc/hxry-cloud/backend-token').write_text(secrets.token_hex(32)+'\n')
PY
fi
[[ ! -L /etc/hxry-cloud/backend-token ]] || exit 1
chown root:hxry-cloud /etc/hxry-cloud/backend-token
chmod 640 /etc/hxry-cloud/backend-token
cat > "$backup/restore.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cp -p "$dir/snippet.conf" /etc/nginx/snippets/hxry-cloud.conf
previous=$(cat "$dir/previous-link")
[[ $previous =~ ^releases/[a-f0-9]{40}$ ]] || exit 1
ln -s "$previous" /var/www/hxry-cloud/restore-link
mv -Tf /var/www/hxry-cloud/restore-link /var/www/hxry-cloud/current
nginx -t
systemctl reload nginx
systemctl disable --now hxry-cloud.service || true
rm -f /etc/systemd/system/hxry-cloud.service
systemctl daemon-reload
echo 'Private browser-only frontend restored. Server data and token retained locally.'
RESTORE
echo "Rollback: bash $backup/restore.sh"
rollback() {
  result=$?; trap - ERR
  bash "$backup/restore.sh" || echo "Use $backup/restore.sh for recovery" >&2
  if [[ -d $backend && ! -L $backend ]]; then mv "$backend" "$backup/backend-files"; fi
  exit "$result"
}
trap rollback ERR
install -d -m 755 "$backend/releases/$ref"
install -m 644 "$backup/server.py" "$backend/releases/$ref/server.py"
ln -s "releases/$ref" "$backend/current"
install -m 644 "$backup/hxry-cloud.service" "$unit"
systemctl daemon-reload
systemctl enable --now hxry-cloud.service
python3 - "$backup" <<'PY'
import pathlib,sys
root=pathlib.Path(sys.argv[1]); token=pathlib.Path('/etc/hxry-cloud/backend-token').read_text().strip()
assert len(token)==64 and all(c in '0123456789abcdef' for c in token)
(root/'curl.conf').write_text('header = "X-Cloud-Token: '+token+'"\n')
text=(root/'hxry-cloud-server.conf').read_text().replace('__BACKEND_TOKEN__',token)
(root/'generated.conf').write_text(text)
PY
healthy=0
for attempt in {1..30}; do
  if curl --noproxy '*' --unix-socket /run/hxry-cloud/backend.sock --config "$backup/curl.conf" -fsS --max-time 2 http://localhost/health > "$backup/health.json" 2>/dev/null; then healthy=1; break; fi
  sleep 0.5
done
[[ $healthy == 1 ]] || { echo 'Backend did not become ready. Inspect journalctl -u hxry-cloud locally.'; false; }
mkdir -m 755 -p "$root/releases/$ref"
tar -C "$root/releases/$ref" -xzf "$backup/server-dist.tar.gz" --no-same-owner
find "$root/releases/$ref" -type d -exec chmod 755 {} +
find "$root/releases/$ref" -type f -exec chmod 644 {} +
ln -s "releases/$ref" "$root/new-link"
mv -Tf "$root/new-link" "$root/current"
install -m 640 -o root -g www-data "$backup/generated.conf" "$snippet"
nginx -t
systemctl reload nginx
python3 - <<'PY'
import subprocess,time
deadline=time.monotonic()+25
while True:
    p=subprocess.run(['curl','--noproxy','*','--resolve','hxry.ru:443:127.0.0.1',
                      '--connect-timeout','3','--max-time','4','-sS','-D','-','-o','/dev/null','-w','%{http_code}',
                      'https://hxry.ru/cloud/api/state'],capture_output=True,text=True,timeout=6)
    if p.returncode==0 and p.stdout.strip().endswith('401') and 'x-cloud-storage: server' in p.stdout.lower(): break
    if time.monotonic()>=deadline: raise SystemExit('Unauthenticated API did not return 401')
    time.sleep(.5)
PY
for endpoint in / /account; do
  label=${endpoint//\//_}
  curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 -sS --max-time 15 \
    -o "$backup/after$label.html" -w '%{http_code}' "https://hxry.ru$endpoint" > "$backup/after$label.status"
  cmp "$backup/before$label.html" "$backup/after$label.html"
  cmp "$backup/before$label.status" "$backup/after$label.status"
done
# Password is entered locally, never stored or passed as a shell argument.
curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --user cloudadmin --max-time 15 -fsS \
  https://hxry.ru/cloud/api/state > "$backup/state.json"
python3 - "$backup/state.json" <<'PY'
import json,pathlib,sys
data=json.loads(pathlib.Path(sys.argv[1]).read_text())
assert isinstance(data['entries'],list) and isinstance(data['version'],int)
print('Authenticated server storage API is working')
PY
rm -f "$backup/curl.conf"
trap - ERR
echo 'Existing site responses preserved; anonymous API blocked; authenticated API verified.'
echo 'Refresh https://hxry.ru/cloud/. Storage: 20 GiB total; 5 GiB/file; 10 GiB host disk reserve.'
echo 'Old IndexedDB files remain in the browser and were NOT automatically imported.'
echo 'Do not upload this backup directory: generated.conf contains the backend token.'
