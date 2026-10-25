# Deployment preparation summary

## Artifacts and implementation

- Added a Netlify static build/staging configuration that publishes only the frontend allowlist and injects `CUTROOM_API_BASE_URL`; root Python sources, `.env`, databases, and media are outside the publish directory.
- Added `api-config.js` and updated the frontend transport path for cross-origin API fetches, CSRF requests, credentialed resumable XHR uploads, media previews, thumbnails, downloads, and YouTube OAuth navigation. The empty default keeps local same-origin behavior.
- Added explicit, opt-in Flask public-deployment controls: exact host and HTTPS-origin allowlists, credentialed CORS and OPTIONS handling, CSRF Origin integration, configurable SameSite/Secure cookies, and one-hop Render proxy support. Public mode refuses to start without required session/encryption secrets and allowlists.
- Added a Render Docker Blueprint, one-worker Gunicorn configuration/dependency, and corrected Docker packaging to include the existing automation blueprint module. The Blueprint mounts the local data path on a single service disk.
- Added `docs/DEPLOYMENT.md` and README/API documentation covering required environment variables, OAuth callback setup, custom-domain cookie guidance, static routing, and known Render limitations.

## Validation and limitations

- Static inspection confirmed the Netlify script allowlists the same browser script/style set referenced by `index.html` and generates the API config; frontend fetches are routed through the shared transport, while XHR, media, downloads, and OAuth navigation have explicit handling.
- Static inspection confirmed local defaults remain same-origin and loopback-only, while public mode requires exact allowlists and secure sessions.
- Runtime validation could not be performed: the available PowerShell command runner cannot start because neither `pwsh.exe` nor `powershell.exe` is installed. Therefore the Node staging build, Python compile/tests, Docker build, Render behavior, Netlify deployment, and browser cookie behavior remain unverified.
- Version-control setup/status/commit could not be performed because the required appmod version-control integration was not callable in this tool session. No direct Git operation was used; branch and dirty-worktree status remain unknown.
- The Render deployment is intentionally documented as a constrained preview only. Persistent storage is conditional on a paid single-instance disk; active daemon-thread/subprocess jobs can be terminated by restarts; no durable queue, shared multi-instance filesystem, or guaranteed large uploads are provided. Third-party cookie blocking can prevent sign-in when default Netlify/Render domains are cross-site.
