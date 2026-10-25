# Implementation plan and tracker

## Completed this task

- [x] Inspect existing workspace, local app and user-supplied reference screenshot.
- [x] Inspect public feature/help/API documentation without logging into the reference product or calling its API.
- [x] Write reconnaissance, architecture, feature matrix and this continuation tracker.
- [x] Turn the original static UI into a runnable local client/server application.
- [x] Add persistent SQLite project/clip state, local uploads, YouTube URL ingestion, async processing, OpenAI transcription/selection, FFmpeg MP4/SRT export paths, and visible error/progress states.
- [x] Add project rename/duplicate/archive/restore/delete, server-side library search/filter/pagination, transcript search and transcript-range clip drafts.
- [x] Add a workspace-scoped All clips page with search, export filtering, pagination, project-aware editing and download actions.
- [x] Add workspace Brand Kit defaults for caption font, colors and preset; snapshot them into clip EDLs and FFmpeg exports.
- [x] Add a workspace-scoped image/audio asset library with file validation, private previews and deletion; let clip EDLs select a logo image that is rendered into MP4.
- [x] Add 9:16, 1:1 and 16:9 crop rendering, selectable burned caption styles, plus SRT/VTT sidecars.
- [x] Probe upload/import dimensions, frame rate and audio/video presence, generate workspace-protected source thumbnails, and add resumable chunk upload with progress, retry, cancellation, resume, and workspace access checks.
- [x] Add persisted Ideas board and a planning calendar; these do not claim to publish content.
- [x] Install imageio-ffmpeg and yt-dlp after approval; verify FFmpeg binary and libx264 encoder exist.
- [x] Add thirty-five Python integration tests; verify syntax, health endpoint, static-file allowlist, CSRF token enforcement and cross-origin rejection, generation preference persistence/validation/duplication and candidate count/platform/source bounds, language-aware timestamped transcription and word timing, video/audio upload inspection and thumbnails, rendered MP4 dimensions/audio, static and timed word-highlight captions, workspace assets and logo/music export, persistent export-job progress/private downloads/queued cancellation, queued source-analysis cancellation and recovery, lifecycle/archive/project and clip library queries, transcript edits/removal/regeneration and stale word-timing invalidation, word timestamp chunk offsets, concurrent versioned EDL saves, brand defaults and styled export, positioned additional-audio-track EDL validation and actual FFmpeg mixing, ordered/reordered video-segment validation/concatenated MP4/caption remapping, persisted manual crop positions and rendered framing, local auth and fixed-window account throttles, account deletion with media cleanup, workspace ownership transfer and member removal, SMTP-mode one-time verification/reset flows and session invalidation (mail transport mocked), workspace isolation and viewer write restrictions, resumable upload offsets/completion/access/cancellation and upload cleanup.
- [x] Add local account sessions, workspace membership and owner/admin/editor/viewer roles; scope projects and ideas by workspace.
- [x] Add manual expiring invite-token create/accept flow and local workspace switch/invite/join UI.
- [x] Persist processing jobs, coarse stage progress, attempts/timestamps/errors, recover interrupted jobs on restart, and cap user-triggered retries at three attempts.
- [x] Configure generation per project: maximum duration, desired clip count, platform, output aspect ratio and transcription language; preserve settings on project duplication.
- [x] Add a Docker Compose local split-process runtime with a persistent named data volume, non-root/read-only API and worker containers, token-protected workspace-scoped encrypted AI-key handoff, bounded logs, localhost-only published port and health check; [ ] build and exercise it on a Docker-enabled host.
- [x] Add authenticated, workspace-scoped resumable source uploads with bounded 8 MiB chunks, persisted offsets, browser resume/retry, cancellation, and 24-hour stale-session cleanup. [ ] Move source media to object storage and add signed multipart upload flows and quotas.
- [x] Smoke-start worker mode with an isolated SQLite database and verify it enters the queue polling loop; [ ] verify coordinated API/worker shutdown and restart in Docker Compose.

## Active continuation checklist — personal local use

This project is for one person running Cutroom on their own computer. Do not spend the next implementation cycles on hosting, SaaS tenancy, distributed infrastructure, billing, social publishing, or deployment. Keep the existing local account/workspace screens only where they help organize personal projects.

### P0 — Make the local clip workflow dependable

- [ ] Run an owner-authorized speech-bearing video through transcription → clip selection → edit → export, once a provider key and source are available.
- [x] Test transcription chunk offsets; [ ] test clip-overlap selection and exact trim-boundary behavior beyond existing job-recovery coverage.
- [ ] Confirm a generated MP4 visually matches saved timestamps/captions using a user-authorized source.
- [ ] Fix any real source import failures and document supported YouTube URL conditions.
- [x] Add a one-time `setup.bat` dependency install and make `start.bat` launch the local app without reinstalling packages each time; document local account, provider-key and `data/` locations.
- [x] Add a local backup script for the SQLite database and media library, with restore instructions and a reminder to preserve any encryption key separately.
- [ ] Evaluate a genuinely local transcription/selection option (including model download size, CPU performance, and setup friction) before adding dependencies; preserve the current API-backed path as an option.

