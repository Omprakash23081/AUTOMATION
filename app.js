const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const themeToggle = $("#themeToggle");
function setTheme(theme, persist = false) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  themeToggle.setAttribute("aria-pressed", String(isDark));
  themeToggle.innerHTML = isDark
    ? '☀ <span>Light mode</span>'
    : '☾ <span>Dark mode</span>';
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", isDark ? "#171a1f" : "#101114");
  if (persist) {
    try {
      localStorage.setItem("cutroom-theme", isDark ? "dark" : "light");
    } catch (error) {
      console.warn("Could not save the theme preference.", error);
      toast("Theme changed, but your browser could not save this preference.");
    }
  }
}
setTheme(document.documentElement.dataset.theme || "light");
themeToggle.addEventListener("click", () =>
  setTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
    true,
  ),
);
let projects = [],
  source = null,
  selectedFile = null,
  pendingUploadProject = null,
  pendingUploadSource = null,
  aiConfigured = false,
  openaiConfigured = false,
  geminiConfigured = false,
  groqConfigured = false,
  toastTimer,
  projectPage = 1,
  projectPages = 1,
  projectTotal = 0,
  searchTimer;
const api = async (url, opts = {}) => {
  const pathname = new URL(url, window.location.href).pathname;
  if (
    /^\/api\/projects\/[^/]+\/upload$/.test(pathname) &&
    opts.body instanceof FormData &&
    window.uploadMediaWithProgress
  )
    return window.uploadMediaWithProgress(url, opts.body);
  const res = await fetch(url, opts);
  let data = {};
  try {
    data = await res.json();
  } catch {}
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
};
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), 3600);
}
document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest(".nav-item[data-view]");
    if (!button) return;
    if (button.dataset.view === "youtube-dashboard") {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.showYoutubeDashboard?.();
      return;
    }
    if (button.dataset.view === "automation") {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.showAutomation?.();
      return;
    }
    $("#youtubeView")?.classList.add("hidden");
    $("#automationView")?.classList.add("hidden");
    $("#projectsView")?.classList.remove("hidden");
    $("#moduleView")?.classList.remove("hidden");
    window.hideAutomation?.();
  },
  true,
);
function esc(text = "") {
  return String(text).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[c],
  );
}
function formatBytes(n) {
  if (n < 1048576) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}
function formatTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function ytId(value) {
  try {
    const u = new URL(value);
    if (u.hostname === "youtu.be") return u.pathname.slice(1);
    if (u.hostname.endsWith("youtube.com"))
      return (
        u.searchParams.get("v") || u.pathname.split("/").filter(Boolean).pop()
      );
  } catch {}
  return null;
}
async function load() {
  try {
    const params = new URLSearchParams({
      scope: $("#projectScope")?.value || "active",
      status: $("#projectStatus")?.value || "",
      sourceType: $("#projectSource")?.value || "",
      sort: $("#sortOrder")?.value || "recent",
      page: String(projectPage),
      perPage: "12",
      q: $("#searchInput")?.value.trim() || "",
    });
    const [p, h] = await Promise.all([
      api("/api/projects?" + params),
      api("/api/health"),
    ]);
    projects = p.projects;
    projectPage = p.page;
    projectPages = p.pages;
    projectTotal = p.total;
    aiConfigured = h.ai_configured;
    openaiConfigured = !!h.openai_configured;
    geminiConfigured = !!h.gemini_configured;
    groqConfigured = !!h.groq_configured;
    $("#keyButton").textContent = aiConfigured
      ? "⚿  AI connected"
      : "⚿  AI settings";
    updateProviderKeyStatus();
    renderProjects();
  } catch (e) {
    if (!/sign in to continue|session has expired/i.test(e.message))
      toast(`Could not connect to Cutroom server: ${e.message}`);
  }
}
function updateProviderKeyStatus() {
  $("#openaiKeyStatus").textContent = openaiConfigured
    ? "· Connected"
    : "· Not connected";
  $("#geminiKeyStatus").textContent = geminiConfigured
    ? "· Connected"
    : "· Not connected";
  $("#groqKeyStatus").textContent = groqConfigured
    ? "· Connected"
    : "· Not connected";
}
function pickTab(tab) {
  $$(".source-tab").forEach((b) =>
    b.classList.toggle("selected", b.dataset.tab === tab),
  );
  $("#dropzone").classList.toggle("hidden", tab !== "upload");
  $("#youtubeEntry").classList.toggle("hidden", tab !== "youtube");
  $("#sourceStatus").classList.add("hidden");
  source = null;
  selectedFile = null;
  $("#continueBtn").disabled = true;
  $("#continueBtn").classList.add("disabled");
}
$$(".source-tab").forEach((b) =>
  b.addEventListener("click", () => pickTab(b.dataset.tab)),
);
function chooseFile(file) {
  if (!file) return;
  const ext = file.name.split(".").pop().toLowerCase();
  if (!["mp4", "mov", "webm", "m4v", "mp3", "m4a", "wav"].includes(ext)) {
    toast("Choose MP4, MOV, WebM, M4V, MP3, M4A, or WAV.");
    return;
  }
  if (file.size > 4 * 1024 ** 3) {
    toast("This file is over the 4 GB upload limit.");
    return;
  }
  selectedFile = file;
  source = { type: "upload", name: file.name };
  const el = $("#sourceStatus");
  el.textContent = `✓  ${file.name}  ·  ${formatBytes(file.size)}  ·  Ready`;
  el.classList.remove("hidden");
  $("#continueBtn").disabled = false;
  $("#continueBtn").classList.remove("disabled");
}
$("#fileInput").addEventListener("change", (e) =>
  chooseFile(e.target.files[0]),
);
$("#browseFiles").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#fileInput").click();
});
$("#dropzone").addEventListener("click", (e) => {
  if (e.target.id !== "browseFiles") $("#fileInput").click();
});
$("#dropzone").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("#fileInput").click();
  }
});
["dragenter", "dragover"].forEach((n) =>
  $("#dropzone").addEventListener(n, (e) => {
    e.preventDefault();
    $("#dropzone").classList.add("dragover");
  }),
);
["dragleave", "drop"].forEach((n) =>
  $("#dropzone").addEventListener(n, (e) => {
    e.preventDefault();
    $("#dropzone").classList.remove("dragover");
  }),
);
$("#dropzone").addEventListener("drop", (e) =>
  chooseFile(e.dataTransfer.files[0]),
);
$("#loadYoutube").addEventListener("click", () => {
  const url = $("#youtubeUrl").value.trim();
  if (!ytId(url)) {
    toast("Paste a valid YouTube video URL.");
    return;
  }
  source = { type: "youtube", url, name: "YouTube video" };
  const el = $("#sourceStatus");
  el.textContent = `✓  YouTube link added  ·  ${url}`;
  el.classList.remove("hidden");
  $("#continueBtn").disabled = false;
  $("#continueBtn").classList.remove("disabled");
});
$("#youtubeUrl").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#loadYoutube").click();
});
function openSetup() {
  if (!source) {
    toast("Add a video file or YouTube link first.");
    return;
  }
  $("#projectName").value = source.name.replace(/\.[^.]+$/, "");
  $("#setupDialog").showModal();
}
$("#continueBtn").addEventListener("click", openSetup);
$("#newProject").addEventListener("click", () => {
  document.querySelector(".create-card").scrollIntoView({ behavior: "smooth" });
  if (source) openSetup();
});
$("#emptyCreate").addEventListener("click", () =>
  document.querySelector(".create-card").scrollIntoView({ behavior: "smooth" }),
);

