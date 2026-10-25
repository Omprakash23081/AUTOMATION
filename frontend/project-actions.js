const renderProjectsWithoutActions = window.renderProjects;
function addYoutubeUploadAction(row, clip) {
  if (row.querySelector('[data-upload-youtube-clip]')) return;
  const actions = row.querySelector('.clip-actions');
  if (!actions) return;
  const upload = document.createElement('button');
  upload.type = 'button';
  upload.className = 'clip-edit';
  upload.dataset.uploadYoutubeClip = clip.id;
  upload.textContent = 'Upload';
  upload.addEventListener('click', () => {
    upload.disabled = true;
    upload.textContent = 'Preparing…';
    window.dispatchEvent(new CustomEvent('cutroom:upload-clip-to-youtube', {
      detail: {clipId: clip.id, title: clip.title, transcript: clip.transcript, button: upload}
    }));
  });
  const deleteButton = actions.querySelector('.clip-delete-button');
  actions.insertBefore(upload, deleteButton || null);
}

window.renderProjects = function () {
  renderProjectsWithoutActions();
  document.querySelectorAll('.project-card').forEach(card => {
    if (card.querySelector('.project-actions-trigger')) return;
    const actions = document.createElement('div');
    actions.className = 'project-actions';
    const project = projects.find(item => item.id === card.dataset.project);
    actions.innerHTML = '<button class="project-actions-trigger" aria-label="Project actions" title="Project actions">···</button><div class="project-actions-menu hidden"><button data-action="rename">Rename</button><button data-action="duplicate">Duplicate</button><button data-action="archive">' + (project?.archived_at ? 'Restore' : 'Archive') + '</button><button data-action="delete">Delete</button></div>';
    card.appendChild(actions);
    actions.querySelector('.project-actions-trigger').addEventListener('click', event => {
      event.stopPropagation();
      document.querySelectorAll('.project-actions-menu').forEach(menu => menu.classList.add('hidden'));
      actions.querySelector('.project-actions-menu').classList.toggle('hidden');
    });
    actions.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async event => {
      event.stopPropagation(); event.preventDefault();
      const id = card.dataset.project, action = button.dataset.action;
      try {
        if (action === 'rename') {
          const current = projects.find(p => p.id === id);
          const name = window.prompt('Project name', current?.name || '');
          if (name === null) return;
          await api('/api/projects/' + encodeURIComponent(id), {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
          toast('Project renamed.');
        } else if (action === 'duplicate') {
          await api('/api/projects/' + encodeURIComponent(id) + '/duplicate', {method:'POST'});
          toast('Project duplicated.');
        } else if (action === 'archive') {
          const current = projects.find(p => p.id === id);
          const archived = !current?.archived_at;
          await api('/api/projects/' + encodeURIComponent(id), {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({archived})});
          toast(archived ? 'Project archived.' : 'Project restored.');
        } else if (action === 'delete') {
          if (!window.confirm('Delete this project, its clips, and its source file if no other project uses it?')) return;
          await api('/api/projects/' + encodeURIComponent(id), {method:'DELETE'});
          toast('Project deleted.');
        }
        await load();
      } catch (err) { toast(err.message); }
    }));
  });
};
document.addEventListener('click', event => {
  if (!event.target.closest('.project-actions')) document.querySelectorAll('.project-actions-menu').forEach(menu => menu.classList.add('hidden'));
});
const refreshWithProjectActions = window.refreshProject;
let automaticRenderTimer = null;
let automaticRenderProjectId = null;
let automaticRenderRefreshBusy = false;
window.refreshProject = async function (projectId) {
  await refreshWithProjectActions(projectId);
  try {
    const project = document.querySelector('#projectDetail')?._projectSnapshot;
    if (!project) return;
    if (!window.acceptProjectJobSnapshot(document.querySelector('#projectDetail'), project)) return;
    const heading = document.querySelector('.detail-section-head h3');
    const description = document.querySelector('.detail-section-head > span');
    if (project.source_type === 'youtube') {
      const rankedCount = project.clips.filter(clip => clip.origin === 'ai').length;
      const transcriptDraftCount = project.clips.filter(clip => clip.origin === 'transcript').length;
      const rankedLabel = rankedCount + ' ranked Short' + (rankedCount === 1 ? '' : 's');
      const draftLabel = transcriptDraftCount ? ' · ' + transcriptDraftCount + ' transcript draft' + (transcriptDraftCount === 1 ? '' : 's') : '';
      if (heading) heading.textContent = rankedLabel + draftLabel;
      if (description) description.textContent = 'Top moments · MP4s render automatically';
    }
    project.clips.forEach((clip, index) => {
      const row = document.querySelectorAll('.clip-row')[index];
      if (!row) return;
      if (clip.export_path) {
        addYoutubeUploadAction(row, clip);
        if (!row.querySelector('.clip-downloads')) {
          const clipInfo = row.querySelector('.clip-info');
          row.querySelector('.clip-workspace-preview-pending')?.remove();
          if (clipInfo && !clipInfo.querySelector('.clip-workspace-preview')) {
            const previewPanel = document.createElement('div');
            previewPanel.className = 'clip-workspace-preview';
            previewPanel.hidden = true;
            previewPanel.innerHTML = '<span>CLIP PREVIEW</span><video controls playsinline preload="metadata" src="/api/clips/' + encodeURIComponent(clip.id) + '/preview?v=' + encodeURIComponent(clip.edit_version || 0) + '" aria-label="Preview of ' + esc(clip.title) + '"></video>';
            clipInfo.appendChild(previewPanel);
          }
          const actions = document.createElement('div');
          actions.className = 'clip-actions';
          const downloads = document.createElement('span');
          downloads.className = 'clip-downloads';
          downloads.innerHTML = '<button class="clip-edit" data-download="' + esc(clip.id) + '">MP4 ↓</button>';
          const preview = document.createElement('button');
          preview.type = 'button';
          preview.className = 'clip-edit';
          preview.dataset.previewClip = clip.id;
          preview.textContent = 'Preview';
          const edit = document.createElement('button');
          edit.type = 'button';
          edit.className = 'clip-edit';
          edit.dataset.clip = clip.id;
          edit.textContent = 'Edit';
          const deleteButton = document.createElement('button');
          deleteButton.type = 'button';
          deleteButton.className = 'clip-edit clip-delete-button';
          deleteButton.dataset.deleteClip = clip.id;
          deleteButton.textContent = 'Delete';
          actions.append(downloads, preview, edit, deleteButton);
          row.querySelector('[data-clip]')?.closest('.clip-actions')?.remove();
          row.querySelector('[data-clip]')?.remove();
          row.appendChild(actions);
          addYoutubeUploadAction(row, clip);
          downloads.querySelector('[data-download]').addEventListener('click', () => { location.href = '/api/clips/' + encodeURIComponent(clip.id) + '/download'; });
          preview.addEventListener('click', () => {
            const container = row.querySelector('.clip-workspace-preview');
            const video = container?.querySelector('video');
            if (!container || !video) return;
            const showing = container.hidden;
            container.hidden = !showing;
            preview.textContent = showing ? 'Hide preview' : 'Preview';
            if (!showing) video.pause();
          });
          edit.addEventListener('click', () => openClipEditor(project, clip));
          deleteButton.addEventListener('click', async () => {
            if (!window.confirm('Permanently delete this clip and its rendered video? This cannot be undone.')) return;
            deleteButton.disabled = true;
            try {
              await api('/api/clips/' + encodeURIComponent(clip.id), {method:'DELETE'});
              toast('Clip deleted.');
              await window.refreshProject(projectId);
            } catch (error) {
              toast(error.message);
              deleteButton.disabled = false;
            }
          });
        }
        return;
      }
      const job = clip.render_job;
      if (!job) return;
      let state = row.querySelector('.automatic-render-state');
      if (!state) {
        state = document.createElement('span');
        state.className = 'automatic-render-state';
        row.querySelector('.clip-info').appendChild(state);
      }
      if (job.status === 'Queued' || job.status === 'Running') {
        const button = row.querySelector('[data-clip]');
        state.textContent = job.stage + ' · ' + Math.max(0, Number(job.progress) || 0) + '%';
        if (button) {
          button.disabled = true;
          button.textContent = 'Rendering…';
        }
      } else if (job.status === 'Failed') {
        state.textContent = 'Automatic render failed: ' + (job.error || 'Open the editor to retry.');
      }
    });
    const rendering = project.clips.some(clip => clip.render_job && ['Queued', 'Running'].includes(clip.render_job.status));
    const processing = !project.error && project.jobs.some(job => job.status === 'Queued' || job.status === 'Running');
    const shouldPoll = rendering || processing;
    if (automaticRenderProjectId !== projectId) {
      clearInterval(automaticRenderTimer);
      automaticRenderTimer = null;
      automaticRenderProjectId = projectId;
    }
    if (shouldPoll && !automaticRenderTimer) {
      automaticRenderTimer = setInterval(async () => {
        const dialog = document.querySelector('#projectDialog');
        if (!dialog || !dialog.open || automaticRenderProjectId !== projectId) {
          clearInterval(automaticRenderTimer);
          automaticRenderTimer = null;
          return;
        }
        if (automaticRenderRefreshBusy) return;
        automaticRenderRefreshBusy = true;
        try { await window.refreshProject(projectId); }
        finally { automaticRenderRefreshBusy = false; }
      }, 2500);
    } else if (!shouldPoll && automaticRenderTimer) {
      clearInterval(automaticRenderTimer);
      automaticRenderTimer = null;
    }
  } catch (error) {
    toast('Could not refresh automatic render status: ' + error.message);
  }
};
