# Local API reference

The Flask server exposes JSON endpoints under /api. By default it binds to 127.0.0.1 and rejects non-loopback requests. The optional, constrained Netlify + Render preview can explicitly enable public access with exact host/origin allowlists and credentialed CORS; see [DEPLOYMENT.md](DEPLOYMENT.md). API routes except health and account entry require a signed-in account. Authenticated writes require the session-bound token from `GET /api/csrf` in `X-CSRF-Token`; unsafe requests with an unconfigured `Origin` or `Sec-Fetch-Site: cross-site` are rejected. Browser code retrieves and sends the token automatically, including multipart uploads. Workspace membership scopes project and planning records; viewer roles cannot mutate. This local auth is not a public deployment security review.

## Accounts and workspaces

| Method | Path | Purpose |
|---|---|---|
| POST | /api/auth/register | Create local account and owner workspace (password minimum 12 characters) |
| POST | /api/auth/login | Start session |
| POST | /api/auth/logout | Clear session |
| GET | /api/auth/me | Return current user, workspace, and role |
| POST | /api/auth/verify-email | Consume an expiring email verification token and start a session |
| POST | /api/auth/verification/resend | Send a replacement verification link when SMTP mode is enabled |
| POST | /api/auth/password/forgot | Request a generic password-reset email response |
| POST | /api/auth/password/reset | Consume a one-time reset token, change password, and invalidate existing sessions |
| DELETE | /api/auth/account | Delete account; requires current password and `confirmation: "DELETE"` |
| GET | /api/csrf | Create/read a session-bound CSRF token for authenticated browser mutations |
| GET | /api/workspaces | List memberships |
| POST | /api/workspaces/select | Select a member workspace (`workspaceId`) |
| GET | /api/workspaces/members | List members of the selected workspace (owner/admin only) |
| POST | /api/workspaces/members/transfer-owner | Transfer ownership to an existing member; the current owner becomes admin |
| DELETE | /api/workspaces/members/{userId} | Remove a member; owner/admin permissions are enforced |
| POST | /api/workspaces/invites | Owner/admin creates an expiring token for a specific email and role |
| POST | /api/workspaces/invites/accept | Signed-in matching email redeems token |

Invite tokens must be delivered manually. Email verification and reset links require `CUTROOM_EMAIL_MODE=smtp` and configured SMTP; disabled local mode verifies accounts immediately. Account endpoints have fixed-window, SQLite-backed throttles (registration 5/hour, login 10/15 minutes, verification 10/15 minutes, resend/reset-email 3/hour, reset submission 10/15 minutes; keyed by IP and email where appropriate). These are single-host safeguards and do not coordinate across multiple app hosts. User administration and audit events are absent. AI credentials are workspace-scoped. Owners/admins manage the active workspace key; API/worker mode requires `CUTROOM_SECRET_ENCRYPTION_KEY` and persists Fernet ciphertext. Local combined mode permits memory-only storage when that key is absent.

Account deletion removes the user's memberships and tokens. Workspaces where they are the sole member are deleted along with projects, transcripts, clips, ideas, brand settings, assets, and app-managed media files. Shared workspace content is retained when the user leaves. Deletion is refused while the user owns a shared workspace or has a running media job in a sole-member workspace; transfer ownership or wait for the job before retrying. Filesystem cleanup only unlinks paths inside Cutroom's configured upload/export/thumbnail/asset directories.

## Health and AI key

| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | Media tool presence, AI-key configured boolean, upload limit |
| POST | /api/settings/openai | JSON body with apiKey; owner/admin only; saves for the active workspace, encrypted when configured |
| DELETE | /api/settings/openai | Remove the active workspace AI key; owner/admin only |

The health response never returns the API key and reports whether the signed-in workspace has one configured. API/worker mode requires encrypted persistence. The internal worker handoff is token-protected and requires `workspaceId`; it returns only that workspace credential. OpenAI errors are attached to the failed project as a bounded error string.

## Resumable source uploads

Large browser uploads use a resumable local chunk protocol:

| Method | Path | Purpose |
|---|---|---|
| POST | /api/projects/{id}/upload-sessions | Create a workspace-scoped upload session from `filename` and `size` |
| GET | /api/upload-sessions/{uploadId} | Read the current acknowledged byte offset |
| PUT | /api/upload-sessions/{uploadId}/chunk?offset={bytes} | Append one raw chunk (up to 8 MiB) at the exact acknowledged offset |
| POST | /api/upload-sessions/{uploadId}/complete | Probe and attach the finished media to its project |
| DELETE | /api/upload-sessions/{uploadId} | Cancel and remove the temporary upload |

Each chunk is bounded and streamed to a temporary file; the server persists its acknowledged offset so a browser can resume after a network interruption. Session access is workspace-scoped, and stale sessions are removed after 24 hours when a new session is created. This is resumable local-disk upload, not object-storage multipart upload; the existing `/api/projects/{id}/upload` endpoint remains supported.

## Projects and media

