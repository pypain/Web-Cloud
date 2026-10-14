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

Public sharing is scoped to individual files with expiring capability links. Unknown or executable MIME types
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

## Profile and appearance

The private `/profile` API stores display name, description, one of six accent
colors, a motion preference and an optional PNG avatar in SQLite. Optimistic
version checks reject concurrent profile overwrites. The browser crops raster
photos to 160×160 pixels; the backend bounds encoded size and PNG dimensions.
The profile is shared by this single-owner storage, not a separate login or
multi-user account. Existing HTTP Basic credentials remain unchanged.

Appearance settings are applied on page load across devices. System
`prefers-reduced-motion` overrides the profile's animation preference. Navigation
animations are limited to view changes, avoiding replay while typing a search.

## Public file links

Owners create links from the private `/shares` API with 1-hour, 1-day, 7-day
or 30-day expiry. Tokens use 32 random bytes and are stored in private SQLite
so owners can copy links again on another device. Treat metadata backups as
confidential. Anyone holding a link can download its selected file until it
expires or is revoked; possession is the authorization, not a separate account.

Only `/cloud/share/` bypasses nginx Basic authentication. It forwards a public
isolation marker; the backend rejects public requests to every other API path
and rejects mutations. Public content downloads as an attachment, with nosniff,
sandbox, no-store and no-referrer headers. Access logging is disabled for this
location. The landing page contains only the shared filename, size and expiry.
Public recipient pages do not include analytics or other external resources.

Revocation blocks new requests; an already-open download can finish. Moving a
file or its ancestor into trash deletes its links, so restoring the file does
not reactivate old access. Missing, expired and revoked links return a generic
404 page. The upgrade script preserves the private snippet and adds only the
public prefix plus a private API isolation header, with nginx test and rollback.
