# Single-owner server storage

Python 3.10+ standard library only. Production uses a private Unix socket,
nginx HTTP Basic authentication, and a locally generated proxy token. No
public listener is added. Write requests require a custom request header;
cross-origin writes are rejected. Keep the existing hxry.ru site trusted:
pages on the same origin share browser privileges. A separate cloud origin
is recommended before expanding access to other users.

The dedicated hxry-cloud system user stores SQLite metadata and immutable
file contents under `/var/lib/hxry-cloud`, outside nginx's static root.
Uploads and downloads stream in 1 MiB chunks. Files are bounded to 5 GiB,
total storage to 20 GiB, and the host retains 10 GiB free disk reserve.
Trash counts toward the quota; permanently deleting committed entries removes
their contents. Uncommitted uploads older than 24 hours are reclaimed before
the next upload. Conflicting metadata writes return 409 rather than silently
overwriting edits made on another device. Refresh to obtain updated listings.

Public sharing is intentionally unavailable. Unknown or executable MIME types
download as attachments, with nosniff and a sandbox policy. File IDs are UUIDs;
user-supplied names never become filesystem paths. Metadata validates parent
folders, duplicate names and cycles. This is a private initial implementation,
not a multi-tenant cloud service or a completed independent security review.

Deployment: run `deploy/install-backend.sh FULL_COMMIT_SHA` as root on the
existing private hxry.ru preview. It verifies pinned artifacts, creates a
restricted systemd service and switches only the cloud snippet and frontend
symlink. It retains the current password, tests the API through nginx and
prints a rollback command. No restart of other services or reboot is needed.
Existing IndexedDB data is untouched but not automatically migrated; download
important browser-local files before upgrading, then upload them afterward.

Backup still requires configuration on an independent destination. To make a
consistent local backup, briefly stop hxry-cloud, archive `/var/lib/hxry-cloud`
and the relevant service/nginx/token configuration, then restart it. Protect
the archive as confidential. A copy on the same VPS is not disaster recovery.

Run `python3 -m unittest discover -s backend -v` for API tests. For browser
tests build with `VITE_SERVER_STORAGE=1 npm run build -- --base=/cloud/`, then
run `npx playwright test --config deploy/server-playwright.config.js`.
The test proxy token is test-only and never used by the production service.