| Method | Path | Purpose |
|---|---|---|
| GET | /api/projects?scope=active\|archived\|all | Workspace-scoped library; supports `q`, `status`, `sourceType`, `sort=recent\|oldest\|name`, `page`, and `perPage` |
| GET | /api/clips?q=... | Workspace-scoped clip library; searches title, hook, transcript and project name; supports `exported=0\|1`, `page` and `perPage` |
| POST | /api/projects | Create an upload project or YouTube URL project. Optional fields: `maxDuration` (5–180 seconds, default 60), `clipCount` (1–20, default 8), `targetPlatform` (`youtube_shorts`, `instagram_reels`, `tiktok`, `linkedin`, `general`), `aspectRatio` (`9:16`, `1:1`, `16:9`), `language` (`auto` or a two-letter ISO language code), and custom `instructions` |
| GET | /api/projects/{id} | Project status, source metadata, clip drafts and latest ten processing-job records |
| POST | /api/projects/{id}/local-youtube-download | Queue an authentication-failed YouTube source download on the user's paired, online Windows agent |
| POST multipart | /api/projects/{id}/upload | Upload one supported media source; response includes duration, dimensions, frame rate, stream presence, and thumbnail availability |
| GET | /api/projects/{id}/thumbnail | Workspace-protected JPEG preview generated during source inspection |
| GET | /api/projects/{id}/source | Preview original source with range support |
| POST | /api/projects/{id}/generate | Enqueue or explicitly retry processing (up to three attempts per job); returns 202 and jobId. Authentication-required YouTube failures are not retried; an attached local-agent source can be processed normally |
| DELETE | /api/processing-jobs/{jobId} | Cancel queued/running processing; active provider requests finish before cancellation is observed |
| GET | /api/projects/{id}/transcript?q=... | Search or page through persisted segments (`limit` up to 500, `offset`; `includeDeleted=1` includes recoverable removals); each segment returns word timing entries when available, otherwise `words: []` |
| PATCH | /api/projects/{id}/transcript/{segmentId} | Correct a transcript segment using `baseRevision`; stale edits return 409 |
| DELETE | /api/projects/{id}/transcript/{segmentId} | Soft-remove a segment; it remains recoverable in the database |
| POST | /api/projects/{id}/transcript/{segmentId}/restore | Restore a soft-removed segment |
| POST | /api/projects/{id}/clips/from-transcript | JSON start/end seconds and title; create a transcript-range draft |
| PATCH | /api/projects/{id} | Rename project and/or set `archived` boolean; restore by setting false |
| POST | /api/projects/{id}/duplicate | Duplicate metadata, transcript and unrendered clip drafts |
| DELETE | /api/projects/{id} | Delete project and unreferenced local media |

Project processing states: Needs upload, Ready to process, Queued, Downloading source, Transcribing, Finding clips, Ready, Failed.
If a browser upload is cancelled or fails, the existing upload project remains available to receive the source again at the same upload endpoint.

## Ideas and planning calendar

| Method | Path | Purpose |
|---|---|---|
| GET/POST | /api/ideas | List or create an idea |
| PATCH/DELETE | /api/ideas/{id} | Edit status/content or delete an idea |
| GET | /api/calendar?month=YYYY-MM | Return planned drafts for a month |

The calendar is a planning surface only; it does not schedule or publish posts to social platforms.

## Windows local-agent YouTube fallback

The browser uses its authenticated session and CSRF token to queue a fallback only after the project has a recognized YouTube authentication error. The agent must already be paired, approved for local-agent testing, and recently online. The job is pinned to that agent and project; the project detail response includes its local-agent progress. The agent's `--browser` selection is configured only on Windows and is never sent in job payloads.

| Method | Path | Purpose |
|---|---|---|
| POST | /api/projects/{id}/local-youtube-download | Queue a YouTube download against the failed project's existing URL |
| POST | /api/agent/runtime/jobs/{jobId}/upload-sessions | Create a source-upload session for the active, project-bound download job |
| PUT | /api/agent/runtime/jobs/{jobId}/upload-sessions/{uploadId}/chunk?offset={bytes} | Upload one bounded raw chunk using the paired agent's bearer token |
| POST | /api/agent/runtime/jobs/{jobId}/upload-sessions/{uploadId}/complete | Validate and attach the source video, preserving the original YouTube URL for captions |

The runtime upload routes require the paired agent's bearer token and verify that the active job, agent, workspace, project, and upload session all match. Browser cookies are read locally by yt-dlp and are not sent in API requests or agent status/error updates. The source video is uploaded to the Render project so the existing server-side processing pipeline can continue; this fallback does not keep the source video exclusively on Windows.

## Clips and exports

