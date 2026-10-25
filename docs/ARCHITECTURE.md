# Cutroom architecture

## Current runnable slice

~~~mermaid
flowchart LR
  Browser[Vanilla JS browser app] -->|HTTP / JSON / multipart| Flask[Flask local API]
  Flask --> SQLite[(SQLite users workspaces projects clips)]
  Flask --> Disk[Local uploads and exports]
  Flask --> Queue[(SQLite processing and export jobs)]
  Queue --> Worker[Local background worker]
  Worker --> FFmpeg[FFmpeg / ffprobe]
  Worker --> ASR[OpenAI Whisper API]
  Worker --> LLM[OpenAI clip selection]
  Browser -->|MP4 / SRT downloads| Flask
~~~

The local API binds to loopback. Media and generated source thumbnails are written under data/; SQLite holds users, workspaces, memberships, invites, workspace brand settings, projects, probed media properties, processing and export jobs, transcripts (including optional per-segment word timings), ideas, clips and immutable clip-edit versions. Export jobs persist FFmpeg progress, cancellation requests, retry counts, results, and workspace ownership; the editor polls and resumes active jobs. Transcript segments retain timestamp bounds, text revision, update time, a recoverable removed flag, and optional timed word JSON. Editing segment text clears its word timings to avoid highlighting against stale words. A project transcript-ready checkpoint prevents clip regeneration from silently replacing edits with a fresh transcription. Thumbnail and source routes check the signed-in workspace. Cookie sessions and membership checks scope project/planning/edit access. Owners/admins can create manually delivered, expiring invite tokens. Project archive/search/filter/sort/pagination and Brand Kit defaults are workspace-scoped. Caption font, colors, animation choice, size, outline, alignment and position are copied into versioned clip edit snapshots; FFmpeg export renders word-highlight ASS captions from stored timing when available and otherwise reports the static fallback. OpenAI keys are scoped to a workspace. With `CUTROOM_SECRET_ENCRYPTION_KEY`, the API stores Fernet ciphertext in the `workspace_secrets` table; local combined mode can keep credentials in memory when encryption is not configured. The worker retrieves the key only for the workspace on its claimed job through a token-protected internal API route. Project/clip/job APIs are in server.py; the UI is in index.html, app.js, media-library.js, job-history.js, editor.js, transcript-search.js, auth.js, brand-kit.js, styles.css, and production.css.

`docker-compose.yml` runs separate non-root API and media-worker containers against one shared SQLite database/media volume. The API does not start an inline worker in this mode; job claims use SQLite `BEGIN IMMEDIATE`, while the worker recovers interrupted jobs at startup and polls the persistent queue. The worker retrieves the claimed job workspace API key through a token-protected internal API route over the Compose bridge; keys are encrypted at rest by the API. The published API port is host-loopback-only and the trusted-client CIDR is limited to that bridge. This is a local split-process step, not a distributed queue: use one worker only; it does not provide PostgreSQL, Redis, S3 or public deployment hardening. Docker image build and startup still need verification on a host with Docker installed.

## Current boundaries

This shape is appropriate for local development, not a public SaaS. Account passwords and workspace roles exist, but there is no formal security review or audit trail. AI keys are workspace-scoped and encrypted at rest when the deployment encryption key is configured, but key rotation and a managed secrets service remain operational requirements. Queue records persist in SQLite and interrupted jobs are requeued on restart; the app uses a single local worker without distributed concurrency controls. Source-processing jobs support cancel requests and coarse stage/chunk progress, and exports support cancellation and FFmpeg progress. Cancellation waits for in-flight provider requests to return. Recovery may repeat a stage after a crash. Uploads are ordinary multipart POSTs and can consume application memory/disk while parsing. SQLite and local disk are single-host persistence. YouTube ingestion relies on a third-party downloader and may stop working due to platform changes or policy constraints. Transcription and selection require externally billed OpenAI API usage. The reference app/API is not a runtime dependency.

## Production target

| Concern | Target |
|---|---|
| Web client | Next.js + React + TypeScript; accessible dashboard, transcript editor, and timeline canvas |
| API | Versioned REST/OpenAPI API with typed request/response contracts and per-workspace authorization |
| Identity | Replace local auth with managed OIDC or hardened identity service; verified email, password recovery, session rotation, workspace membership and roles |
| Data | PostgreSQL with migrations, tenant-scoped foreign keys, audit events, idempotency keys |
| Storage | Private S3-compatible buckets, multipart resumable uploads, short-lived signed URLs, lifecycle and deletion jobs |
| Jobs | Redis + durable queue workers; persisted state/progress/retry count, timeout, cancellation and dead-letter queue |
| Media | Isolated FFmpeg/ffprobe workers with resource limits; original source, proxy, thumbnail, exports and subtitles modeled separately |
| AI | Provider interface for ASR/diarization/LLM; explicit provider/model/cost and source provenance on each job |
| Realtime | Server-sent events or WebSockets for progress; polling fallback |
| Operations | Structured logs, metrics, traces, health/readiness checks, backups, restore drills and quota enforcement |
| Deployment | Containerized web/API/worker, managed Postgres/Redis/object storage, secret manager and CI/CD |

## Media and editing data model target

- Workspace, Membership, User, Project, MediaAsset, ProcessingJob, Transcript, TranscriptSegment, ClipSuggestion, EditDecisionList, TimelineTrack, TimelineItem, CaptionStyle, Export, BrandTemplate, CalendarItem, SocialConnection, Publication, UsageEvent, and AuditEvent.
- Every durable row is workspace-scoped, with authorization enforced in the service layer and database queries.
- Current edit snapshots are immutable, versioned clip EDLs containing up to 20 non-overlapping source video segments in output order, normalized manual crop focus, original audio, one optional looping music bed, up to eight extra timeline-positioned audio asset tracks with independent volume/fades, optional logo overlay, captions and output profile. Autosaves use a SQLite write lock and base-version check; export concatenates video/audio segments and remaps captions from the saved snapshot. The browser preview applies the aspect crop and saved crop focus, but does not preview the concatenated timeline, captions, audio mix or overlays.
- Production editing still needs image/B-roll overlay tracks, crop/keyframes, transitions and a live composite preview. The current renderer consumes the saved audio tracks and reordered segments, while the browser preview is not yet rendered from the full EDL.
- A media job has id, workspace_id, project_id, stage, status, progress, attempt, idempotency_key, timestamps, and structured error. Stage transitions are explicit and validated.
- Deletion marks and schedules removal of source, derivative, transcript and export objects; legal hold/retention policies are applied before physical cleanup.

## Security notes

The local build is loopback-only and has a strict static-asset allowlist. Authenticated browser mutations use a session-bound CSRF token, same-origin validation and Fetch Metadata checks; session cookies are HttpOnly and SameSite=Strict, with the Secure flag configurable for HTTPS deployment. Public deployment still requires a security review of authentication and every object authorization path, rate limits, signed storage URLs, SSRF-safe URL ingestion, media sandboxing, secrets management, quotas and adversarial tenant-isolation tests. Do not expose the current Flask server directly to the Internet.
