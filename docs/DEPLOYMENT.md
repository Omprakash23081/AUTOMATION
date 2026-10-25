# Netlify + Render deployment (constrained preview)

This setup hosts the static browser UI on Netlify and the Flask API on one Render web service. The browser calls the API through a Netlify `/api/*` proxy rewrite so authentication cookies remain first-party; Netlify forwards those requests to Render. The project was designed for a single local machine; this configuration is a constrained, single-instance preview, **not a production-ready multi-tenant media service**. Read the platform limits below before uploading valuable or private media.

The existing Windows workflow is unchanged: `start.bat` / `python start_local.py` still use the same-origin Flask server at `http://127.0.0.1:5000`. The checked-in `frontend/api-config.js` defaults to same-origin. Netlify generates an `_redirects` file at build time from `CUTROOM_API_BASE_URL`; this value is used only as the proxy destination and is not embedded in browser JavaScript.

Deploy in this order: create the Netlify site and note its hostname; configure and deploy the Render service with that exact hostname; then set Netlify's `CUTROOM_API_BASE_URL` and deploy the frontend. The initial Netlify build may fail until the Render API origin is configured.

## 1. Configure the Render API

1. Create a Render Blueprint from this repository and review `render.yaml`. The Blueprint builds the existing Dockerfile, starts the root `server:app` compatibility entry point under the checked-in one-process Gunicorn configuration, and mounts a persistent disk at `/var/data`.
2. After creating the Netlify site, set Render's `CUTROOM_ALLOWED_HOSTS` to the exact Netlify site hostname, for example `cutroom7.netlify.app`. The backend automatically adds Render's documented `RENDER_EXTERNAL_HOSTNAME` to the allowlist. Add any custom hostnames actually used, without schemes or paths; do not use wildcards.
3. In Render's environment settings, set:

   | Variable | Required value / notes |
   |---|---|
   | `CUTROOM_PUBLIC_DEPLOYMENT` | `true` (explicitly opts into public access; local-only defaults otherwise remain active) |
   | `CUTROOM_ALLOWED_HOSTS` | Exact Netlify and any custom hostnames, e.g. `cutroom7.netlify.app`; Render's own hostname is added automatically |
   | `DATABASE_URL` | Managed PostgreSQL connection URL; required for local-agent metadata endpoints. Keep it only in Render secrets. |
   | `CUTROOM_AGENT_TESTER_EMAILS` | Comma-separated exact email addresses for the 3–5 private testers allowed to pair agents and submit local-agent jobs. Required to approve agents in public mode. |
   | `CUTROOM_ALLOWED_ORIGINS` | Exact Netlify browser origin, e.g. `https://my-cutroom.netlify.app`, comma-separated; no paths or trailing slash. Include only production origins you trust. |
   | `CUTROOM_DATA` | `/var/data` (the mounted persistent disk) |
   | `CUTROOM_MODE` | `combined`; API and the existing in-process processing/export loops run in this one service process |
   | `SESSION_SECRET` | Render-generated or a strong random secret. Keep it private. |
   | `CUTROOM_SECRET_ENCRYPTION_KEY` | A valid Fernet key, stored only as a Render secret. Generate locally with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste its output into Render's secret field. Never place it in this repository or a frontend variable. |
   | `SESSION_COOKIE_SECURE` | `true` |
   | `SESSION_COOKIE_SAMESITE` | `Lax` with `SESSION_COOKIE_SECURE=true`; browser API requests are same-origin through the Netlify proxy. |
   | `CUTROOM_PUBLIC_URL` | Netlify frontend origin, e.g. `https://my-cutroom.netlify.app`; used for account verification/reset links. |
   | `YOUTUBE_REDIRECT_URI` | Set to `https://my-cutroom.netlify.app/api/youtube/callback`. Register this exact proxied callback URI in Google Cloud Console. |

   If YouTube OAuth is used, also set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` as Render secrets. Configure Google OAuth's authorized redirect URI to exactly match `YOUTUBE_REDIRECT_URI`. Email verification/reset delivery additionally requires `CUTROOM_EMAIL_MODE=smtp` and the `SMTP_*` settings; these are optional and disabled by default.

4. Use a paid Render service plan that supports a persistent disk. The included Blueprint requests a 10 GB disk; size it for retained source files, rendered exports, thumbnails, SQLite data, and automation assets. Back up this disk and database independently before relying on them.

## 2. Configure the Netlify frontend

1. Use the Netlify site created earlier, connected to this repository with the repository root as the base directory. `netlify.toml` runs `node scripts/build-netlify.js` and publishes only `netlify-dist/`.
2. Set the Netlify build environment variable `CUTROOM_API_BASE_URL` to the Render backend HTTPS **origin only**, e.g. `https://cutroom-api.onrender.com`. Do not include `/api`, a path, query, credentials, or a trailing route. Trigger a fresh deploy after changing this value. The build writes a Netlify proxy rule for `/api/*` and a final single-page-app fallback; the API origin is not exposed in the browser config.
3. Use the exact production Netlify site origin in Render's `CUTROOM_ALLOWED_ORIGINS`, and the corresponding Netlify hostname in `CUTROOM_ALLOWED_HOSTS`. Add any custom origin/host deliberately; do not use `*` or allow arbitrary branch/preview deploy origins.
4. The build staging script copies only the allowlisted files from `frontend/`, the same-origin API config, and generated proxy rules; it does not publish Python modules, `.env`, SQLite databases, or local `data/`.

