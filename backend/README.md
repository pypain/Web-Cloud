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
location. The landing page contains only the selected file, its metadata and preview.
Public recipient pages do not include analytics or other external resources.

Revocation blocks new requests; an already-open download can finish. Moving a
file or its ancestor into trash deletes its links, so restoring the file does
not reactivate old access. Missing, expired and revoked links return a generic
404 page. The upgrade script preserves the private snippet and adds only the
public prefix plus a private API isolation header, with nginx test and rollback.

The public landing page includes download/copy actions and an optional standalone
preview action. Raster images and supported audio/video stream through the token's
`/preview` endpoint with range support. PDFs use the browser's native viewer and
include an open/download fallback. Browser codec/viewer support determines whether
these formats can render. Text and known text extensions render escaped plain text,
bounded to the first 200 KiB. HTML, SVG and other executable formats are not served
as active previews. Revocation/expiry is rechecked for every preview request.

Only a nonce-authorized, fixed script handles copying and media error messages on
the landing page. CSP permits same-origin media/frame resources, denies connections
and external resources, and forbids embedding the landing page elsewhere. File
responses retain their sandbox and nosniff restrictions. Mobile actions precede
the preview and the layout honors reduced-motion preferences.

## Source previews and browser Python

Python and other common source/config extensions are displayed as escaped text,
including files stored with application/octet-stream. Their contents are never
served as executable HTML/JavaScript. Source previews are bounded to 200 KiB.

In the private interface, .py/.pyw files up to 100 KB can be edited temporarily
and run with the pinned Pyodide 0.27.7 runtime on the viewer's device. The editor
does not overwrite the uploaded file. Stdin is supplied in a separate field;
stdout/errors are capped and displayed as text. Runtime startup is bounded to
90 seconds and execution to 10 seconds, subject to browser scheduling. Stop or
closing the preview removes the execution iframe and worker.

Execution uses an opaque-origin sandbox iframe (allow-scripts, no
allow-same-origin) and a dedicated Worker. Worker-inherited CSP permits only the
self-hosted Python runtime asset path for script/data loads, blocking cloud API
requests. No code is executed on the VPS. The public runtime asset prefix serves
only immutable npm artifacts with CORS for the opaque-origin context; private
APIs and uploaded files remain protected. No external CDN is required.

Standard-library scripts are supported; third-party Python packages, server
filesystem access and arbitrary networking are not. There is no hard browser
memory quota, so large allocations can still exhaust resources on the viewer's
device. Python execution is not offered on public sharing pages.

## Website accounts (closed registration)

The account migration/update entry point is `deploy/install-accounts.sh FULL_SHA`.
The installer creates the first `cloudadmin` website password interactively on
the host (12–256 characters), backs up SQLite metadata with the backend stopped,
checks original owner metadata, verifies nginx/HTTPS session access and denies
anonymous storage access. It prints a local rollback script. Basic Auth files
remain on the host for rollback; SSH and other sites/services are unchanged.
Interrupted or failed migration restores previous code/configuration without
deleting original files or new account data. Inactive releases from an earlier
failed attempt are moved into its private backup before a retry. After accounts
are enabled, use this installer for upgrades rather than `upgrade-uploads.sh`.

Routes: `/cloud/auth/` for login, `/cloud/admin/` for account management,
`/cloud/auth/?password=1` for changing one's own password. There is no public
registration or email recovery. Admins can create accounts, adjust 1–100 GiB
quotas, reset passwords, change roles and disable/enable accounts. Files remain
when an account is disabled; its sessions and public links are revoked. Resetting
passwords or changing roles revokes all that account's sessions. Self-demotion
and self-blocking are denied; at least one active administrator must remain.
Audit records cover logins and management actions, never passwords/session tokens.

Each account has a server-generated UUID and its own Store under
`/var/lib/hxry-cloud/users/UUID/`. The original owner's Store stays in place at
`/var/lib/hxry-cloud` with existing files, profile and share URLs retained. All
private endpoints (files, state, uploads, shares, profile) select the Store only
from a validated session, never from client-supplied owner IDs. Admins manage
account metadata and quota usage; the admin API does not open other users' files.
Public links use a central token-to-owner index and the owner's visibility checks.
The UI's resumable queues are scoped by account ID; the original owner imports
its previous queue once. Shared-browser localStorage contains queue metadata,
so browser profiles should be separate when local metadata privacy is required.

Passwords use scrypt (N=16384,r=8,p=5, random 16-byte salt). Opaque random sessions
are stored as SHA-256 digests, with a separate CSRF token. Cookies are Secure,
HttpOnly, SameSite=Strict, scoped to `/cloud/`. Sessions expire after 12 hours or
30 minutes of inactivity; activity refresh is persisted at most once a minute.
Writes require an exact Origin, a custom request header and session CSRF token;
login also requires exact Origin/custom header. Login attempts are reserved in
SQLite before hashing (10 per login, 60 per source IP per 15 minutes). nginx
sets the client identity from the existing trusted PROXY-protocol peer. Only
local Unix-socket callers with the secret nginx token can reach the backend.
Hash concurrency is bounded to two and 32 MiB upload buffers to two across users.
Session count is limited to ten per account, users to 100 and audit retention to
5,000 records. Quotas include unfinished uploads and trash; summed quotas do not
reserve physical disk space. The existing host disk reserve still applies.

Runtime activation uses the presence of `accounts.sqlite`; do not create an
empty accounts DB on a running legacy deployment manually. For recovery from the
host console as root (does not need a browser session):

```sh
python3 /opt/hxry-cloud-backend/current/manage-accounts.py reset-admin \
  /var/lib/hxry-cloud /etc/hxry-cloud/backend-token
```

This restores the original owner to an enabled administrator, prompts locally
for a new password and revokes its sessions. Metadata rollback backups are local
and are not a substitute for encrypted off-host backups of the complete data
root. MFA and email-based recovery are not provided in this version. The cloud
shares an origin with the rest of hxry.ru; same-origin applications must also be
trusted. A dedicated subdomain is the next step for browser-origin isolation.

Validation:

```sh
python3 -m unittest discover -s backend -v
VITE_SERVER_STORAGE=1 npm run build -- --base=/cloud/
npx playwright test --config deploy/accounts-playwright.config.js --workers=1
python3 deploy/test-accounts-nginx.py --nginx /usr/sbin/nginx
```

The nginx test uses an ephemeral locally trusted TLS certificate, PROXY protocol
and a Unix socket without modifying host configuration. Browser tests verify
owner preservation, independent users, admin access, reset/block, session
revocation, resumed uploads and mobile layouts. Legacy storage tests remain
available to validate the code prior to migration.
