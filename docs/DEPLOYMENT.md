# Netlify + Render deployment (constrained preview)

This setup hosts the static browser UI on Netlify and the Flask API on one Render web service. The project was designed for a single local machine; this configuration is a constrained, single-instance preview, **not a production-ready multi-tenant media service**. Read the platform limits below before uploading valuable or private media.

The existing Windows workflow is unchanged: `start.bat` / `python start_local.py` still use the same-origin Flask server at `http://127.0.0.1:5000`. The checked-in `api-config.js` defaults to same-origin. Netlify generates its own version of this file at build time from `CUTROOM_API_BASE_URL`.

## 1. Configure the Render API

1. Create a Render Blueprint from this repository and review `render.yaml`. The Blueprint builds the existing Dockerfile, starts `server:app` under the checked-in one-process Gunicorn configuration, and mounts a persistent disk at `/var/data`.
2. Keep the Render service's public hostname aligned with `CUTROOM_ALLOWED_HOSTS` in `render.yaml`. If you add an API custom domain, append its hostname as a comma-separated value. This is an exact host allowlist, not a wildcard.
3. In Render's environment settings, set:

   | Variable | Required value / notes |
   |---|---|
   | `CUTROOM_PUBLIC_DEPLOYMENT` | `true` (explicitly opts into public access; local-only defaults otherwise remain active) |
   | `CUTROOM_ALLOWED_HOSTS` | Exact API hostnames, e.g. `cutroom-api.onrender.com,api.example.com`, without scheme or path |
   | `CUTROOM_ALLOWED_ORIGINS` | Exact browser origins, e.g. `https://app.example.com`, comma-separated; no paths or trailing slash. Include only production origins you trust. |
   | `CUTROOM_DATA` | `/var/data` (the mounted persistent disk) |
   | `CUTROOM_MODE` | `combined`; API and the existing in-process processing/export loops run in this one service process |
   | `SESSION_SECRET` | Render-generated or a strong random secret. Keep it private. |
   | `CUTROOM_SECRET_ENCRYPTION_KEY` | A valid Fernet key, stored only as a Render secret. Generate locally with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste its output into Render's secret field. Never place it in this repository or a frontend variable. |
   | `SESSION_COOKIE_SECURE` | `true` |
   | `SESSION_COOKIE_SAMESITE` | `Lax` when the frontend and API use HTTPS hostnames under the same registrable domain (recommended). For different sites, `None` requires `Secure=true` but third-party-cookie blocking can still prevent sign-in. |
   | `CUTROOM_PUBLIC_URL` | Frontend origin, e.g. `https://app.example.com`; used for account verification/reset links. |
   | `YOUTUBE_REDIRECT_URI` | Optional but recommended for YouTube OAuth with split hosts: `https://api.example.com/api/youtube/callback`. Register this exact URI in Google Cloud Console. It separates the API callback origin from `CUTROOM_PUBLIC_URL`. |

   If YouTube OAuth is used, also set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` as Render secrets. Configure Google OAuth's authorized redirect URI to exactly match `YOUTUBE_REDIRECT_URI`. Email verification/reset delivery additionally requires `CUTROOM_EMAIL_MODE=smtp` and the `SMTP_*` settings; these are optional and disabled by default.

4. Use a paid Render service plan that supports a persistent disk. The included Blueprint requests a 10 GB disk; size it for retained source files, rendered exports, thumbnails, SQLite data, and automation assets. Back up this disk and database independently before relying on them.

## 2. Configure the Netlify frontend

1. Create a Netlify site from the same repository, using the repository root as the base directory. `netlify.toml` runs `node scripts/build-netlify.js` and publishes only `netlify-dist/`.
2. Set the Netlify build environment variable `CUTROOM_API_BASE_URL` to the backend HTTPS **origin only**, e.g. `https://api.example.com` or `https://cutroom-api.onrender.com`. Do not include `/api`, a path, query, credentials, or a trailing route. Trigger a fresh deploy after changing this value.
3. Use a fixed production frontend origin (custom domain recommended), then set that exact origin in Render's `CUTROOM_ALLOWED_ORIGINS`. Add any additional trusted production origin deliberately; do not use `*` with credentialed sessions. Avoid allowing arbitrary branch/preview deploy origins access to production accounts.
4. The build staging script copies only `index.html`, the listed UI scripts/styles, and generated `api-config.js`; it does not publish Python modules, `.env`, SQLite databases, or local `data/`. The fallback serves the UI for client-side paths. This app currently uses in-page views rather than URL-based routes.