## 3. Netlify proxy, cookies, and browser-origin requirements

The browser sends `/api/*` requests to the Netlify site; Netlify rewrites them to the Render API. This makes API requests and session cookies same-origin from the browser's perspective. The backend still uses an exact origin/host allowlist and session-bound CSRF checks. Do not disable these protections or use wildcard allowlists.

The API sends `Cache-Control: no-store` in public mode so authenticated API responses and media are not stored by the Netlify proxy/CDN.

The YouTube OAuth callback also goes through the Netlify proxy so it uses the same session cookie as sign-in. Set `YOUTUBE_REDIRECT_URI` to the Netlify origin plus `/api/youtube/callback` and register that exact URL with Google. The `/api/*` proxy has Netlify's documented 26-second response timeout; the app's job creation APIs are asynchronous, but individual API requests and transfers should complete within that limit.

Do not set wildcard origins, disable CSRF, expose secrets in Netlify variables, or enable public deployment without setting both host and origin allowlists. `CUTROOM_ALLOWED_HOSTS` validates the forwarded host, and Render proxy headers are honored for HTTPS URL generation only in explicit public-deployment mode.

## 4. Render constraints and incompatibilities

- **One instance only.** The SQLite database, media paths, uploads, session-related state, and current automation state are local filesystem/process state. A Render persistent disk belongs to one service instance and is not a shared multi-instance filesystem. Do not autoscale this service or run a second worker service against it.
- **Workers are not durable jobs.** Generation/export run as daemon threads inside the Gunicorn process, and the automation integration starts local subprocess/thread work. Render restarts, deploys, crashes, resource limits, or service suspension terminate active work. A persistent disk preserves completed files and SQLite state, but does not keep an active job alive. No durable distributed queue, external object storage, or separate worker system is configured.
- **Filesystem persistence is conditional.** The configured disk persists mounted `/var/data` on the single paid service. A free/ephemeral filesystem is not a substitute; files outside the mount and temporary `/tmp` files can disappear. Disk capacity is finite and no automatic backup or media lifecycle policy is configured.
- **Uploads and processing are heavy.** The UI uses resumable requests of up to 8 MiB each for large media; this is more compatible with platform request limits than the legacy one-shot upload endpoint, but it does not remove total transfer-time, request, disk, memory, CPU, or platform timeout limits. Netlify proxy requests must complete within its 26-second response timeout. Do not assume the app's 4 GiB application limit is a guarantee that a 4 GiB upload will complete through Netlify and Render. Test representative files and use a managed object-storage upload design for larger/reliable production uploads.
- **Browser automation and third-party ingestion are not guaranteed.** Playwright/Chromium and media tooling increase image size and resource use. Headless ChatGPT/browser workflows can require human verification and cannot be assumed to work unattended on Render. YouTube downloading/captions remain subject to external platform changes and policies.
- **Automation paths are server paths.** Automation workflows that ask for a source/output folder interpret it inside the Render container, not on a user's Windows machine. Only paths mounted in the service (including `/var/data`) are available, and files elsewhere are ephemeral or absent. Do not configure a user's local path as a Render target.
- **Security is not production-reviewed.** Public-host support and CORS do not constitute a security audit. Authentication, account throttles, SQLite behavior, media processing, remote URL ingestion, automation, and workspace isolation have not been reviewed for public SaaS exposure. Use only for a controlled preview with non-sensitive test data until those paths receive a formal security review and operational design.

