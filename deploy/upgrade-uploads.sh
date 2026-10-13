#!/usr/bin/env bash
# Upgrade only private cloud code; retain existing files, metadata and credentials.
set -euo pipefail
umask 077
[[ $EUID == 0 && ${1:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Run as root with full deployment commit SHA'; exit 2; }
ref=$1
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
curl -fSL "$url/backend/server.py" -o "$backup/server.py"
curl -fSL "$url/deploy/server-dist.tar.gz" -o "$backup/server-dist.tar.gz"
printf '%s  %s\n' '8e6161441e24cb27d75272ea64478e95d7c92a58a1664432967c1a5357e34a83' "$backup/server.py" | sha256sum -c -
printf '%s  %s\n' 'e340f49c4a142abefd13d88d4e76ce9e464ba79c570c3147ab89b68d204b2029' "$backup/server-dist.tar.gz" | sha256sum -c -
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
systemctl restart hxry-cloud
echo 'Previous cloud code restored. File data and SQLite metadata retained.'
RESTORE
echo "Rollback: bash $backup/restore.sh"
rollback(){ result=$?; trap - ERR; bash "$backup/restore.sh" || echo "Use $backup/restore.sh" >&2; exit "$result"; }
trap rollback ERR
install -d -m 755 "$root/releases/$ref" "$backend/releases/$ref"
install -m 644 "$backup/server.py" "$backend/releases/$ref/server.py"
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
