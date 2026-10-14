#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID == 0 && ${1:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Run with sudo and full deployment commit SHA';exit 2; }
ref=$1
for tool in python3 curl sha256sum systemctl useradd getent;do command -v "$tool" >/dev/null || { echo "Missing $tool; on Arch install the required package before retrying.";exit 1; };done
python3 -c 'import sys;assert sys.version_info >= (3,10),"Python 3.10+ required"'
[[ -d /run/systemd/system ]] || { echo 'systemd must be running';exit 1; }
stage=$(mktemp -d /tmp/hxry-cloud-laptop-install-XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
url=https://raw.githubusercontent.com/pypain/Web-Cloud/$ref/laptop
for file in agent.py configure.py hxry-cloud-replica.service hxry-cloud-replica.timer;do curl -fSL "$url/$file" -o "$stage/$file";done
printf '%s  %s\n' '64226c835fbac6ab9f10844511b621c2254d8931a7c21a4c5bdc47ccace6f8ba' "$stage/agent.py" | sha256sum -c -
printf '%s  %s\n' '64dc14aa4693380c56c8c503418d788f232541100c5d4654a61fcdd3f8a71214' "$stage/configure.py" | sha256sum -c -
printf '%s  %s\n' 'ca2f1fc8167509457ccb1b244c70c36867072d7a52078acdfca881c602ebac2f' "$stage/hxry-cloud-replica.service" | sha256sum -c -
printf '%s  %s\n' 'ac69bf410cdcb2beb44ac4ab4109ac6795eeabdb7cad115601861198bdb953fe' "$stage/hxry-cloud-replica.timer" | sha256sum -c -
if ! getent passwd hxry-cloud-replica >/dev/null;then useradd --system --user-group --no-create-home --shell /usr/bin/nologin hxry-cloud-replica;fi
[[ $(getent passwd hxry-cloud-replica | cut -d: -f7) == /usr/bin/nologin ]] || { echo 'Unexpected existing service account';exit 1; }
# Configuration and source checks complete before stopping an existing timer.
python3 "$stage/configure.py"
systemctl stop hxry-cloud-replica.timer hxry-cloud-replica.service 2>/dev/null || true
install -d -m 755 /opt/hxry-cloud-replica/releases/"$ref"
install -m 644 "$stage/agent.py" /opt/hxry-cloud-replica/releases/"$ref"/agent.py
if [[ -L /opt/hxry-cloud-replica/new-link ]];then rm /opt/hxry-cloud-replica/new-link;fi
ln -s "releases/$ref" /opt/hxry-cloud-replica/new-link
mv -Tf /opt/hxry-cloud-replica/new-link /opt/hxry-cloud-replica/current
chown hxry-cloud-replica:hxry-cloud-replica /var/lib/hxry-cloud-replica /var/lib/hxry-cloud-replica/.replica-root
chmod 700 /var/lib/hxry-cloud-replica
chmod 600 /var/lib/hxry-cloud-replica/.replica-root
chown root:hxry-cloud-replica /etc/hxry-cloud-replica
chmod 750 /etc/hxry-cloud-replica
install -m 644 "$stage/hxry-cloud-replica.service" /etc/systemd/system/hxry-cloud-replica.service
install -m 644 "$stage/hxry-cloud-replica.timer" /etc/systemd/system/hxry-cloud-replica.timer
systemctl daemon-reload
systemctl enable --now hxry-cloud-replica.timer
systemctl start --no-block hxry-cloud-replica.service
systemctl status hxry-cloud-replica.timer --no-pager
echo 'Mirror started in background. Files: /var/lib/hxry-cloud-replica/objects/'
echo 'Status: cloud admin page; local logs: sudo journalctl -u hxry-cloud-replica -n 30 --no-pager'
echo 'This is a deletion-synchronized mirror, not retained history. Offlines retry automatically.'