$("#startProject").addEventListener("click", async () => {
  if (!source) return;
  const btn = $("#startProject");
  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    const uploadKey = selectedFile
      ? [selectedFile.name, selectedFile.size, selectedFile.lastModified].join(
          ":",
        )
      : null;
    let p;
    if (
      selectedFile &&
      pendingUploadProject &&
      pendingUploadSource === uploadKey
    ) {
      p = pendingUploadProject;
    } else {
      const created = await api("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceType: source.type,
          url: source.url,
          name: $("#projectName").value.trim() || source.name,
          instructions: $("#instructions").value.trim(),
          maxDuration: Number($("#maxDuration").value || 60),
          clipCount: Number($("#clipCount").value || 8),
          targetPlatform: $("#targetPlatform").value,
          aspectRatio: $("#aspectRatio").value,
          language: $("#language").value,
        }),
      });
      p = created.project;
      if (selectedFile) {
        pendingUploadProject = p;
        pendingUploadSource = uploadKey;
      }
    }
    if (selectedFile) {
      const fd = new FormData();
      fd.append("file", selectedFile, selectedFile.name);
      await api(`/api/projects/${p.id}/upload`, { method: "POST", body: fd });
      pendingUploadProject = null;
      pendingUploadSource = null;
    }
    $("#setupDialog").close();
    source = null;
    selectedFile = null;
    $("#fileInput").value = "";
    $("#youtubeUrl").value = "";
    $("#sourceStatus").classList.add("hidden");
    $("#continueBtn").disabled = true;
    $("#continueBtn").classList.add("disabled");
    await load();
    openProject(p.id);
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Create project <span>→</span>";
  }
});
function renderProjects() {
  const list = projects;
  $("#projectCount").textContent = projectTotal;
  $("#emptyState").classList.toggle("hidden", projectTotal > 0);
  $("#projectPagination").classList.toggle("hidden", projectPages <= 1);
  $("#projectPageLabel").textContent =
    `Page ${projectPage} of ${projectPages} · ${projectTotal} projects`;
  $("#projectPrevious").disabled = projectPage <= 1;
  $("#projectNext").disabled = projectPage >= projectPages;
  const scope = $("#projectScope").value,
    filtered =
      $("#searchInput").value ||
      $("#projectStatus").value ||
      $("#projectSource").value;
  const heading = $("#emptyState h3"),
    message = $("#emptyState p"),
    create = $("#emptyCreate");
  if (projectTotal === 0 && scope === "archived") {
    heading.textContent = "No archived projects.";
    message.textContent =
      "Archived projects will appear here so you can restore them later.";
    create.classList.add("hidden");
  } else if (projectTotal === 0 && filtered) {
    heading.textContent = "No matching projects.";
    message.textContent =
      "Try a different search or change the library filters.";
    create.classList.add("hidden");
  } else if (projectTotal === 0) {
    heading.textContent = "Your next great clip starts here.";
    message.textContent =
      "Bring in a long video to get your first set of clip ideas.";
    create.classList.remove("hidden");
  }
  $("#projectGrid").innerHTML = list
    .map(
      (p, i) =>
        `<article class="project-card" data-project="${esc(p.id)}" tabindex="0"><div class="thumb thumb-color-${(i % 3) + 1}"><span class="thumb-label">${p.source_type === "youtube" ? "YOUTUBE SOURCE" : "VIDEO SOURCE"}</span><span class="thumb-play">${p.status === "Ready" ? "▶" : "✳"}</span><span class="thumb-duration">${p.clip_count || 0} clips</span></div><div class="card-body"><h3 class="card-title">${esc(p.name)}</h3><div class="card-meta"><span>${new Date(p.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</span><span class="status-pill ${p.status === "Failed" ? "status-processing" : ""}">${esc(p.status)}</span></div></div></article>`,
    )
    .join("");
  $$(".project-card").forEach((card) => {
    const open = () => openProject(card.dataset.project);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter") open();
    });
  });
}
$("#searchInput").addEventListener("input", () => {
  clearTimeout(searchTimer);
  projectPage = 1;
  searchTimer = setTimeout(load, 250);
});
["projectScope", "projectStatus", "projectSource", "sortOrder"].forEach((id) =>
  $("#" + id).addEventListener("change", () => {
    projectPage = 1;
    load();
  }),
);
$("#projectPrevious").addEventListener("click", () => {
  if (projectPage > 1) {
    projectPage--;
    load();
  }
});
$("#projectNext").addEventListener("click", () => {
  if (projectPage < projectPages) {
    projectPage++;
    load();
  }
});
$("#keyButton").addEventListener("click", () => {
  $("#keyDialog").showModal();
  updateProviderKeyStatus();
});
$("#saveKey").addEventListener("click", async () => {
  const key = $("#apiKeyInput").value.trim();
  try {
    const result = await api("/api/settings/openai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apiKey: key }),
    });
    $("#apiKeyInput").value = "";
    await load();
    toast(
      result.persisted
        ? "Workspace OpenAI key encrypted and saved."
        : "Workspace key connected for this server run. Configure the encryption key to persist it.",
    );
  } catch (e) {
    toast(e.message);
  }
});
$("#removeKey").addEventListener("click", async () => {
  try {
    await api("/api/settings/openai", { method: "DELETE" });
    $("#apiKeyInput").value = "";
    await load();
    toast("Workspace OpenAI key removed.");
  } catch (e) {
    toast(e.message);
  }
});
$("#saveGeminiKey").addEventListener("click", async () => {
  const key = $("#geminiKeyInput").value.trim();
  try {
    const result = await api("/api/settings/gemini", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apiKey: key }),
    });
    $("#geminiKeyInput").value = "";
    await load();
    toast(
      result.persisted
        ? "Workspace Gemini key encrypted and saved."
        : "Workspace Gemini key connected for this server run. Configure the encryption key to persist it.",
    );
  } catch (e) {
    toast(e.message);
  }
});
$("#removeGeminiKey").addEventListener("click", async () => {
  try {
    await api("/api/settings/gemini", { method: "DELETE" });
    $("#geminiKeyInput").value = "";
    await load();
    toast("Workspace Gemini key removed.");
  } catch (e) {
    toast(e.message);
  }
});
$("#saveGroqKey").addEventListener("click", async () => {
  const key = $("#groqKeyInput").value.trim();
  try {
    const result = await api("/api/settings/groq", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apiKey: key }),
    });
    $("#groqKeyInput").value = "";
    await load();
    toast(
      result.persisted
        ? "Workspace Groq key encrypted and saved."
        : "Workspace Groq key connected for this server run. Configure the encryption key to persist it.",
    );
  } catch (e) {
    toast(e.message);
  }
});
$("#removeGroqKey").addEventListener("click", async () => {
  try {
    await api("/api/settings/groq", { method: "DELETE" });
    $("#groqKeyInput").value = "";
    await load();
    toast("Workspace Groq key removed.");
  } catch (e) {
    toast(e.message);
  }
});
async function openProject(id) {
  const dialog = $("#projectDialog");
  dialog.showModal();
  dialog.onpointerdown = (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    ) {
      dialog.close();
    }
  };
  $("#projectDetail").dataset.projectId = "";
  $("#projectDetail").innerHTML =
    '<div class="project-loading">Loading project…</div>';
  await refreshProject(id);
}
function stageLabel(status) {
  return (
    {
      Queued: "Queued for processing",
      "Checking saved source": "Checking for a saved video",
      "Inspecting cached source": "Checking saved video",
      "Connecting to YouTube": "Connecting to YouTube",
      "Downloading source": "Downloading source video",
      "Inspecting source": "Checking downloaded video",
      "Reusing downloaded source": "Reusing saved source",
      Transcribing: "Creating transcript with OpenAI Whisper",
      "Reusing transcript": "Reusing saved transcript",
      "Getting YouTube captions": "Getting YouTube transcript",
      "Finding clips": "Finding clip moments",
      "Finding clip moments": "Finding clip moments",
      "Ranking moments with OpenAI": "Sending transcript to OpenAI",
      "Switching to Gemini": "Switching to Gemini fallback",
      "Ranking moments with Gemini": "Sending transcript to Gemini",
      "Switching to Groq": "Switching to Groq fallback",
      "Ranking moments with Groq": "Sending transcript to Groq",
      "Switching to ChatGPT browser": "Switching to ChatGPT browser fallback",
      "Starting ChatGPT browser": "Opening ChatGPT browser",
      "Opening ChatGPT": "Navigating to ChatGPT",
      "Waiting for ChatGPT input": "Waiting for ChatGPT composer",
      "Waiting for ChatGPT verification": "Complete browser verification",
      "Ranking moments with ChatGPT": "Sending transcript to ChatGPT",
      "Queueing automatic Short renders": "Preparing clip renders",
      "Cancel requested": "Canceling processing",
      Ready: "Clips ready",
      Failed: "Needs attention",
      "Needs upload": "Upload needed",
      "Ready to process": "Ready to process",
    }[status] || status
  );
}
function stageDescription(status) {
  return (
    {
      Queued: "Waiting for a processing worker to pick up this job.",
      "Checking saved source":
        "Checking whether this YouTube video is already saved locally; no network download is running.",
      "Inspecting cached source":
        "Validating the saved local video; no network download is running.",
      "Connecting to YouTube":
        "Requesting video details and selecting formats. The file download has not started yet.",
      "Downloading source": "Downloading and saving the YouTube source video.",
      "Inspecting source":
        "Validating the downloaded video and reading its media properties.",
      "Reusing downloaded source": "Reusing the validated saved video.",
      Transcribing:
        "Preparing audio or sending a chunk to OpenAI Whisper for timestamped transcription.",
      "Reusing transcript":
        "Using the saved transcript rather than paying to transcribe it again.",
      "Getting YouTube captions":
        "Checking caption availability, then retrieving timestamped captions.",
      "Finding clips":
        "Validating the transcript and saving the selected clip moments.",
      "Ranking moments with OpenAI":
        "Sending transcript text to OpenAI for clip ranking.",
      "Switching to Gemini":
        "OpenAI did not complete; switching to the configured Gemini key.",
      "Ranking moments with Gemini":
        "Sending transcript text to Gemini for clip ranking.",
      "Switching to Groq":
        "Earlier API providers did not complete; switching to Groq.",
      "Ranking moments with Groq":
        "Sending transcript text to Groq for clip ranking.",
      "Switching to ChatGPT browser":
        "API providers did not complete or are not configured; switching to the signed-in ChatGPT browser.",
      "Starting ChatGPT browser":
        "Launching the signed-in ChatGPT browser.",
      "Opening ChatGPT": "The browser has launched; navigating to chatgpt.com.",
      "Waiting for ChatGPT input":
        "Waiting for the ChatGPT composer. If sign-in, MFA, or a verification prompt is shown, complete it in the opened browser.",
      "Waiting for ChatGPT verification":
        "A human-verification page is visible. Complete it manually in the opened browser; automated challenge solving is not used.",
      "Ranking moments with ChatGPT":
        "ChatGPT is ready; sending the transcript and clip-ranking prompt.",
      "Queueing automatic Short renders":
        "Preparing the selected clips for automatic MP4 rendering.",
      "Cancel requested":
        "Stopping after the current download or provider request finishes.",
    }[status] ||
    "Processing the video. This status shows the current action, not private model reasoning."
  );
}
function formatWait(seconds) {
  return seconds < 60
    ? seconds + "s"
    : Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
}
function pipelineSteps(project) {
  const isYoutube = project.source_type === "youtube";
  const steps = isYoutube
    ? [
        ["Queued", "Queue"],
        ["Checking saved source", "Check saved"],
        ["Connecting to YouTube", "Connect"],
        ["Downloading source", "Download"],
        ["Getting YouTube captions", "Transcript"],
        ["Finding clips", "Find moments"],
        ["Queueing automatic Short renders", "Prepare clips"],
      ]
    : [
        ["Queued", "Queue"],
        ["Inspecting source", "Inspect"],
        ["Transcribing", "Transcript"],
        ["Finding clips", "Find moments"],
      ];
  const stage = projectProcessingState(project).currentStage;
  let activeIndex = 0;
  if (
    stage === "Checking saved source" ||
    stage === "Inspecting cached source" ||
    stage === "Inspecting source" ||
    stage === "Reusing downloaded source"
  )
    activeIndex = 1;
  else if (stage === "Connecting to YouTube") activeIndex = 2;
  else if (stage === "Downloading source") activeIndex = isYoutube ? 3 : 1;
  else if (
    stage === "Transcribing" ||
    stage === "Reusing transcript" ||
    stage === "Getting YouTube captions"
  )
    activeIndex = isYoutube ? 4 : 2;
  else if (
    [
      "Finding clips",
      "Ranking moments with OpenAI",
      "Switching to Gemini",
      "Ranking moments with Gemini",
      "Switching to Groq",
      "Ranking moments with Groq",
      "Switching to ChatGPT browser",
      "Starting ChatGPT browser",
      "Opening ChatGPT",
      "Waiting for ChatGPT input",
      "Waiting for ChatGPT verification",
      "Ranking moments with ChatGPT",
    ].includes(stage)
  )
    activeIndex = isYoutube ? 5 : 3;
  else if (stage === "Queueing automatic Short renders")
    activeIndex = steps.length - 1;
  else if (stage === "Cancel requested") activeIndex = -1;
  return { steps, activeIndex: Math.min(activeIndex, steps.length - 1) };
}
function projectContentKey(project) {
  return JSON.stringify({
    id: project.id,
    name: project.name,
    duration: project.duration,
    source_url: project.source_url,
    source_name: project.source_name,
    clips: project.clips.map((clip) => [
      clip.id,
      clip.title,
      clip.hook,
      clip.start,
      clip.end,
      clip.score,
      clip.transcript,
      clip.export_path,
      clip.edit_version,
      clip.render_job?.status,
    ]),
  });
}
function projectProcessingState(project) {
  const jobs = project.jobs || [];
  const latest = jobs[0];
  const error =
    project.error || (latest?.status === "Failed" ? latest.error : "");
  const activeJob = error
    ? null
    : jobs.find((job) => job.status === "Queued" || job.status === "Running");
  const failed = !!error || latest?.status === "Failed";
  return {
    activeJob,
    active: !!activeJob,
    currentStage: activeJob?.stage || (failed ? "Failed" : project.status),
    status: failed ? "Failed" : project.status,
    error,
  };
}
function updateProjectStatus(detail, project) {
  const {
    activeJob,
    active,
    currentStage,
    status: projectStatus,
    error: projectError,
  } = projectProcessingState(project);
  const status = detail.querySelector(".detail-status .status-pill");
  if (status) {
    status.textContent = stageLabel(currentStage);
    status.classList.toggle("status-processing", active);
  }
  const button = detail.querySelector("#generateBtn");
  const captionsUnavailable =
    !!projectError &&
    /YouTube captions are disabled for this video/i.test(projectError);
  if (button) {
    button.disabled = active || captionsUnavailable;
    button.innerHTML =
      (captionsUnavailable
        ? "Captions unavailable"
        : projectStatus === "Ready"
          ? "Find new clips"
          : projectStatus === "Failed"
            ? "Retry processing"
            : "Generate clips") + " <span>✦</span>";
  }
  let error = detail.querySelector(".error-box");
  if (projectError) {
    if (!error) {
      error = document.createElement("div");
      error.className = "error-box";
      detail.querySelector(".detail-header")?.after(error);
    }
    error.textContent = projectError;
  } else error?.remove();
  let progress = detail.querySelector(".progress-box");
  if (active && !progress) {
    progress = document.createElement("div");
    progress.className = "progress-box";
    progress.innerHTML =
      '<div class="spinner"></div><div class="progress-content"><b></b><p></p><div class="stage-progress" role="progressbar"><span></span></div><small class="progress-caption"></small><ol class="pipeline-steps"></ol></div>';
    const header = detail.querySelector(".detail-header");
    (detail.querySelector("#jobHistory") || header)?.insertAdjacentElement(
      "afterend",
      progress,
    );
  }
  if (progress) {
    progress.hidden = !active;
    if (active) {
      const queued = activeJob?.status === "Queued";
      const worker = project.worker || {};
      const queuedAge =
        Date.now() - Date.parse(activeJob?.created_at || Date.now());
      const workerOffline =
        queued &&
        !worker.available &&
        (worker.age_seconds > 20 || queuedAge > 20000);
      const spinner = progress.querySelector(".spinner");
      if (spinner) spinner.hidden = workerOffline;
      progress.classList.toggle("worker-offline", workerOffline);
      const label = progress.querySelector("b");
      if (label)
        label.textContent = workerOffline
          ? "Worker is not responding"
          : stageLabel(currentStage) + (queued ? "" : "…");
      const description = progress.querySelector("p");
      const queueMessage = queued
        ? workerOffline
          ? "The job is safely queued, but the processing worker has not checked in. Start or restart Cutroom (or the Docker worker) to resume."
          : worker.available
            ? "Waiting in the processing queue" +
              (activeJob.queue_position
                ? " · position " + activeJob.queue_position
                : "") +
              ". The worker will start it automatically."
            : "Waiting for the processing worker to start."
        : "";
      const downloadPercent =
        active &&
        currentStage === "Downloading source" &&
        Number(activeJob?.progress) > 0
          ? " · " + Number(activeJob.progress) + "% downloaded"
          : "";
      const lastUpdate = Date.parse(
        activeJob?.updated_at || activeJob?.created_at || "",
      );
      const idleSeconds =
        !queued && Number.isFinite(lastUpdate)
          ? Math.max(0, Math.floor((Date.now() - lastUpdate) / 1000))
          : 0;
      let staleMessage = "";
      if (idleSeconds >= 30) {
        if (currentStage === "Connecting to YouTube")
          staleMessage =
            "No YouTube response for " +
            formatWait(idleSeconds) +
            "; video bytes have not started downloading yet. Check YouTube connectivity.";
        else if (currentStage === "Downloading source")
          staleMessage =
            "No download update for " +
            formatWait(idleSeconds) +
            "; waiting on YouTube or the network. The download is not confirmed complete.";
        else if (
          [
            "Getting YouTube captions",
            "Ranking moments with OpenAI",
            "Ranking moments with Gemini",
            "Ranking moments with Groq",
            "Ranking moments with ChatGPT",
            "Waiting for ChatGPT input",
            "Waiting for ChatGPT verification",
            "Transcribing",
          ].includes(currentStage)
        )
          staleMessage =
            "No update for " +
            formatWait(idleSeconds) +
            "; the current provider request is still pending. A failure will be shown if it times out.";
        else
          staleMessage =
            "No progress update for " +
            formatWait(idleSeconds) +
            "; the current operation is still running.";
      }
      if (description)
        description.textContent =
          queueMessage ||
          stageDescription(currentStage) +
            (activeJob?.progress_detail
              ? " · " + activeJob.progress_detail
              : "") +
            downloadPercent +
            (staleMessage ? " · " + staleMessage : "");
      const meter = progress.querySelector(".stage-progress");
      const meterFill = meter?.firstElementChild;
      const determinate = !queued && Number(activeJob?.progress) > 0;
      if (meter) {
        meter.hidden = workerOffline;
        meter.classList.toggle("indeterminate", !determinate && !workerOffline);
        meter.setAttribute(
          "aria-label",
          stageLabel(currentStage) + " progress",
        );
        if (determinate) {
          meter.setAttribute("aria-valuemin", "0");
          meter.setAttribute("aria-valuemax", "100");
          meter.setAttribute("aria-valuenow", String(activeJob.progress));
        } else {
          meter.removeAttribute("aria-valuemin");
          meter.removeAttribute("aria-valuemax");
          meter.removeAttribute("aria-valuenow");
        }
      }
      if (meterFill)
        meterFill.style.width =
          (determinate
            ? Math.max(0, Math.min(100, Number(activeJob.progress) || 0))
            : 0) + "%";
      const caption = progress.querySelector(".progress-caption");
      if (caption)
        caption.textContent = workerOffline
          ? "You can close this window; your queued job and downloaded source are preserved."
          : determinate
            ? "Current step: " +
              activeJob.progress +
              "%" +
              (activeJob.attempts
                ? " · attempt " +
                  activeJob.attempts +
                  " of " +
                  activeJob.max_attempts
                : "")
            : queued
              ? "Queued jobs do not use a processing attempt until a worker starts them."
              : "Progress is for the current step, not the whole pipeline.";
      const { steps, activeIndex } = pipelineSteps(project);
      const stepList = progress.querySelector(".pipeline-steps");
      if (stepList) {
        stepList.replaceChildren(
          ...steps.map((step, index) => {
            const item = document.createElement("li");
            item.textContent = step[1];
            item.className =
              activeIndex >= 0 && index < activeIndex
                ? "complete"
                : index === activeIndex
                  ? "current"
                  : "upcoming";
            if (index === activeIndex)
              item.setAttribute("aria-current", "step");
            return item;
          }),
        );
      }
    }
  }
}
function acceptProjectJobSnapshot(detail, project) {
  const latest = (project.jobs || [])[0];
  const projectId = project.id || "";
  if (detail.dataset.snapshotProjectId !== projectId) {
    detail.dataset.snapshotProjectId = projectId;
    detail.dataset.snapshotJobId = "";
    detail.dataset.snapshotJobUpdatedAt = "";
  }
  const updatedAt = latest?.updated_at || latest?.created_at || "";
  if (
    latest &&
    detail.dataset.snapshotJobId === latest.id &&
    updatedAt < detail.dataset.snapshotJobUpdatedAt
  )
    return false;
  detail.dataset.snapshotJobId = latest?.id || "";
  detail.dataset.snapshotJobUpdatedAt = updatedAt;
  return true;
}
window.acceptProjectJobSnapshot = acceptProjectJobSnapshot;
async function refreshProject(id) {
  try {
    const { project: p } = await api(`/api/projects/${id}`);
    const detail = $("#projectDetail");
    if (!acceptProjectJobSnapshot(detail, p)) return;
    detail._projectSnapshot = p;
    const contentKey = projectContentKey(p);
    if (
      detail.dataset.projectId === id &&
      detail.dataset.contentKey === contentKey
    ) {
      updateProjectStatus(detail, p);
      return;
    }
    const active = [
      "Queued",
      "Downloading source",
      "Inspecting source",
      "Reusing downloaded source",
      "Transcribing",
      "Transcribing audio",
      "Getting YouTube captions",
      "Finding clips",
      "Ranking moments with OpenAI",
      "Switching to Gemini",
      "Ranking moments with Gemini",
      "Switching to Groq",
      "Ranking moments with Groq",
      "Switching to ChatGPT browser",
      "Starting ChatGPT browser",
      "Opening ChatGPT",
      "Waiting for ChatGPT input",
      "Waiting for ChatGPT verification",
      "Ranking moments with ChatGPT",
      "Queueing automatic Short renders",
    ].includes(p.status);
    detail.innerHTML = `<div class="detail-header"><div><div class="eyebrow">PROJECT WORKSPACE</div><h2>${esc(p.name)}</h2><div class="detail-status"><span class="status-pill ${active ? "status-processing" : ""}">${esc(stageLabel(p.status))}</span>${p.duration ? `<span>${formatTime(p.duration)} source</span>` : ""}</div></div><button class="primary-button" id="generateBtn" ${active ? "disabled" : ""}>${p.status === "Ready" ? "Find new clips" : "Generate clips"} <span>✦</span></button></div>${p.error ? `<div class="error-box">${esc(p.error)}</div>` : ""}<div class="detail-layout"><section><div class="detail-section-head"><h3>${p.clips.length} clip ideas</h3><span>Ranked by AI · review before export</span></div><div class="clip-list">${p.clips.length ? p.clips.map((c, i) => `<article class="clip-row"><div class="clip-number">${String(i + 1).padStart(2, "0")}</div><div class="clip-info"><h4>${esc(c.title)}</h4><p>${esc(c.hook || c.transcript)}</p><div class="clip-meta"><span>${formatTime(c.start)} – ${formatTime(c.end)}</span><span>✦ ${c.score} score</span></div>${c.export_path ? `<div class="clip-workspace-preview" hidden><span>CLIP PREVIEW</span><video controls playsinline preload="metadata" src="/api/clips/${encodeURIComponent(c.id)}/preview?v=${encodeURIComponent(c.edit_version || 0)}" aria-label="Preview of ${esc(c.title)}"></video></div>` : c.render_job && ["Queued", "Running"].includes(c.render_job.status) ? `<div class="clip-workspace-preview-pending">Preview will appear when clip rendering completes.</div>` : ""}</div><div class="clip-actions">${c.export_path ? `<span class="clip-downloads"><button class="clip-edit" data-download="${c.id}">MP4 ↓</button></span><button class="clip-edit" type="button" data-preview-clip="${c.id}">Preview</button><button class="clip-edit" data-clip="${c.id}">Edit</button>` : `<button class="clip-edit" data-clip="${c.id}">Edit & export →</button>`}<button class="clip-edit clip-delete-button" data-delete-clip="${c.id}">Delete</button></div></article>`).join("") : '<div class="no-clips">' + (p.status === "Failed" ? "Check the error above, update settings or source, then retry." : "Generate clips to see AI-selected moments here.") + "</div>"}</div></section><aside class="source-panel"><div class="source-panel-title">SOURCE VIDEO</div><video controls preload="metadata" src="/api/projects/${p.id}/source"></video><div class="source-name">${esc(p.source_url || p.source_name || "Source not uploaded")}</div><div class="source-hint">Edit a clip to set exact in and out points. Exported MP4 keeps the original audio.</div></aside></div>`;
    detail.dataset.projectId = id;
    detail.dataset.contentKey = contentKey;
    updateProjectStatus(detail, p);
    $("#generateBtn")?.addEventListener("click", async () => {
      try {
        await api(`/api/projects/${id}/generate`, { method: "POST" });
        await load();
        await refreshProject(id);
      } catch (e) {
        if (e.message.toLowerCase().includes("api key"))
          $("#keyDialog").showModal();
        else toast(e.message);
      }
    });
    $$(".clip-edit[data-clip]").forEach((b) =>
      b.addEventListener("click", () => {
        const c = p.clips.find((x) => x.id === b.dataset.clip);
        openClipEditor(p, c);
      }),
    );
    $$("[data-download]").forEach((b) =>
      b.addEventListener("click", () => {
        location.href = `/api/clips/${b.dataset.download}/download`;
      }),
    );
    $$("[data-preview-clip]").forEach((b) =>
      b.addEventListener("click", () => {
        const preview = b.closest(".clip-row")?.querySelector(".clip-workspace-preview");
        if (!preview) return;
        const video = $("video", preview);
        const showing = preview.hidden;
        preview.hidden = !showing;
        b.textContent = showing ? "Hide preview" : "Preview";
        if (!showing) video.pause();
      }),
    );
    $$("[data-delete-clip]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!window.confirm("Permanently delete this clip and its rendered video? This cannot be undone.")) return;
        b.disabled = true;
        try {
          await api(`/api/clips/${encodeURIComponent(b.dataset.deleteClip)}`, { method: "DELETE" });
          toast("Clip deleted.");
          await refreshProject(id);
        } catch (e) {
          toast(e.message);
          b.disabled = false;
        }
      }),
    );
  } catch (e) {
    $("#projectDetail").innerHTML =
      `<div class="error-box">${esc(e.message)}</div>`;
  }
}
function openClipEditor(project, clip) {
  const overlay = document.createElement("div");
  overlay.className = "editor-overlay";
  overlay.innerHTML = `<div class="clip-editor"><button class="close-modal" id="closeClip">×</button><div class="eyebrow">CLIP REVIEW</div><h2>${esc(clip.title)}</h2><video controls preload="metadata" src="/api/projects/${project.id}/source" id="editorVideo"></video><label class="field-label">Clip title</label><input class="field-input" id="editTitle" value="${esc(clip.title)}"><div class="time-fields"><label>In point (seconds)<input type="number" id="editStart" min="0" step="0.1" value="${clip.start.toFixed(1)}"></label><label>Out point (seconds)<input type="number" id="editEnd" min="0" step="0.1" value="${clip.end.toFixed(1)}"></label></div><div class="editor-transcript">${esc(clip.transcript || "Transcript text unavailable.")}</div><div class="dialog-foot"><span>Export MP4 with original audio · SRT available separately</span><button class="primary-button" id="exportClip">Export clip <span>↓</span></button></div></div>`;
  document.body.appendChild(overlay);
  const video = $("#editorVideo", overlay);
  video.addEventListener("loadedmetadata", () => {
    video.currentTime = clip.start;
  });
  $("#closeClip", overlay).addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  $("#exportClip", overlay).addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Rendering…";
    try {
      const x = await api(`/api/clips/${clip.id}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: Number($("#editStart", overlay).value),
          end: Number($("#editEnd", overlay).value),
          title: $("#editTitle", overlay).value,
        }),
      });
      overlay.remove();
      await refreshProject(project.id);
      const dl = document.createElement("a");
      dl.href = x.download;
      dl.download = "";
      dl.click();
      toast("Clip exported. Downloading MP4.");
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
      btn.innerHTML = "Export clip <span>↓</span>";
    }
  });
}
$("#upgradeBtn").addEventListener("click", () =>
  toast("This local build has no billing plan."),
);
$$(".nav-item").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.dataset.view === "automation") {
      window.showAutomation?.();
      return;
    }
    if (b.dataset.view === "projects") {
      window.showProjects?.();
      return;
    }
    if (b.dataset.view === "youtube-dashboard") {
      window.showYoutubeDashboard?.();
      return;
    }
    toast(`${b.textContent.trim()} will be added after the clip workflow.`);
  }),
);
load();