## 5. Focused checks after deploy

- Visit the Netlify root and confirm `/api/health` is served through the Render proxy.
- Register/sign in and verify an authenticated API read and mutation. In browser developer tools confirm the session cookie is present and API requests include it; do not copy cookie values into logs or tickets.
- Test a small resumable upload, a short job, a rendered preview/download, and a restart/redeploy. Expect active work to stop during restarts; verify that completed artifacts remain under the mounted disk.
- Test YouTube OAuth only after registering the exact Netlify proxy callback URI. Verify the final redirect returns to the configured frontend origin.
- Do not interpret a successful health check as proof that long media jobs, large uploads, Netlify proxy timeouts, or persistent-disk capacity are production-ready.

## 6. Local-agent migration status

The local-agent migration is **in progress, not complete**. The backend has PostgreSQL-backed storage for agent pairing records and local-agent job metadata when `DATABASE_URL` is configured; local development and tests use a separate SQLite metadata file. The PostgreSQL implementation has not been verified against a live PostgreSQL instance. Existing account authentication, workspaces, OAuth connections, and provider secrets remain in SQLite.

### Private Windows tester setup

1. For a public Render service, keep the persistent disk and configure `DATABASE_URL` to the managed PostgreSQL URL and `CUTROOM_AGENT_TESTER_EMAILS` to the exact tester addresses. The tester allowlist is checked on authenticated dashboard endpoints and agent API requests. Do not put these values in Netlify or browser code.
2. From the repository root on each authorized Windows computer, install Python 3 with the Windows `py` launcher. Install the local-agent media dependencies, including the current yt-dlp release:

   ```powershell
   py -3 -m pip install imageio-ffmpeg python-dotenv "yt-dlp[default]>=2026.10.21"
   ```

   Playwright browsers and AI credentials are not needed for local YouTube downloading or the basic local render. Then pair the computer:

   ```powershell
   .\start_local_agent.bat pair --api-url https://cutroom7.netlify.app --name "Om's Windows PC"
   ```

   Use the Netlify site origin so agent traffic follows the existing same-origin `/api/*` proxy to Render. The agent prints a 16-character, five-minute pairing code. In Cutroom, sign in with the approved tester account, open **Account settings**, enter the code under **Windows local agent**, and select **Pair computer**. The account API call uses the existing session cookie and CSRF protection.
3. After the CLI confirms pairing, start the agent with:

   ```powershell
   .\start_local_agent.bat run --api-url https://cutroom7.netlify.app --browser edge
   ```

   Choose `chrome`, `edge`, or `firefox` to let yt-dlp read that browser's local session when YouTube requires sign-in. This setting is local to the agent and is not sent to Render. Omit `--browser` to download without using browser cookies. The bearer token is stored only in `%LOCALAPPDATA%\Cutroom\agent-token.dpapi`, protected with Windows DPAPI for the current Windows user. The agent makes outbound HTTPS requests only; it does not expose a local HTTP listener. Restarting the command reuses the protected credential.