## 3. Cookie, CORS, and browser-origin requirements

The static site and API are different origins. API `fetch` and resumable upload requests therefore use `credentials: "include"`; the backend returns `Access-Control-Allow-Origin` for only configured origins and `Access-Control-Allow-Credentials: true`. Preflight requests are answered explicitly. Mutations still require the session-bound CSRF token, and a cross-origin `Origin` must be on the configured exact-origin list.

For dependable browser sessions, put both endpoints on HTTPS hostnames under the **same registrable domain**, for example `app.example.com` on Netlify and `api.example.com` on Render. They remain cross-origin (so CORS is still required) but are same-site for cookie policy; use `SameSite=Lax; Secure`. If using the default `*.netlify.app` and `*.onrender.com` hosts, the sites are cross-site. `SameSite=None; Secure` is then necessary, but browser privacy settings may block third-party cookies anyway; sign-in may fail and this setup cannot guarantee compatibility. A same-site custom-domain arrangement or a same-origin authenticated proxy is the supported recommendation.

Do not set wildcard origins, disable CSRF, expose secrets in Netlify variables, or enable public deployment without setting both host and origin allowlists. `CUTROOM_ALLOWED_HOSTS` validates the forwarded host, and Render proxy headers are honored for HTTPS URL generation only in explicit public-deployment mode.

## 4. Render constraints and incompatibilities

- **One instance only.** The SQLite database, media paths, uploads, session-related state, and current automation state are local filesystem/process state. A Render persistent disk belongs to one service instance and is not a shared multi-instance filesystem. Do not autoscale this service or run a second worker service against it.
- **Workers are not durable jobs.** Generation/export run as daemon threads inside the Gunicorn process, and the automation integration starts local subprocess/thread work. Render restarts, deploys, crashes, resource limits, or service suspension terminate active work. A persistent disk preserves completed files and SQLite state, but does not keep an active job alive. No durable distributed queue, external object storage, or separate worker system is configured.
- **Filesystem persistence is conditional.** The configured disk persists mounted `/var/data` on the single paid service. A free/ephemeral filesystem is not a substitute; files outside the mount and temporary `/tmp` files can disappear. Disk capacity is finite and no automatic backup or media lifecycle policy is configured.
- **Uploads and processing are heavy.** The UI uses resumable requests of up to 8 MiB each for large media; this is more compatible with platform request limits than the legacy one-shot upload endpoint, but it does not remove total transfer-time, request, disk, memory, CPU, or platform timeout limits. Do not assume the app's 4 GiB application limit is a guarantee that a 4 GiB upload will complete through Render. Test representative files and use a managed object-storage upload design for larger/reliable production uploads.
- **Browser automation and third-party ingestion are not guaranteed.** Playwright/Chromium and media tooling increase image size and resource use. Headless ChatGPT/browser workflows can require human verification and cannot be assumed to work unattended on Render. YouTube downloading/captions remain subject to external platform changes and policies.
- **Automation paths are server paths.** Automation workflows that ask for a source/output folder interpret it inside the Render container, not on a user's Windows machine. Only paths mounted in the service (including `/var/data`) are available, and files elsewhere are ephemeral or absent. Do not configure a user's local path as a Render target.
- **Security is not production-reviewed.** Public-host support and CORS do not constitute a security audit. Authentication, account throttles, SQLite behavior, media processing, remote URL ingestion, automation, and workspace isolation have not been reviewed for public SaaS exposure. Use only for a controlled preview with non-sensitive test data until those paths receive a formal security review and operational design.

## 5. Focused checks after deploy

- Visit the Netlify root and confirm the browser loads the API URL configured at build time.
- Check Render `/api/health`, then register/sign in and verify an authenticated API read and mutation. In browser developer tools confirm the API cookie is present and requests include credentials; do not copy cookie values into logs or tickets.
- Test a small resumable upload, a short job, a rendered preview/download, and a restart/redeploy. Expect active work to stop during restarts; verify that completed artifacts remain under the mounted disk.
- Test YouTube OAuth only after registering the exact backend callback URI. Verify the final redirect returns to the configured frontend origin.
- Do not interpret a successful health check as proof that long media jobs, large uploads, cookies on cross-site defaults, or persistent-disk capacity are production-ready.
