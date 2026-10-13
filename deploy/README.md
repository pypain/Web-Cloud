# Private hxry.ru preview

The archive is built from this repository with `npm ci` and
`npm run build -- --base=/cloud/`. SHA-256 is pinned in install-hxry.sh.
The browser still stores files locally: this is not a VPS storage backend.

The installer expects Ubuntu nginx, the supplied hxry.ru configuration,
and `htpasswd` from `apache2-utils`. Run as root with the full deployment commit
SHA as argument. Enter a new, unique password for `cloudadmin` locally.
The final authenticated curl prompts for the same password again.

Only the existing hxry.ru TLS server receives a snippet include; no ports,
DNS records, existing API routes, or other services are changed. Both `/cloud`
and `/cloud/`, including assets, require HTTP Basic authentication over HTTPS.
The nginx config is tested before reload. An automatic rollback is attempted
on failures, and a local restore command is printed for later use.

Keep the backup directory private. The restore script restores the entire
saved hxry.ru config and can overwrite later edits; review before using it later.
On success, verify `/`, `/account`, and the APIs you normally use, as well as
the cloud prompt and file operations in your browser. HTTP checks alone do not
verify application behavior. Assets remain browser-local and share the hxry.ru
origin with existing pages; use a separate origin for a future production cloud.

Validation: `npm test`; `npm run build -- --base=/cloud/`;
`npx playwright test --config deploy/playwright.config.js`.
