(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
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
  const numberFormat = (value) =>
    value === null || value === undefined
      ? "Hidden"
      : new Intl.NumberFormat().format(Number(value) || 0);
  let channels = [];
  let selectedChannelId = null;
  let channelSort = "subscribers";
  let uploadCaption = "";
  let uploadDescriptionIsDefault = true;

  function youtubeShortTitle(title) {
    const cleanTitle = String(title || "").trim();
    if (/(?:^|\s)#shorts\b/i.test(cleanTitle)) {
      return cleanTitle.slice(0, 100);
    }
    const suffix = " #shorts";
    return `${cleanTitle.slice(0, 100 - suffix.length).trimEnd()}${suffix}`;
  }

  function cleanUploadCaption(caption) {
    const lines = String(caption || "").split(/\r?\n/);
    const timestampLine =
      /^\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?/;
    return lines
      .filter((line, index) => {
        if (/^\s*WEBVTT\s*$/.test(line) || timestampLine.test(line)) return false;
        return !(/^\s*\d+\s*$/.test(line) && timestampLine.test(lines[index + 1] || ""));
      })
      .join("\n")
      .trim();
  }

  function defaultUploadDescription(title) {
    const taggedTitle = youtubeShortTitle(title);
    return [
      taggedTitle,
      cleanUploadCaption(uploadCaption),
      "#Shorts #Viral #Trending",
    ]
      .filter(Boolean)
      .join("\n\n");
  }

  function showError(message) {
    const error = $("#youtubeError");
    error.textContent = message;
    error.classList.remove("hidden");
  }

  function showYoutubeDashboard() {
    $("#projectsView")?.classList.add("hidden");
    $("#automationView")?.classList.add("hidden");
    $("#youtubeView")?.classList.remove("hidden");
    $("#moduleView")?.classList.add("hidden");
    $("#crumbTitle").textContent = "YouTube dashboard";
    document.querySelectorAll(".nav-item").forEach((button) =>
      button.classList.toggle(
        "active",
        button.dataset.view === "youtube-dashboard",
      ),
    );
    window.hideAutomation?.();
    return loadChannels();
  }

  function renderChannels() {
    $("#youtubeChannelCount").textContent = numberFormat(channels.length);
    const visibleSubscriberCounts = channels.filter(
      (channel) => channel.subscribers !== null,
    );
    $("#youtubeSubscriberTotal").textContent =
      channels.length === 0
        ? "—"
        : visibleSubscriberCounts.length === 0
          ? "Hidden"
          : numberFormat(
              visibleSubscriberCounts.reduce(
                (total, channel) => total + Number(channel.subscribers || 0),
                0,
              ),
            );
    $("#youtubeViewTotal").textContent = numberFormat(
      channels.reduce((total, channel) => total + Number(channel.views || 0), 0),
    );
    $("#youtubeVideoTotal").textContent = numberFormat(
      channels.reduce((total, channel) => total + Number(channel.video_count || 0), 0),
    );
    $("#youtubeEmpty").classList.toggle("hidden", channels.length > 0);
    const sortedChannels = [...channels].sort((left, right) => {
      if (channelSort === "name") {
        return left.title.localeCompare(right.title);
      }
      const field =
        channelSort === "views"
          ? "views"
          : channelSort === "videos"
            ? "video_count"
            : "subscribers";
      return Number(right[field] || 0) - Number(left[field] || 0);
    });
    if (!channels.some((channel) => channel.channel_id === selectedChannelId)) {
      selectedChannelId = sortedChannels[0]?.channel_id || null;
    }
    const selectedChannel = channels.find(
      (channel) => channel.channel_id === selectedChannelId,
    );
    const bannerUrl = selectedChannel?.banner_url?.startsWith("https://")
      ? selectedChannel.banner_url
      : "";
    $("#youtubeChannelPicker").innerHTML = sortedChannels
      .map(
        (channel) => `
          <button class="youtube-channel-choice${channel.channel_id === selectedChannelId ? " is-selected" : ""}" type="button" data-select-channel="${escapeHtml(channel.channel_id)}" aria-pressed="${channel.channel_id === selectedChannelId}">
            <span class="youtube-channel-ring">
              ${
                channel.thumbnail_url
                  ? `<img class="youtube-channel-avatar" src="${escapeHtml(channel.thumbnail_url)}" alt="" referrerpolicy="no-referrer">`
                  : '<span class="youtube-channel-avatar youtube-channel-placeholder">▶</span>'
              }
            </span>
            <span class="youtube-channel-choice-info"><strong>${escapeHtml(channel.title)}</strong></span>
          </button>`,
      )
      .join("");
    $("#youtubeChannels").innerHTML = selectedChannel
      ? `
          <article class="youtube-channel-card" data-channel-id="${escapeHtml(selectedChannel.channel_id)}">
            <div class="youtube-channel-banner${bannerUrl ? " has-banner" : ""}">
              ${bannerUrl ? `<img src="${escapeHtml(bannerUrl)}" alt="" referrerpolicy="no-referrer" loading="lazy">` : ""}
              <div class="youtube-channel-top">
                <div class="youtube-channel-identity">
                  ${
                    selectedChannel.thumbnail_url
                      ? `<img class="youtube-channel-avatar" src="${escapeHtml(selectedChannel.thumbnail_url)}" alt="" referrerpolicy="no-referrer">`
                      : '<div class="youtube-channel-avatar youtube-channel-placeholder">▶</div>'
                  }
                  <div><span class="youtube-channel-account">${escapeHtml(selectedChannel.email)}</span><h3>${escapeHtml(selectedChannel.title)}</h3><span class="youtube-channel-updated">Updated ${escapeHtml(new Date(selectedChannel.updated_at).toLocaleString())}</span></div>
                </div>
                <button class="youtube-channel-menu" type="button" data-disconnect="${escapeHtml(selectedChannel.connection_id)}" title="Disconnect Google account">Disconnect account</button>
              </div>
            </div>
            <div class="youtube-channel-metrics">
              <div><span>SUBSCRIBERS</span><b>${numberFormat(selectedChannel.subscribers)}</b></div>
              <div><span>CHANNEL VIEWS</span><b>${numberFormat(selectedChannel.views)}</b></div>
              <div><span>VIDEOS</span><b>${numberFormat(selectedChannel.video_count)}</b></div>
            </div>
            <div class="youtube-channel-actions">
              <button class="secondary-button" type="button" data-videos="${escapeHtml(selectedChannel.channel_id)}">Recent videos</button>
              <button class="secondary-button" type="button" data-refresh-channel="${escapeHtml(selectedChannel.channel_id)}">Refresh stats</button>
              <button class="primary-button youtube-upload-button" type="button" data-upload="${escapeHtml(selectedChannel.channel_id)}">Upload / schedule</button>
            </div>
            <div class="youtube-video-list hidden" id="youtubeVideos-${escapeHtml(selectedChannel.channel_id)}"></div>
          </article>`
      : "";
    $("#youtubeUploadChannel").innerHTML = channels
      .map(
        (channel) =>
          `<option value="${escapeHtml(channel.channel_id)}">${escapeHtml(channel.title)} · ${escapeHtml(channel.email)}</option>`,
      )
      .join("");
  }

  async function loadChannels() {
    const list = $("#youtubeChannels");
    list.innerHTML = '<div class="youtube-loading">Loading connected channels…</div>';
    $("#youtubeError").classList.add("hidden");
    try {
      const data = await api("/api/youtube/channels");
      channels = data.channels || [];
      const setup = $("#youtubeSetupNotice");
      if (!data.configured) {
        setup.textContent =
          `Google OAuth is not configured yet. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env. Register this exact redirect URI in Google Cloud: ${data.redirect_uri}`;
      } else if (!data.can_connect) {
        setup.textContent =
          `Set CUTROOM_SECRET_ENCRYPTION_KEY in .env before connecting. In Google Cloud, register this exact redirect URI: ${data.redirect_uri}`;
      } else {
        setup.textContent =
          `Google OAuth callback URI: ${data.redirect_uri} — if Google shows redirect_uri_mismatch, add this exact URI under your OAuth client's Authorized redirect URIs.`;
      }
      $("#connectYoutube").disabled = !data.can_connect;
      $("#connectYoutubeEmpty").disabled = !data.can_connect;
      renderChannels();
    } catch (error) {
      list.innerHTML = "";
      showError(error.message);
    }
  }

  async function connectYoutube() {
    window.location.assign("/api/youtube/connect");
  }

  async function loadVideos(channelId, button) {
    const panel = $(`#youtubeVideos-${CSS.escape(channelId)}`);
    if (!panel) return;
    const opening = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !opening);
    if (!opening) return;
    button.disabled = true;
    button.textContent = "Loading videos…";
    panel.innerHTML = '<div class="youtube-loading">Loading recent uploads…</div>';
    try {
      const data = await api(
        `/api/youtube/channels/${encodeURIComponent(channelId)}/videos`,
      );
      if (!data.videos.length) {
        panel.innerHTML = '<p class="youtube-video-empty">No uploaded videos were found on this channel.</p>';
      } else {
        panel.innerHTML = data.videos
          .map(
            (video) => `
              <div class="youtube-video-row">
                ${video.thumbnail ? `<img src="${escapeHtml(video.thumbnail)}" alt="" loading="lazy">` : ""}
                <div class="youtube-video-info"><a href="${escapeHtml(video.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(video.title)}</a><span>${video.published_at ? escapeHtml(new Date(video.published_at).toLocaleDateString()) : "Date unavailable"} · ${numberFormat(video.views)} views · ${escapeHtml(video.privacy_status)}</span></div>
                <button class="youtube-make-shorts" type="button" data-create-clips="${escapeHtml(video.url)}" data-video-title="${escapeHtml(video.title)}">Create clips</button>
              </div>`,
          )
          .join("");
      }
      button.textContent = "Hide videos";
    } catch (error) {
      panel.innerHTML = `<p class="youtube-video-empty youtube-inline-error">${escapeHtml(error.message)}</p>`;
      button.textContent = "Retry recent videos";
    } finally {
      button.disabled = false;
    }
  }

  async function refreshChannel(channelId, button) {
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "Refreshing…";
    try {
      await api(`/api/youtube/channels/${encodeURIComponent(channelId)}/refresh`, {
        method: "POST",
      });
      await loadChannels();
    } catch (error) {
      showError(error.message);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  function openUpload(channelId, file = null, title = "", caption = "") {
    $("#youtubeUploadForm").reset();
    $("#youtubeUploadChannel").value = channelId;
    uploadCaption = cleanUploadCaption(caption);
    uploadDescriptionIsDefault = true;
    if (title) $("#youtubeUploadTitle").value = youtubeShortTitle(title);
    $("#youtubeUploadDescription").value = defaultUploadDescription(title);
    if (file) {
      const files = new DataTransfer();
      files.items.add(file);
      $("#youtubeUploadFile").files = files.files;
    }
    $("#youtubeUploadError").classList.add("hidden");
    $("#youtubeUploadDialog").showModal();
  }

  $("#youtubeUploadTitle").addEventListener("input", () => {
    if (uploadDescriptionIsDefault) {
      $("#youtubeUploadDescription").value = defaultUploadDescription(
        $("#youtubeUploadTitle").value,
      );
    }
  });

  $("#youtubeUploadTitle").addEventListener("blur", () => {
    const title = $("#youtubeUploadTitle");
    if (title.value.trim()) {
      title.value = youtubeShortTitle(title.value);
      if (uploadDescriptionIsDefault) {
        $("#youtubeUploadDescription").value = defaultUploadDescription(title.value);
      }
    }
  });

  $("#youtubeUploadDescription").addEventListener("input", () => {
    uploadDescriptionIsDefault = false;
  });

  async function disconnect(connectionId, button) {
    if (!window.confirm("Disconnect this Google account and remove its channels from the dashboard?")) return;
    button.disabled = true;
    try {
      await api(`/api/youtube/connections/${encodeURIComponent(connectionId)}`, {
        method: "DELETE",
      });
      await loadChannels();
    } catch (error) {
      showError(error.message);
      button.disabled = false;
    }
  }

  $("#youtubeChannels").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.videos) await loadVideos(button.dataset.videos, button);
    if (button.dataset.refreshChannel)
      await refreshChannel(button.dataset.refreshChannel, button);
    if (button.dataset.upload) openUpload(button.dataset.upload);
    if (button.dataset.disconnect)
      await disconnect(button.dataset.disconnect, button);
    if (button.dataset.createClips) {
      window.dispatchEvent(
        new CustomEvent("cutroom:create-project-from-youtube", {
          detail: {
            url: button.dataset.createClips,
            title: button.dataset.videoTitle,
          },
        }),
      );
    }
  });

  $("#youtubeChannelPicker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-select-channel]");
    if (!button) return;
    selectedChannelId = button.dataset.selectChannel;
    renderChannels();
  });

  $("#youtubeChannelSort").addEventListener("change", (event) => {
    channelSort = event.target.value;
    renderChannels();
  });

  $("#connectYoutube").addEventListener("click", connectYoutube);
  $("#connectYoutubeEmpty").addEventListener("click", connectYoutube);
  $("#refreshYoutube").addEventListener("click", async () => {
    const button = $("#refreshYoutube");
    button.disabled = true;
    button.textContent = "Refreshing…";
    try {
      for (const channel of channels) {
        await api(
          `/api/youtube/channels/${encodeURIComponent(channel.channel_id)}/refresh`,
          { method: "POST" },
        );
      }
      await loadChannels();
    } catch (error) {
      showError(error.message);
      await loadChannels();
    } finally {
      button.disabled = false;
      button.textContent = "Refresh overview";
    }
  });

  $("#youtubeUploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#youtubeUploadSubmit");
    const file = $("#youtubeUploadFile").files[0];
    if (!file) return;
    const channelId = $("#youtubeUploadChannel").value;
    const titleInput = $("#youtubeUploadTitle");
    titleInput.value = youtubeShortTitle(titleInput.value);
    if (uploadDescriptionIsDefault) {
      $("#youtubeUploadDescription").value = defaultUploadDescription(
        titleInput.value,
      );
    }
    const body = new FormData();
    body.append("file", file, file.name);
    body.append("title", titleInput.value);
    body.append("description", $("#youtubeUploadDescription").value.trim());
    body.append("privacyStatus", $("#youtubeUploadPrivacy").value);
    const scheduled = $("#youtubePublishAt").value;
    if (scheduled) body.append("publishAt", new Date(scheduled).toISOString());
    button.disabled = true;
    button.textContent = "Uploading…";
    $("#youtubeUploadError").classList.add("hidden");
    try {
      const result = await api(
        `/api/youtube/channels/${encodeURIComponent(channelId)}/upload`,
        { method: "POST", body },
      );
      $("#youtubeUploadDialog").close();
      toast(
        scheduled
          ? "Upload complete. The video is scheduled to publish."
          : "Video uploaded to YouTube.",
      );
      if (result.video?.url) window.open(result.video.url, "_blank", "noopener");
      await loadChannels();
    } catch (error) {
      const errorBox = $("#youtubeUploadError");
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
    } finally {
      button.disabled = false;
      button.innerHTML = 'Upload video <span>↑</span>';
    }
  });

  window.showYoutubeDashboard = showYoutubeDashboard;
  window.addEventListener("cutroom:upload-clip-to-youtube", async (event) => {
    const { clipId, title, transcript, button } = event.detail || {};
    try {
      await showYoutubeDashboard();
      if (!channels.length) {
        throw new Error("Connect a YouTube account before uploading a clip.");
      }
      const channelId = channels.some(
        (channel) => channel.channel_id === selectedChannelId,
      )
        ? selectedChannelId
        : channels[0].channel_id;
      const response = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/download`,
      );
      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? "This clip has no rendered MP4 to upload yet."
            : `Could not prepare this clip for upload (${response.status}).`,
        );
      }
      const video = await response.blob();
      if (!video.size) {
        throw new Error("The rendered clip is empty and cannot be uploaded.");
      }
      const filename = `${(title || "Cutroom clip")
        .replace(/[<>:"/\\|?*\x00-\x1F]/g, "")
        .trim()
        .slice(0, 90) || "Cutroom clip"}.mp4`;
      openUpload(
        channelId,
        new File([video], filename, { type: "video/mp4" }),
        title || "Cutroom clip",
        transcript || "",
      );
    } catch (error) {
      toast(error.message);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Upload";
      }
    }
  });
  window.addEventListener("cutroom:create-project-from-youtube", (event) => {
    const { url, title } = event.detail || {};
    if (!url) return;
    window.showProjects?.();
    document.querySelector('.source-tab[data-tab="youtube"]')?.click();
    $("#youtubeUrl").value = url;
    $("#loadYoutube").click();
    $("#projectName").value = title || "YouTube video";
    $("#setupDialog").showModal();
  });

  const query = new URLSearchParams(window.location.search);
  if (query.get("view") === "youtube") {
    history.replaceState({}, "", window.location.pathname);
    showYoutubeDashboard();
    if (query.get("youtubeConnected")) toast("YouTube account connected.");
  }
  if (query.has("youtubeError")) {
    history.replaceState({}, "", window.location.pathname);
    showYoutubeDashboard();
    showError(query.get("youtubeError"));
  }
})();