### Deferred — only revisit if the owner asks to host or share Cutroom

- [x] Isolate OpenAI credentials by workspace; owner/admin settings write Fernet-encrypted SQLite ciphertext when `CUTROOM_SECRET_ENCRYPTION_KEY` is set, local combined mode supports memory-only keys, and worker handoff is scoped to the job workspace. [ ] Add managed secret storage/key rotation for multi-host deployment and verify an actual provider run.

- [ ] Replace the local Compose container with web/API, distributed worker, PostgreSQL, Redis and MinIO/S3-compatible storage; current Compose setup still runs the single-host SQLite application.
- [x] Add configurable SMTP email verification/recovery, one-time expiring tokens, password-reset session invalidation, and single-host SQLite throttles for account endpoints. [ ] Verify delivery against a real SMTP provider. [ ] Move throttles to shared Redis for multi-host deployment; add production database migrations, typed API contracts, managed identity and security review.
- [x] Add password-confirmed account deletion with sole-member workspace/media cleanup, shared-workspace retention, and protection against deleting an owner with teammates. [ ] Add ownership-transfer workflow, cleanup retry/retention guarantees and activity/audit history.
- [x] Add owner/admin member listing, member removal with role checks, and explicit ownership transfer in workspace settings. The former owner becomes admin, enabling a reviewed account deletion path. [ ] Add workspace role changes and audited ownership/member events.
- [ ] Move long media work to a durable queue; persist events/progress, cancellation, retry/backoff, idempotency, and dead-letter diagnostics.
- [x] Add resumable local chunk uploads, content probing, thumbnails, and cleanup of partial sessions on cancellation/account deletion. [ ] Add S3-compatible multipart uploads, signed URLs, quotas, proxy generation, and durable cleanup jobs.
- [ ] Add API OpenAPI spec, .env.example, secrets controls, and a clean-install runbook.

### P2 — Editing and captions

- [x] Persist a versioned clip EDL for trim/title, up to 20 reorderable non-overlapping video segments, output aspect, manual crop focus and caption settings; render exports from the saved version.
- [x] Allow transcript segment corrections and recoverable removal/restoration; use the edited transcript for later clip generation.
- [x] Expand EDL with independently timed audio asset tracks and ordered multi-item video segments; [ ] add image/B-roll overlay timeline items. [x] Persist and consume provider word timings when available; [ ] verify one real provider run.
- [x] Add transcript search and corrections; [ ] add text-based cuts mapped to source intervals.
- [x] Add a zoomable source timeline with seek, draggable in/out trim handles, split/remove/reorder controls, undo/redo and EDL autosave. [ ] Add image/B-roll lanes, frame-accurate preview, and drag/drop clip arrangement.
- [x] Add aspect ratio and normalized horizontal/vertical crop-focus controls; render using the saved crop. Add burned-in caption presets, font/size/color/outline, alignment, top/middle/bottom safe positioning, and word-highlight ASS rendering. [ ] Validate browser crop against rendered output visually and verify provider-generated word timings.
- [ ] Add speaker/face tracking as a separately selectable capability with center-crop fallback and confidence display.
- [x] Add workspace media library, static image-logo overlay, one looping music bed plus up to eight independently positioned/faded audio tracks, and selectable export resolution/frame rate/bitrate. [ ] Add multitrack video, image/B-roll overlays, transitions, title cards and profanity controls.
- [x] Queue exports durably with persisted FFmpeg progress, workspace-scoped status/download results, cancel, three-attempt retry and editor resume after reopening. [ ] Move export workers/queue off the local shared SQLite volume for multi-host scaling.

### P3 — Content operations and business features

- [ ] Workspace settings and activity history; project CRUD/archive is implemented locally.
- [x] Workspace Brand Kit caption defaults and reusable image/audio asset storage; [ ] reusable brand templates and font upload.
- [ ] Persisted ideas board and calendar with filters.
- [ ] Official social OAuth and publishing adapters; test only with authorized sandbox/creator accounts and report missing scopes.
- [ ] Analytics sourced only from authorized platform responses with timestamps and provenance.
- [ ] Usage metering, plan limits, billing and audit trail; account deletion now handles private solo workspaces and shared membership removal but requires stronger lifecycle and operational verification.

### P4 — Release confidence

- [ ] Playwright core journeys and adversarial authorization-isolation tests.
- [ ] CI lint/type/unit/integration/E2E checks and dependency/container scans.
- [ ] Structured observability, backups/restore, retention, incident and capacity guidance.
- [ ] Production deployment guide and a clean-environment acceptance run.

## External inputs needed

- An OpenAI API key and an authorized speech-bearing test video are needed to prove a real model run. Add the key through Cutroom's Connect AI dialog; local combined mode can hold it in memory, or persist it encrypted when `CUTROOM_SECRET_ENCRYPTION_KEY` is configured. API charges apply to the key owner.
- The actual transcription and selection workflow needs either a user-configured provider or a separately selected local model. Processing a YouTube source still depends on its availability and the user's rights to use it.