4. In **Account settings**, queue **Harmless agent test**. The agent claims the job with its bearer token, runs a fixed local self-test (no download, browser, file access, or shell command), and reports `Succeeded`. The account settings job list displays the state. Revoke a computer from the same panel if needed.
5. Stop the agent with Ctrl+C. If it reports that credentials were rejected or revoked, the stored token is deleted; pair that computer again. Temporary network failures are retried. A running job is heartbeat-leased; if the agent stops unexpectedly, the job becomes retryable after the 90-second lease expires and counts against its three-attempt limit.

## Local video-to-narration sync

Use two PowerShell windows from the repository root. Keep the paired agent running in the first window:

```powershell
.\start_local_agent.bat run --api-url https://cutroom7.netlify.app
```

In the second window, open the Windows file pickers and queue one local render:

```powershell
.\start_local_agent.bat process-video --api-url https://cutroom7.netlify.app
```

Select a source video, narration audio, and an existing local output folder. The selected media and export stay on that Windows computer. The agent keeps paths in its local `%LOCALAPPDATA%\Cutroom\agent-files.sqlite3`; the backend receives only opaque references bound to the submitting paired agent. The export is an MP4 rendered with the existing `youtube_automation.video_edit.render_video` FFmpeg renderer, using a uniform duration fit of the full source video to the narration. The account settings job list shows progress, safe failure details, and the local output path; queued or running jobs can be canceled there.

This is intentionally the first/basic workflow, not semantic scene matching: it does not use Gemini transcription or ChatGPT speed recommendations. The existing `video_edit.process_video()` workflow still requires its Playwright browser integrations and has not been moved to the agent. FFmpeg runs only from the local application code; a job cannot supply a command or arbitrary path. If a job fails, the agent window contains local diagnostic details; select the files again to submit a new job. A disconnected job can be reclaimed by the same paired agent after its lease expires.

To run the focused local checks from the repository root:

```powershell
py -3 -m unittest discover -s tests -p "test_local_agent.py"
py -3 -m unittest discover -s tests -p "test_local_youtube_download.py"
py -3 -m unittest discover -s tests -p "test_local_video.py"
py -3 -m unittest discover -s tests -p "test_agent_store.py"
py -3 -m unittest discover -s tests -p "test_automation_integration.py"
```

The Flask-backed pairing, isolation, revocation, and end-to-end self-test cases are in `tests/test_server.py`; run `py -3 -m unittest discover -s tests -p "test_server.py"` to execute that integration suite.

## YouTube sign-in fallback

When a Render download receives YouTube's sign-in-to-confirm-you-are-not-a-bot response, the processing job stops without retrying the authentication-required request. The project page offers **Download with paired Windows agent**. Keep the paired agent running on the tester's own computer and start it with the browser profile in which that tester is signed in, for example:

```powershell
.\start_local_agent.bat run --api-url https://cutroom7.netlify.app --browser edge
```

The website queues the project-specific job and shows its download/upload progress. yt-dlp reads the selected browser session only on that Windows computer; browser cookies are never uploaded to Render or included in agent status/error messages. The video itself is transferred to the existing Render project using bounded, resumable chunks so the current transcription and clip-generation pipeline can continue. This hybrid flow therefore keeps authentication local, but the source video is stored on the Render disk after upload. It does not keep the large source file exclusively on the tester's PC.

The agent accepts only the fixed `agent_self_test`, `video_sync`, and project-bound `youtube_download` job types; it rejects unknown job types rather than treating payload data as a command. Paths are not submitted by the browser or accepted as job instructions. This does not move transcription, semantic clip selection, or rendering to Windows. Other sources and the regular server-side YouTube path remain unchanged.

Do not remove the existing Render disk or move the service to ephemeral/free storage yet. The remaining SQLite-backed account and OAuth data still needs a verified, non-destructive PostgreSQL migration before that would be safe. No SQLite database, media, or user data is automatically copied or deleted by this incremental change.
