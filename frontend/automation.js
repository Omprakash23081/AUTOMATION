(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = (value = "") =>
    String(value).replace(/[&<>"']/g, (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[character],
    );
  const formatBytes = (bytes) => {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  };
  let scannedFiles = [];
  let refreshTimer;
  let loading = false;

  async function request(url, options) {
    const response = await fetch(url, options);
    let data = {};
    try {
      data = await response.json();
    } catch {}
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function showMessage(message, isError = false) {
    const element = $("#automationMessage");
    element.textContent = message;
    element.classList.toggle("automation-error", isError);
    element.classList.remove("hidden");
  }

  function showProjects() {
    $("#projectsView").classList.remove("hidden");
    $("#automationView").classList.add("hidden");
    $("#youtubeView")?.classList.add("hidden");
    $("#crumbTitle").textContent = "Projects";
    $$(".nav-item").forEach((button) =>
      button.classList.toggle("active", button.dataset.view === "projects"),
    );
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = undefined;
  }

  function showAutomation() {
    $("#projectsView").classList.add("hidden");
    $("#youtubeView")?.classList.add("hidden");
    $("#moduleView")?.classList.add("hidden");
    $("#automationView").classList.remove("hidden");
    $("#crumbTitle").textContent = "Automation";
    $$(".nav-item").forEach((button) =>
      button.classList.toggle("active", button.dataset.view === "automation"),
    );
    updateWorkflowFields();
    loadAutomation();
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      if (!$("#automationView").classList.contains("hidden")) loadAutomation();
    }, 5000);
  }

  function hideAutomation() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = undefined;
  }

  function updateWorkflowFields() {
    const workflow = $("#automationWorkflow").value;
    $$("[data-workflow-fields]").forEach((fields) => {
      fields.classList.toggle("hidden", !fields.dataset.workflowFields.split(/\s+/).includes(workflow));
    });
    const needsAuthorization = ["kuaishou", "youtube_batch"].includes(workflow);
    $("#automationAuthorized").checked = false;
    $("#automationAuthorized").closest(".automation-authorize").classList.toggle("hidden", !needsAuthorization);
    const needsTranscript = workflow === "english_translation";
    $("#translationFileLabel").classList.toggle("hidden", !needsTranscript);
    $("#automationTranscriptFile").classList.toggle("hidden", !needsTranscript);
    $("#automationReferenceFiles").classList.toggle("hidden", workflow === "cartesia_tts");
    $("#youtubeCustomVoiceField").classList.toggle(
      "hidden",
      workflow !== "youtube_batch" || $("#youtubeCartesiaVoice").value !== "other",
    );
  }

  function buildJobPayload() {
    const workflow = $("#automationWorkflow").value;
    const payload = {
      workflow,
      timeoutMinutes: Number($("#automationTimeout").value),
    };
    if (workflow === "kuaishou") {
      payload.mode = Number($("#kuaishouMode").value);
      payload.videoTitle = $("#kuaishouVideoTitle").value.trim();
      payload.startPart = Number($("#kuaishouStartPart").value);
      payload.urls = $("#kuaishouUrls").value;
      payload.targetFolder = $("#kuaishouTargetFolder").value.trim();
      payload.authorized = $("#automationAuthorized").checked;
    } else if (workflow === "youtube_batch") {
      payload.urls = $("#youtubeBatchUrls").value;
      payload.targetFolder = $("#youtubeTargetFolder").value.trim();
      payload.language = $("#youtubeTranscriptLanguage").value;
      payload.namePrefix = $("#youtubeNamePrefix").value.trim();
      payload.startNumber = Number($("#youtubeStartNumber").value);
      payload.steps = $$('input[name="youtubePipelineStep"]:checked').map((input) => input.value);
      payload.voiceCharacter = $("#youtubeCartesiaVoice").value;
      if (payload.voiceCharacter === "other") {
        payload.voiceId = $("#youtubeCustomCartesiaVoiceId").value.trim();
      }
      payload.authorized = $("#automationAuthorized").checked;
    } else if (workflow === "video_edit_sync") {
      payload.videoFile = $("#videoEditVideoFile").files[0] || null;
      payload.audioFile = $("#videoEditAudioFile").files[0] || null;
      payload.transcriptFile = $("#videoEditTranscriptFile").files[0] || null;
    } else if (["gemini_transcribe", "chatgpt_hinglish", "whisper", "english_translation"].includes(workflow)) {
      payload.sourceFolder = $("#automationSourceFolder").value.trim();
      if (workflow === "english_translation") payload.sourceFile = $("#automationTranscriptFile").value;
    } else if (workflow === "cartesia_tts") {
      payload.text = $("#cartesiaText").value;
    }
    return payload;
  }

  function renderReferences() {
    const list = $("#automationReferenceFiles");
    if (!scannedFiles.length) {
      list.innerHTML = "";
      $("#automationTranscriptFile").innerHTML = "";
      return;
    }
    list.innerHTML = `<div class="automation-reference-title">Read-only folder scan · ${scannedFiles.length} files</div>${scannedFiles
      .map((file) => `<div class="automation-reference-item"><span>${escapeHtml(file.name)}</span><small>${escapeHtml(file.type.toUpperCase())} · ${formatBytes(file.bytes)}</small></div>`)
      .join("")}`;
    const transcripts = scannedFiles.filter((file) => file.type === "txt");
    $("#automationTranscriptFile").innerHTML = transcripts
      .map((file) => `<option value="${escapeHtml(file.name)}">${escapeHtml(file.name)}</option>`)
      .join("");
  }

  async function renderJobs(jobs) {
    const container = $("#automationJobs");
    const expandedLogJobIds = new Set(
      $$("details.automation-log[open]", container).map((details) => details.dataset.jobId),
    );
    if (!jobs.length) {
      container.innerHTML = '<div class="automation-empty">No automation jobs yet.</div>';
      return;
    }
    const cards = await Promise.all(
      jobs.slice(0, 10).map(async (job) => {
        let files = [];
        if (job.status === "Succeeded") {
          try {
            files = (await request(`/api/automation/jobs/${encodeURIComponent(job.id)}/files`)).files;
          } catch {
            files = [];
          }
        }
        const outputLinks = files
          .map((file) => `<a class="automation-output-link" href="${escapeHtml(file.download)}">${escapeHtml(file.name)} <small>${formatBytes(file.bytes)} · Download</small></a>`)
          .join("");
        const log = [
          job.error ? `Error: ${job.error}` : "",
          job.logs ? `Process logs:\n${job.logs}` : "",
        ].filter(Boolean).join("\n\n");
        const downloadProgressLines = (job.logs || "")
          .split("\n")
          .filter((line) => line.includes(" download progress:"));
        const latestDownloadProgress = downloadProgressLines[downloadProgressLines.length - 1];
        return `<article class="automation-job">
          <div class="automation-job-heading"><div><b>${escapeHtml(job.workflow.replaceAll("_", " "))}</b><small>${escapeHtml(job.created_at)}</small></div><span class="automation-job-status status-${escapeHtml(job.status.toLowerCase())}">${escapeHtml(job.status)}</span></div>
          <div class="automation-job-stage">${escapeHtml(job.stage)} · ${Number(job.progress) || 0}%</div>
          ${latestDownloadProgress ? `<div class="automation-job-stage automation-download-progress">Latest transfer: ${escapeHtml(latestDownloadProgress)}</div>` : ""}
          <div class="automation-progress"><span style="width:${Math.min(100, Math.max(0, Number(job.progress) || 0))}%"></span></div>
          ${log ? `<details class="automation-log" data-job-id="${escapeHtml(job.id)}"><summary>${job.error ? "Show error and logs" : "Show logs"}</summary><pre>${escapeHtml(log)}</pre></details>` : ""}
          ${outputLinks ? `<div class="automation-output-list">${outputLinks}</div>` : ""}
        </article>`;
      }),
    );
    container.innerHTML = cards.join("");
    $$("details.automation-log", container).forEach((details) => {
      details.open = expandedLogJobIds.has(details.dataset.jobId);
    });
  }

  async function loadAutomation() {
    if (loading) return;
    loading = true;
    try {
      const result = await request("/api/automation");
      const runnerAvailable = result.sourceAvailable && result.jobsAvailable;
      $("#automationSourceStatus").textContent = !result.sourceAvailable
        ? "Automation source unavailable"
        : result.jobsAvailable
          ? "Automation source ready"
          : "Jobs require Cutroom local combined mode";
      $("#automationSourceStatus").classList.toggle("automation-status-error", !runnerAvailable);
      const credential = result.credentials;
      if (document.activeElement !== $("#automationApiUrl")) {
        $("#automationApiUrl").value = credential.apiUrl || "";
      }
      $("#automationCredentialStatus").textContent = result.credentialsError
        ? result.credentialsError
        : `Kuaishou URL: ${credential.kuaishouUrlConfigured ? "saved" : "not saved"} · API key: ${credential.kuaishouKeyConfigured ? "saved" : "required"} · Cartesia: ${credential.cartesiaConfigured ? "connected" : "not connected"}`;
      $("#saveAutomationSettings").disabled = !result.canManageCredentials;
      if (!result.canManageCredentials) {
        $("#automationCredentialStatus").textContent += " · Only workspace admins can update credentials.";
      }
      $("#startAutomation").disabled = !runnerAvailable;
      await renderJobs(result.jobs);
    } catch (error) {
      $("#automationSourceStatus").textContent = error.message;
      $("#automationSourceStatus").classList.add("automation-status-error");
    } finally {
      loading = false;
    }
  }

  $("#automationWorkflow").addEventListener("change", updateWorkflowFields);
  $("#youtubeCartesiaVoice").addEventListener("change", updateWorkflowFields);
  $("#scanAutomationFolder").addEventListener("click", async () => {
    const button = $("#scanAutomationFolder");
    button.disabled = true;
    try {
      const result = await request("/api/automation/references", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: $("#automationSourceFolder").value }),
      });
      scannedFiles = result.files;
      renderReferences();
      showMessage(`Scanned ${scannedFiles.length} file(s) in ${result.folder}. Existing files were not changed.`);
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("#saveAutomationSettings").addEventListener("click", async () => {
    const button = $("#saveAutomationSettings");
    button.disabled = true;
    try {
      const result = await request("/api/automation/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          apiUrl: $("#automationApiUrl").value,
          apiKey: $("#automationApiKey").value,
          cartesiaApiKey: $("#automationCartesiaKey").value,
        }),
      });
      $("#automationApiKey").value = "";
      $("#automationCartesiaKey").value = "";
      showMessage("Automation credentials saved. API keys are encrypted for this workspace.");
      await loadAutomation();
      $("#automationCredentialStatus").textContent = `Kuaishou URL: ${result.kuaishouUrlConfigured ? "saved" : "not saved"} · API key: ${result.kuaishouKeyConfigured ? "saved" : "required"} · Cartesia: ${result.cartesiaConfigured ? "connected" : "not connected"}`;
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("#startAutomation").addEventListener("click", async () => {
    const button = $("#startAutomation");
    button.disabled = true;
    try {
      const payload = buildJobPayload();
      if (payload.workflow === "youtube_batch" && !payload.steps.length) {
        throw new Error("Select at least one YouTube batch step.");
      }
      let body = JSON.stringify(payload);
      const headers = { "Content-Type": "application/json" };
      if (payload.workflow === "video_edit_sync") {
        if (!payload.videoFile || !payload.audioFile || !payload.transcriptFile) {
          throw new Error("Upload the Chinese video, Hindi audio, and timestamped Hindi transcript.");
        }
        body = new FormData();
        body.append("workflow", payload.workflow);
        body.append("timeoutMinutes", String(payload.timeoutMinutes));
        body.append("video", payload.videoFile);
        body.append("audio", payload.audioFile);
        body.append("transcript", payload.transcriptFile);
        delete headers["Content-Type"];
      }
      const result = await request("/api/automation/jobs", {
        method: "POST",
        headers,
        body,
      });
      showMessage("Automation job queued. Progress and output files will appear below.");
      await loadAutomation();
    } catch (error) {
      showMessage(error.message, true);
      button.disabled = false;
    }
  });
  $("#refreshAutomationJobs").addEventListener("click", loadAutomation);
  window.showAutomation = showAutomation;
  window.showProjects = showProjects;
  window.hideAutomation = hideAutomation;
  updateWorkflowFields();
})();
