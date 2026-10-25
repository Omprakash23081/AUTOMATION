# Deployment preparation plan

## Goal and scope

Prepare this existing vanilla-JavaScript/Flask project for a static Netlify frontend and a separately hosted Render Flask API. Preserve the current Windows/local workflow. Scope is limited to deployment/runtime configuration, the minimum frontend-to-backend URL/CORS/session plumbing required for split hosting, and deployment documentation. Do not change product behavior, business workflows, `.env`, secrets, user data, or unrelated files.

## Architecture discovered

- The frontend is a root-level `index.html` plus allowlisted root-level JavaScript and CSS files, not a framework build.
- The existing Docker image copies the main server modules and required media package; the deployment packaging must also include the Flask automation blueprint module.
- `server.py` is a Flask app with SQLite and user/session authentication. Locally it serves both the frontend and API and rejects non-loopback clients.
- Frontend API calls occur in multiple scripts; authenticated mutations use a session-bound CSRF token. Browser uploads also use `XMLHttpRequest` for resumable 8 MiB chunks.
- The backend starts generation/export threads inside the same Python process in combined mode. The Docker Compose split worker depends on a shared local SQLite/media volume and is not a multi-host queue.
- The project writes source media, exports, thumbnails, assets, automation state, and SQLite data to local disk. A Render filesystem is ephemeral without a persistent disk; multiple independently scaled instances cannot share this local state.

## Planned changes

1. Add a Netlify static build step that stages only the browser application's allowlisted files, writes a non-secret API base URL from a build environment variable, and prevents Python files, `.env`, SQLite, and local media from entering the published directory.
2. Add a frontend API URL configuration/helper before application scripts. Route all fetch, CSRF, chunk-upload, media URL, and API-navigation surfaces consistently to the configured backend. Default to same-origin so local startup continues to work.
3. Add narrowly configured Flask deployment controls: optional exact-origin CORS with credentials and preflight handling, deployment-mode public access gated by explicit settings and allowed host/origin lists, configurable secure cookie SameSite behavior, and proxy-header support for Render TLS/host handling. Keep local defaults unchanged.
4. Add a Render Blueprint for a single-instance Docker deployment with a persistent disk and one Gunicorn worker; add the required Gunicorn dependency/configuration. Do not present this as a horizontally scalable worker/queue architecture.
5. Document the required Netlify/Render environment variables, cookie/CORS and YouTube OAuth configuration, persistent-disk requirement, platform limits, and explicit media/long-running-job caveats.

## Files in planned scope

- `.github/modernize/deployment/plan.md`
- `.github/modernize/deployment/progress.md`
- `.gitignore`
- `netlify.toml`
- `scripts/build-netlify.js`
- `render.yaml`
- `gunicorn.conf.py`
- `requirements.txt`
- `Dockerfile`
- `index.html`
- `api-config.js`
- `auth.js`
- `media-library.js`
- `server.py`
- `README.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`

## Deployment type

Static site hosted by Netlify, calling a single Render Docker web service over HTTPS. Render runs one Gunicorn process/worker, with Flask's existing in-process background threads and a mounted persistent disk. This is a constrained single-instance deployment, not a distributed worker architecture.

## Validation steps

1. Check that Netlify's configured publish directory contains only the frontend allowlist and generated runtime API configuration; verify deployment secrets/data/backend source are excluded.
2. Check the API URL configuration defaults to same-origin locally and builds a valid absolute HTTPS backend URL when `CUTROOM_API_BASE_URL` is supplied.
3. Search every frontend script for API fetch, raw XHR, media preview/download, and OAuth navigation surfaces; ensure each uses the same configurable API endpoint and credential mode.
4. Inspect Flask's local-vs-deployment gates, exact-origin CORS responses/preflight, CSRF Origin validation, session cookie settings, and proxy-derived URL behavior. Preserve strict local defaults.
5. Validate Netlify/Render/Gunicorn configuration syntax and dependency declarations.
6. Run focused local tests and static checks where the available environment permits. Record any unavailable tool/platform checks and constraints in the final report.