| Method | Path | Purpose |
|---|---|---|
| GET | /api/clips/{id}/edits | Current version and latest 30 immutable edit snapshots |
| PUT | /api/clips/{id}/edit | Save a versioned clip EDL; send `baseVersion` to reject stale edits |
| POST | /api/clips/{id}/edits/{version}/restore | Restore an old EDL as a new version |
| POST | /api/clips/{id}/export | Render an MP4 using the saved EDL: up to 20 ordered source segments, title, aspectRatio, outputResolution (480/720/1080), frameRate (24/25/30/50/60), videoBitrate (2.5/6/10/16 Mbps), caption styling, logo, and audio mix |
| POST | /api/clips/{id}/export-jobs | Queue a saved edit version for durable background rendering; returns 202 and jobId |
| GET | /api/clips/{id}/export-jobs | List recent export jobs for this workspace clip |
| GET | /api/exports/{jobId} | Read workspace-scoped status, progress, retry count and completed result |
| DELETE | /api/exports/{jobId} | Cancel a queued/running export |
| POST | /api/exports/{jobId}/retry | Re-queue a failed export, up to three total attempts |
| GET | /api/clips/{id}/download | Download rendered MP4 |
| GET | /api/clips/{id}/captions | Download SRT sidecar |
| GET | /api/clips/{id}/captions?format=vtt | Download WebVTT sidecar |

## Brand Kit

| Method | Path | Purpose |
|---|---|---|
| GET | /api/brand | Read current workspace brand name, accent/caption colors, supported caption font, and default caption preset |
| PATCH | /api/brand | Save workspace defaults; accepts `brandName`, `accentColor`, `captionColor`, `captionBackground`, `fontFamily` (Arial/DejaVu Sans/Liberation Sans), and `defaultCaptionStyle` (classic/bold/boxed) |

Brand defaults are applied to newly initialized clip edit decision lists and saved in each immutable edit version. Existing saved edits retain their settings until a user applies the workspace defaults in the clip editor. FFmpeg export uses the font and colors stored in the edit snapshot.

## Workspace assets

| Method | Path | Purpose |
|---|---|---|
| GET | /api/assets?type=image|audio | List assets in the active workspace; omit type to list all |
| POST | /api/assets | Upload PNG/JPEG/WebP images or MP3/M4A/WAV audio (100 MB per file) |
| GET | /api/assets/{id}/file | Authenticated, workspace-scoped asset preview/download |
| DELETE | /api/assets/{id} | Delete an unused workspace asset; saved logo references must be removed first |

Image data is decoded and dimension-limited; audio is probed by FFmpeg. Asset metadata and files are stored on the local data volume. A saved clip EDL may reference logoAssetId and musicAssetId plus `audioTracks`, an array of up to eight workspace audio assets with output-timeline start/end, volume, and fade-in/out values. MP4 export overlays a selected image in the lower-right and mixes a selected looping music bed with source audio (or uses it alone when the source has no audio); sourceVolume and musicVolume accept 0–2, while musicFadeIn/musicFadeOut each accept 0–10 seconds, bounded by clip duration. Export resolution supports 480p/720p/1080p across the three aspect ratios; frame rate is 24/25/30/50/60, and video bitrate presets are 2.5/6/10/16 Mbps. This local library is not object storage; asset uploads still use multipart POST and do not use the resumable source-upload protocol. Additional audio layers are included in the saved EDL and rendered with timeline offsets, per-track gain, and fades.

Source processing uses a SQLite-backed local queue. Job records persist, and project detail returns the ten newest with stage/chunk progress, attempts, timestamps and error details. Queued and running jobs accept cancellation; checks run between stages, in YouTube download hooks, and between transcription/selection requests, so an in-flight provider call must return before cancellation completes. Interrupted running jobs are re-queued on server startup until the three-attempt limit unless cancellation was requested. Retry is user-triggered to avoid repeating billed provider work silently. Transcript segments are stored as a checkpoint, allowing recovery to skip transcription when that stage committed. This remains a single-host queue without distributed leases or multi-worker coordination. Tenant scoping is implemented locally but needs adversarial authorization testing before deployment.

Exports also use a persistent local SQLite queue. FFmpeg progress is reported from its progress stream; queued work can be canceled, in-progress renders terminate when the next progress update observes cancellation, and failed jobs can be retried up to three attempts. The clip editor resumes polling an active job when reopened. This queue is still single-host SQLite, not a distributed production queue.

Editor changes are stored as immutable, versioned clip EDL snapshots. Export saves inline edit fields when supplied, checks `editVersion` when provided, and renders from the resulting saved snapshot. Older EDL snapshots are upgraded to the current schema when exported. `videoSegments` accepts 1–20 non-overlapping `{sourceStart,sourceEnd}` objects in desired output order, each at least 0.2 seconds; the server computes contiguous output timeline positions and caps total duration at 180 seconds. `cropX` and `cropY` are normalized crop-focus positions in the range 0–1, defaulting to 0.5; zero aligns to the left/top edge and one to right/bottom. Video, original audio and captions concatenate in the same order; SRT and word-highlight timings are remapped to output time. Caption position maps to ASS safe margins based on output size. The EDL also supports one optional music bed, up to eight additional timeline-positioned audio asset tracks with gain/fades, caption settings, output encoding, and an optional image logo. Automatic subject tracking, video overlay tracks and a rendered live preview are not yet implemented.

Transcription requests segment and word timestamps when the selected OpenAI model supports both. Word timings are stored with each transcript segment; correcting segment text clears those timings because they no longer safely match the edited words. Word-highlight export uses timed ASS karaoke captions when timing is available and reports a static-caption fallback otherwise. OpenAI documents word timestamp generation as incurring additional latency.
