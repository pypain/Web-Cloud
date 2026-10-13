#!/usr/bin/env bash
# First deployment only. Touches only hxry.ru, its new cloud snippet and static files.
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Supply the full Git commit SHA'; exit 2; }
ref=$1
for tool in curl python3 nginx htpasswd sha256sum tar; do command -v "$tool" >/dev/null || { echo "Missing $tool (htpasswd is in apache2-utils)"; exit 1; }; done
site=$(readlink -f /etc/nginx/sites-enabled/hxry.ru)
case "$site" in /etc/nginx/sites-available/*|/etc/nginx/sites-enabled/*) ;; *) echo 'Unexpected active site path'; exit 1;; esac
echo "Active site file: $site"
snippet=/etc/nginx/snippets/hxry-cloud.conf
root=/var/www/hxry-cloud
auth=/etc/nginx/hxry-cloud.htpasswd
[[ -f $site && ! -L $site ]] || { echo 'Unexpected site file'; exit 1; }
[[ ! -e $snippet && ! -L $snippet && ! -e $root && ! -L $root ]] || { echo 'Cloud deployment paths already exist; refusing overwrite'; exit 1; }
[[ ! -L $auth ]] || exit 1
nginx -t
python3 - "$site" <<'PY'
import pathlib,sys
s=pathlib.Path(sys.argv[1]).read_text()
assert s.count('    root /opt/vpnbot-rw;') == 1
assert 'listen 10444 ssl proxy_protocol;' in s and 'server_name hxry.ru;' in s
assert '/cloud' not in s, 'Existing cloud route; refusing conflict'
PY
backup=$(mktemp -d /root/hxry-cloud-backup-XXXXXXXX)
chmod 700 "$backup"
cp -p "$site" "$backup/hxry.ru"
printf '%s\n' "$site" > "$backup/site-path"
for endpoint in / /account; do
  label=${endpoint//\//_}
  curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --connect-timeout 5 --max-time 15 -sS \
    -o "$backup/before$label.html" -w '%{http_code}' "https://hxry.ru$endpoint" > "$backup/before$label.status"
done
url=https://raw.githubusercontent.com/pypain/Web-Cloud/$ref/deploy
curl -fSL "$url/cloud-dist.tar.gz" -o "$backup/cloud-dist.tar.gz"
printf '%s  %s\n' '1f56c1fdea4109158ea7fa0d79bf1174c10bd13717a54c0858c3eb94b9471f55' "$backup/cloud-dist.tar.gz" | sha256sum -c -
curl -fSL "$url/hxry-cloud.conf" -o "$backup/hxry-cloud.conf"
python3 - "$backup/cloud-dist.tar.gz" <<'PY'
import pathlib,sys,tarfile
with tarfile.open(sys.argv[1]) as t:
    for m in t.getmembers():
        p=pathlib.PurePosixPath(m.name)
        assert not p.is_absolute() and '..' not in p.parts
        assert m.isfile() or m.isdir(), 'Unexpected archive member'
PY
# Password is entered only in the terminal, never in a command argument.
if [[ ! -e $auth ]]; then
  htpasswd -cB "$auth" cloudadmin
else
  grep -q '^cloudadmin:' "$auth" || { echo 'Existing password file has no cloudadmin user'; exit 1; }
fi
chown root:www-data "$auth"
chmod 640 "$auth"
cat > "$backup/restore.sh" <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
site=$(cat "$dir/site-path")
case "$site" in /etc/nginx/sites-available/*|/etc/nginx/sites-enabled/*) ;; *) exit 1;; esac
cp -p "$dir/hxry.ru" "$site"
nginx -t
systemctl reload nginx
rm -f /etc/nginx/snippets/hxry-cloud.conf
echo 'Original hxry.ru configuration restored; static release and password file retained locally.'
RESTORE
echo "Rollback: bash $backup/restore.sh"
rollback() {
  result=$?; trap - ERR
  bash "$backup/restore.sh" || echo "Use $backup/restore.sh for recovery" >&2
  if [[ -d $root && ! -L $root ]]; then mv -- "$root" "$backup/static-files"; fi
  exit "$result"
}
trap rollback ERR
mkdir -p "$root/releases/$ref"
tar -C "$root/releases/$ref" -xzf "$backup/cloud-dist.tar.gz" --no-same-owner
find "$root" -type d -exec chmod 755 {} +
find "$root" -type f -exec chmod 644 {} +
ln -s "releases/$ref" "$root/current"
install -m 644 "$backup/hxry-cloud.conf" "$snippet"
python3 - "$site" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1]); s=p.read_text()
anchor='    root /opt/vpnbot-rw;'
assert s.count(anchor)==1
p.write_text(s.replace(anchor,'    include /etc/nginx/snippets/hxry-cloud.conf;\n\n'+anchor))
PY
nginx -t
nginx -T 2>/dev/null | python3 -c '
import sys
s=sys.stdin.read()
assert "# configuration file /etc/nginx/snippets/hxry-cloud.conf:" in s, "Cloud snippet is not included in the active nginx configuration"
print("Cloud snippet found in active nginx configuration")
'
systemctl reload nginx
# A successful reload request does not mean new workers already handle requests.
python3 - <<'PY'
import subprocess,time
deadline=time.monotonic()+25
last='no response'
while True:
    p=subprocess.run(['curl','--noproxy','*','--resolve','hxry.ru:443:127.0.0.1',
                      '--connect-timeout','3','--max-time','4','-sS','-o','/dev/null',
                      '-w','%{http_code}','https://hxry.ru/cloud/'],
                     capture_output=True,text=True,timeout=6)
    last=p.stdout.strip()
    if p.returncode==0 and last=='401':
        print('New nginx route is active; unauthenticated request returns 401')
        break
    if time.monotonic()>=deadline:
        raise SystemExit(f'Cloud route did not become protected: last HTTP {last}, curl exit {p.returncode}')
    time.sleep(0.5)
PY
for endpoint in / /account; do
  label=${endpoint//\//_}
  curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --connect-timeout 5 --max-time 15 -sS \
    -o "$backup/after$label.html" -w '%{http_code}' "https://hxry.ru$endpoint" > "$backup/after$label.status"
  cmp "$backup/before$label.status" "$backup/after$label.status"
  cmp "$backup/before$label.html" "$backup/after$label.html"
done
echo 'Existing / and /account responses preserved.'
# Verify through the existing TLS listener, preserving its SNI/proxy-protocol routing.
code=$(curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://hxry.ru/cloud/)
[[ $code == 401 ]] || { echo "Expected unauthenticated 401, got $code"; false; }
echo 'Unauthenticated cloud request is blocked (401).'
code=$(curl --noproxy '*' --resolve hxry.ru:443:127.0.0.1 --connect-timeout 5 --max-time 15 --user cloudadmin -sS -o "$backup/page.html" -w '%{http_code}' https://hxry.ru/cloud/)
[[ $code == 200 ]] || { echo "Authenticated request returned $code"; false; }
grep -q '/cloud/assets/' "$backup/page.html"
trap - ERR
echo 'Authenticated page returned 200. Open https://hxry.ru/cloud/ with user cloudadmin.'
echo 'Frontend preview only: uploaded files remain in this browser, not on the VPS.'
