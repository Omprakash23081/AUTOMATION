// Show persisted processing attempts in the project workspace and expose bounded retry state.
const refreshProjectWithJobHistory = window.refreshProject;
window.refreshProject = async function (projectId) {
  await refreshProjectWithJobHistory(projectId);
  const detail = document.getElementById('projectDetail');
  const header = detail?.querySelector('.detail-header');
  if (!header) return;
  try {
    const project = detail._projectSnapshot;
    if (!project) return;
    if (!window.acceptProjectJobSnapshot(detail, project)) return;
    const jobs = project.jobs || [];
    let section = detail.querySelector('#jobHistory');
    if (!jobs.length) { section?.remove(); return; }
    if (!section) {
      section = document.createElement('section');
      section.id = 'jobHistory';
      section.className = 'job-history';
      section.innerHTML = '<div class="job-history-heading"><h3>Processing history</h3><span></span></div>';
      header.insertAdjacentElement('afterend', section);
    }
    section.querySelector('.job-history-heading span').textContent = jobs.length + ' recent run' + (jobs.length === 1 ? '' : 's');
    jobs.forEach((job, index) => {
      const date = new Date(job.created_at).toLocaleString();
      const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
      const failed = job.status === 'Failed' || (index === 0 && !!project.error);
      const active = !failed && ['Queued','Running'].includes(job.status);
      const errorMessage = job.error || (index === 0 ? project.error : '');
      const captionUnavailable = failed && /YouTube captions are disabled for this video/i.test(errorMessage || '');
      const stage = active ? (job.stage === 'Queued' ? 'Queued for processing' : job.stage || job.status) : failed ? 'Processing failed' : job.status === 'Succeeded' ? 'Processing complete' : 'Processing canceled';
      const indeterminate = active && job.status === 'Running' && progress === 0 && ['Checking saved source','Inspecting cached source','Connecting to YouTube','Downloading source','Inspecting source','Reusing downloaded source','Transcribing','Reusing transcript','Getting YouTube captions','Finding clips','Ranking moments with OpenAI','Ranking moments with Gemini','Switching to Gemini'].includes(job.stage);
      const waitingText = job.stage === 'Checking saved source' ? 'Checking for a saved local video' : job.stage === 'Inspecting cached source' ? 'Validating saved video; no download needed' : job.stage === 'Connecting to YouTube' ? 'Resolving video details; download has not started' : job.stage === 'Downloading source' ? 'Waiting for YouTube download progress' : job.stage === 'Inspecting source' ? 'Checking the downloaded video' : job.stage === 'Reusing downloaded source' ? 'Checking the saved video file' : job.stage === 'Transcribing' ? 'Waiting for transcription response' : job.stage === 'Reusing transcript' ? 'Using saved transcript' : job.stage === 'Getting YouTube captions' ? 'Checking caption availability' : job.stage === 'Ranking moments with OpenAI' ? 'Waiting for OpenAI ranking response' : job.stage === 'Switching to Gemini' ? 'Changing to the configured Gemini provider' : job.stage === 'Ranking moments with Gemini' ? 'Waiting for Gemini ranking response' : 'Analyzing transcript';
      const queuedAge = Date.now() - Date.parse(job.created_at || Date.now());
      const workerOffline = job.status === 'Queued' && project.worker && !project.worker.available && (project.worker.age_seconds > 20 || queuedAge > 20000);
      const queueText = workerOffline ? 'Worker has not checked in; job remains safely queued' : 'Waiting in queue' + (job.queue_position ? ' · position ' + job.queue_position : '');
      const lastUpdate=Date.parse(job.updated_at||job.created_at||'');
      const idleSeconds=active&&job.status==='Running'&&Number.isFinite(lastUpdate)?Math.max(0,Math.floor((Date.now()-lastUpdate)/1000)):0;
      const staleText=idleSeconds>=30?' · no update for '+(idleSeconds<60?idleSeconds+'s':Math.floor(idleSeconds/60)+'m '+idleSeconds%60+'s'):'';
      const progressText = active ? (job.status === 'Queued' ? queueText + ' · no attempt used yet' : (job.progress_detail || (indeterminate ? waitingText : progress + '% of this step')) + (job.stage === 'Downloading source' && progress > 0 ? ' · ' + progress + '% downloaded' : '')+staleText) : failed ? (captionUnavailable ? 'Captions unavailable; retry will not help' : job.attempts < job.max_attempts ? 'Retry available' : 'Retry limit reached') : job.status === 'Succeeded' ? 'Complete' : 'Canceled';
      let row = Array.from(section.querySelectorAll('[data-job-row]')).find(item => item.dataset.jobRow === job.id);
      if (!row) {
        row = document.createElement('article');
        row.className = 'job-history-row';
        row.dataset.jobRow = job.id;
        row.innerHTML = '<div class="job-history-main"><b class="job-stage"></b><small class="job-meta"></small><div class="job-progress" role="progressbar"><span></span></div><p class="job-error" hidden></p></div><div class="job-history-actions"><span class="job-state"></span></div>';
      }
      row.querySelector('.job-stage').textContent = stage;
      row.querySelector('.job-meta').textContent = date + ' · attempt ' + job.attempts + '/' + job.max_attempts + ' · ' + progressText;
      const bar = row.querySelector('.job-progress');
      bar.hidden = !active || job.status === 'Queued' || workerOffline;
      bar.classList.toggle('indeterminate', indeterminate);
      bar.setAttribute('aria-label', stage);
      if (indeterminate) {
        bar.removeAttribute('aria-valuemin');
        bar.removeAttribute('aria-valuemax');
        bar.removeAttribute('aria-valuenow');
      } else {
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', String(progress));
      }
      bar.firstElementChild.style.width = progress + '%';
      const error = row.querySelector('.job-error');
      error.textContent = errorMessage || '';
      error.hidden = !errorMessage;
      const state = row.querySelector('.job-state');
      state.className = 'job-state ' + (failed ? 'failure' : job.status === 'Succeeded' ? 'success' : job.status === 'Canceled' ? 'canceled' : 'pending');
      state.textContent = !failed && job.cancel_requested ? 'Canceling' : failed ? 'Failed' : job.status;
      const actions = row.querySelector('.job-history-actions');
      let cancel = actions.querySelector('.cancel-processing-job');
      if (active) {
        if (!cancel) {
          cancel = document.createElement('button');
          cancel.type = 'button';
          cancel.className = 'cancel-processing-job';
          cancel.textContent = 'Cancel';
          actions.appendChild(cancel);
        }
        cancel.dataset.job = job.id;
        cancel.disabled = !!job.cancel_requested;
        cancel.textContent = job.cancel_requested ? 'Canceling…' : 'Cancel';
      } else cancel?.remove();
      const anchor = section.children[index + 1];
      if (anchor !== row) section.insertBefore(row, anchor || null);
    });
    if (!section.dataset.actionsBound) {
      section.dataset.actionsBound = 'true';
      section.addEventListener('click', async event => {
        const button = event.target.closest('.cancel-processing-job');
        if (!button) return;
        button.disabled = true;
        button.textContent = 'Canceling…';
        try {
          const result = await api('/api/processing-jobs/' + encodeURIComponent(button.dataset.job), { method: 'DELETE' });
          toast(result.alreadyFinished ? 'Processing had already finished.' : result.status === 'Canceled' ? 'Processing canceled.' : 'Cancellation requested. Stopping after the current operation.');
        } catch (error) {
          toast('Could not cancel processing: ' + error.message);
        }
        await window.refreshProject(projectId);
      });
    }
    const latest = jobs[0], retryButton = document.getElementById('generateBtn');
    if ((latest.status === 'Failed' || project.error) && retryButton) {
      if (/YouTube captions are disabled for this video/i.test(latest.error || project.error || '')) {
        retryButton.disabled = true;
        retryButton.textContent = 'Captions unavailable';
      } else if (latest.attempts >= latest.max_attempts) {
        retryButton.disabled = true;
        retryButton.textContent = 'Retry limit reached';
      } else retryButton.innerHTML = 'Retry processing (' + latest.attempts + '/' + latest.max_attempts + ') <span>↻</span>';
    }
  } catch (error) {
    console.warn('Could not load processing history:', error.message);
  }
};
