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

## Resumable upload queue

The server reserves the full file size against the quota when a session starts.
New uploads start with 8 MiB chunks and adapt within 4–32 MiB using measured
request duration, targeting about two seconds per request. SHA-256 for the next
chunk is prepared while the current one is transferring. Network writes stay
ordered: concurrent out-of-order requests are unnecessary for this protocol.
Existing 4 MiB sessions retain their original protocol and remain resumable.
Each chunk is verified against a client SHA-256 digest.
Only flushed chunks whose offsets were committed to SQLite count as confirmed.
Retries first query that offset; duplicate or out-of-order writes are rejected.
The browser checks already confirmed chunk hashes against the reselected source
before continuing. The final file appears in listings only after length and
stored chunk integrity checks, atomic publication, and metadata commit.

The frontend displays per-file and total progress, transfer speed, an approximate
remaining time, and pause/resume/cancel controls. Three automatic retries handle
short network errors; reconnecting after offline state resumes available sources.
Closing the page stops transfer. Queue metadata is persisted in localStorage;
files themselves are not copied there. Reselect the source files after reopening.
Clearing browser data loses that saved queue. The beforeunload warning is subject
to browser restrictions and is not a guarantee that closing can be prevented.

Unfinished sessions expire after seven days without a confirmed chunk. They are
cleaned on API access and hourly by the production backend. Cancelling an upload
releases its reservation and removes its parts; cancellation tombstones prevent
late create requests from resurrecting it. Cancelling after publication does not
delete a completed file: use the normal trash workflow instead.

Upgrade an existing deployment with `deploy/upgrade-uploads.sh FULL_COMMIT_SHA`.
It snapshots SQLite metadata while the service is stopped, retains previous code
links for rollback, and does not delete existing file data or change credentials.
If the backend is absent, it uses the guarded first-install path. Finish existing
legacy uploads before upgrading; the older whole-file protocol cannot resume a
transfer that started before this version.
