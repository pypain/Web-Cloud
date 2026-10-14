# Arch laptop mirror

The VPS remains the primary cloud. The laptop pulls confirmed cloud files and
restore-ready account/store metadata over HTTPS. No SSH configuration, router
port forwarding or incoming laptop access is needed. This is a mirror **without
retained deletion history**: permanently removed source entries are removed on
the laptop after a complete validated source inventory is available. Trash is
included because those files still exist on the VPS. A corrupted/unavailable
source or incomplete inventory never triggers pruning. A copied file is
confirmed only after its SHA-256 matches and data is fsynced to disk.

## Installation

1. Pause active uploads and update the existing VPS cloud with
   `deploy/install-accounts.sh FULL_SHA`. Existing website passwords and data
   remain when accounts were already enabled; otherwise the original owner's
   website account is initialized as documented in the backend README.
2. Open `/cloud/admin/`, find **Копия на ноутбуке**, click **Подключить ноут /
   заменить ключ** and copy the new key. It is shown only once. Replacing it
   disables the old key and resets confirmations until the laptop checks its
   existing copies again. The key can read all backed-up cloud files and
   account metadata, but cannot manage website accounts or modify source data.
3. On Arch, download `laptop/install-laptop.sh` from the same full commit and run
   it with sudo. Paste the key into its local hidden prompt; do not upload the
   key, `/etc/hxry-cloud-replica` or private VPS backups to chat/GitHub.

Python 3.10+, curl, systemd and shadow/useradd are needed. No extra Python
packages are installed. The existing degraded TV-input service is unrelated;
the installer manages only the replica service/timer and dedicated service
account. It checks cloud connectivity/key before stopping an old timer. Files
live under `/var/lib/hxry-cloud-replica`, owned by a dedicated nologin account,
with an identity marker to avoid touching an unexpected/missing volume.

Budget: **200,000,000,000 bytes (200 GB)** including metadata and partial files;
**20,000,000,000 bytes remain free** for Arch. The known laptop filesystem has
260 GiB available, so the chosen budget fits. Budget values are limits, not
preallocated disk space. Files/metadata are normal disk files protected by Unix
permissions, not encrypted by this agent. A token is saved privately at
`/etc/hxry-cloud-replica/config.json` (root/service-group only).

The systemd oneshot timer starts after boot and retries two minutes after the
last attempt, with up to 15 seconds jitter. A long healthy copy continues until
finished; an interrupted copy preserves `.part` data and resumes using a Range
request on the next attempt. Reads use HTTPS certificate verification; redirects
are refused to prevent forwarding the device key elsewhere. Processes use a
local flock so two cycles cannot manipulate the mirror together. Updates retain
copies, metadata and resumable partial files.

```sh
sudo systemctl status hxry-cloud-replica.timer hxry-cloud-replica.service --no-pager
sudo journalctl -u hxry-cloud-replica -n 30 --no-pager
sudo systemctl start --no-block hxry-cloud-replica.service
```

To temporarily stop copying:

```sh
sudo systemctl stop hxry-cloud-replica.timer hxry-cloud-replica.service
```

To resume:

```sh
sudo systemctl enable --now hxry-cloud-replica.timer
sudo systemctl start --no-block hxry-cloud-replica.service
```

## Status and consistency

The admin card calculates missing percentages from **bytes in current source
files** versus laptop confirmations; empty-only datasets fall back to file
counts. It also shows file counts, pending deletion confirmations, metadata
lag, last contact/completion, free space and errors. An active large-file
transfer has a separate percentage until its full checksum is verified. With
no contact for ten minutes the laptop is marked offline, while previous
confirmations remain visible. This means last known verified copies, not a
claim that an offline disk has just been checked. Local files are rehashed on
size/stat changes and at least weekly to catch local corruption.

Source inventories are generated asynchronously. The server captures consistent
SQLite copies using nonblocking store locks and releases locks before hashing
large content, so an active upload postpones preparation instead of blocking
website sessions. New source objects are hashed once; immutable unchanged files
reuse verified fingerprints. Confirmed transfers use stable owner/UUID/digest
keys even when newer metadata snapshots appear after a disconnection.

Only files tracked by the agent index are pruned. Objects absent from a fully
validated inventory, or replaced with new content at the same ID, are removed
before downloading replacements so obsolete copies do not occupy the budget.
Pruning precedes completion of all new copies: during catch-up the mirror is
incomplete, which the admin percentage/metadata status reflects. No older
complete point is retained once deletions synchronize; this cannot recover an
accidental source deletion. Untracked/manual files are never intentionally
pruned. Do not place other data or symlinks inside the managed directory.

## Layout and recovery

- `objects/OWNER_UUID/FILE_UUID`: verified content, filenames mapped by SQLite.
- `parts/`: resumable, unconfirmed downloads.
- `current`: atomic pointer to the last published metadata generation.
- `metadata/GENERATION/`: `accounts.sqlite`, `inventory.sqlite`, and
  `stores/OWNER_UUID.sqlite`.
- `index.sqlite`: local confirmations/fingerprints; not primary website metadata.
- `work/`: temporary/current compressed metadata archive; no historical content.

Restore only a fully verified generation, using the inventory and file digests.
Copy `accounts.sqlite` to a fresh VPS data root. For each user in its `users`
table, `legacy=1` maps its captured state DB and files to the original data root;
all other IDs map to `users/UUID/`. Restore its store DB as `state.sqlite` and
content to `files/`. All files must be owned by the VPS service user. Restore
into an offline/isolated directory before switching a live server. The normal
VPS backend token/nginx/TLS configuration is recreated separately. Old website
sessions, login throttles and unfinished uploads are deliberately excluded
from recovery metadata; users must sign in again and unfinished uploads restart.
Share routing is rebuilt in the metadata snapshot, preserving valid public links.
The laptop connection key must be reissued on a rebuilt VPS. Tests restore the
actual files, multiple user stores and original public links from the mirror.

Validation:

```sh
python3 -m unittest discover -s backend -p test_replica.py -v
systemd-analyze verify laptop/hxry-cloud-replica.service laptop/hxry-cloud-replica.timer
```
