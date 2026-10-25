# Reference reconnaissance

Date: 18 October 2026. This is a feature/workflow inventory, not a claim to know Choppity's internals.

## Sources inspected

- User-provided screenshot of app.choppity.com/projects (the only authenticated product screen supplied). It shows a fixed left rail for Projects, Brand template, Social profiles, Calendar, Analytics, Storage and support; a source picker for new/existing video; a pasted-link field; a drag/drop upload zone; optional custom instructions; an empty project library; and an upload call to action.
- Public Choppity AI video editor feature page: https://www.choppity.com/features/ai-video-editor/ . It publicly describes upload/YouTube intake, semantic highlight selection, transcript editing, framing, captions, B-roll, and export/publishing.
- Public help articles for dashboard, clip generation, editing clips, transcript editing, captions, auto-framing/speaker tracking, profanity, brand templates, calendar, pricing, and save/preview/export. See linked sources in the Feature Matrix and README.
- Public API docs quickstart: https://docs.choppity.com/quickstart . It documents an API key, asynchronous clip job, polling/webhook completion, full-quality render, and draft/publish operations. This project does not call or depend on that API.
- Public /projects app URL was not inspectable through an unauthenticated page fetch (only a thin iframe shell was exposed). No credentials were supplied, and no attempt was made to sign in, bypass access controls, or inspect private screens.

## Confirmed workflow surface

| Area | Observed/documented behavior | Evidence type |
|---|---|---|
| Project intake | Upload a new file, choose an existing video, or paste a YouTube URL where enabled; optional natural-language instructions | Screenshot + dashboard help |
| Clip generation | Configure target duration and custom instructions, start async analysis, review a folder of selected clips | Official help + API quickstart |
| Clip refinement | Preview, trim, split/reorder, edit transcript and captions, auto-frame to social formats, add supporting media | Official help |
| Captioning | Correct caption words/line breaks/timing; export subtitle sidecars | Official help |
| Brand | Reusable caption styling, fonts/colors/logo and layout settings | Official help |
| Content ops | Ideas, calendar, connected profiles, scheduled/published post states and analytics sections appear in the screenshot/docs | Screenshot + official help |
| Publishing API | Draft posts, update caption, publish, poll render and subscribe to webhooks are described in public API docs | Official API docs |

## Product interpretation

The screenshot makes source intake and a useful empty library first-class. The docs expand the product beyond clip generation into editing and publishing operations. Cutroom should therefore have clear project and editor routes with a real async media pipeline and saved edit model; it should not expose nonfunctional sidebar controls as if they work.

## Current evidence limits

- Detailed logged-in Choppity editor screens, team/billing administration, private settings, actual platform OAuth dialogs, and real export results were not accessible in this task.
- Marketing copy is treated as a claim about Choppity's public positioning, not independently verified benchmark performance.
- Each feature marked in our matrix refers to Cutroom implementation status and evidence, not feature parity assumed from the reference.
