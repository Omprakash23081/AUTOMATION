// Clip editor backed by immutable, versioned edit-decision records.
function openClipEditor(project, clip) {
  const edit = clip.edit || {};
  const overlay = document.createElement('dialog');
  overlay.className = 'editor-overlay';
  overlay.innerHTML = '<div class="clip-editor"><button class="close-modal" id="closeClip" aria-label="Close">×</button><div class="eyebrow">CLIP REVIEW · EDIT V' + Number(clip.edit_version || 0) + '</div><h2>' + esc(edit.title || clip.title) + '</h2><video controls preload="metadata" src="/api/projects/' + encodeURIComponent(project.id) + '/source" id="editorVideo"></video><div class="editor-playhead"><input type="range" id="playhead" min="0" max="1" step="0.1" value="0" aria-label="Source video playhead"><div><button type="button" id="setInPoint">Set in to playhead</button><button type="button" id="setOutPoint">Set out to playhead</button><span id="playheadTime">0:00</span></div></div><section class="timeline-panel"><div class="timeline-heading"><b>Source timeline</b><label>Zoom <input id="timelineZoom" type="range" min="1" max="8" step="1" value="3"></label><span>Drag trim handles · Ctrl+Z undo</span><button type="button" id="undoEdit" title="Undo">↶</button><button type="button" id="redoEdit" title="Redo">↷</button></div><div class="timeline-scroll" id="timelineScroll"><div class="timeline-content" id="timelineContent"><div class="timeline-ruler" id="timelineRuler"></div><div class="timeline-track" id="timelineTrack"><div class="timeline-selection" id="timelineSelection"><button type="button" class="timeline-handle in" data-edge="in" aria-label="Drag clip in point">IN</button><button type="button" class="timeline-handle out" data-edge="out" aria-label="Drag clip out point">OUT</button></div><div class="timeline-playhead" id="timelinePlayhead"></div></div></div></div></section><label class="field-label">Clip title</label><input class="field-input" id="editTitle" maxlength="100" value="' + esc(edit.title || clip.title) + '"><div class="time-fields"><label>In point (seconds)<input type="number" id="editStart" min="0" step="0.1" value="' + Number(edit.start ?? clip.start).toFixed(1) + '"></label><label>Out point (seconds)<input type="number" id="editEnd" min="0" step="0.1" value="' + Number(edit.end ?? clip.end).toFixed(1) + '"></label></div><div class="editor-selection" id="selectionDuration"></div><div class="export-options"><label>Aspect ratio<select id="editAspect"><option value="9:16">Portrait 9:16</option><option value="1:1">Square 1:1</option><option value="16:9">Landscape 16:9</option></select></label><label>Caption style<select id="captionStyle"><option value="classic">Classic</option><option value="bold">Bold</option><option value="boxed">Boxed</option></select></label></div><label class="burn-option"><input type="checkbox" id="burnCaptions"> Burn captions into the MP4</label><section class="edit-history"><div><b>Saved edit versions</b><span id="editSaveState">Loading…</span></div><select id="editHistory" aria-label="Saved edit version"></select><button type="button" id="restoreEdit" disabled>Restore version</button></section><div class="editor-transcript">' + esc(clip.transcript || 'Transcript text unavailable.') + '</div><div class="dialog-foot"><span>Edits save automatically. Render a preview here; download it only when ready.</span><button class="primary-button" id="exportClip">Render preview <span>▶</span></button></div></div>';
  document.body.appendChild(overlay);
  overlay.showModal();
  const $e = selector => $(selector, overlay);
  const video = $e('#editorVideo'), saveState = $e('#editSaveState');
  const videoPreview = document.createElement('div'); videoPreview.className = 'editor-preview'; video.parentNode.insertBefore(videoPreview, video); videoPreview.append(video);
  let baseVersion = Number(clip.edit_version || 0), saveTimer = null, saveChain = Promise.resolve();
  $e('#editAspect').value = edit.aspectRatio || clip.aspect_ratio || '9:16';
  $e('#captionStyle').value = edit.captionStyle || clip.caption_style || 'classic';
  $e('#burnCaptions').checked = edit.burnCaptions ?? (clip.burn_captions !== 0);
  const brandControls = document.createElement('div'); brandControls.className = 'caption-brand-controls';
  brandControls.innerHTML = '<label>Font<select id="captionFont"><option>Arial</option><option>DejaVu Sans</option><option>Liberation Sans</option></select></label><label>Text color<input id="captionColor" type="color"></label><label>Box color<input id="captionBackground" type="color"></label><label>Highlight color<input id="captionHighlightColor" type="color"></label><label>Logo overlay<select id="logoAsset"><option value="">No logo</option></select></label><label>Background music<select id="musicAsset"><option value="">No music</option></select></label><label>Source volume (0–2)<input id="sourceVolume" type="number" min="0" max="2" step="0.05"></label><label>Music volume (0–2)<input id="musicVolume" type="number" min="0" max="2" step="0.05"></label><label>Fade in (seconds)<input id="musicFadeIn" type="number" min="0" max="10" step="0.1"></label><label>Fade out (seconds)<input id="musicFadeOut" type="number" min="0" max="10" step="0.1"></label><button type="button" id="applyBrandDefaults">Use Brand Kit defaults</button>';
  const outputControls = document.createElement('div'); outputControls.className = 'export-options';
  outputControls.innerHTML = '<label>Output resolution<select id="outputResolution"><option value="1080">1080p · high</option><option value="720">720p · standard</option><option value="480">480p · compact</option></select></label><label>Frame rate<select id="frameRate"><option value="24">24 fps</option><option value="25">25 fps</option><option value="30">30 fps</option><option value="50">50 fps</option><option value="60">60 fps</option></select></label><label>Video bitrate<select id="videoBitrate"><option value="2500000">2.5 Mbps</option><option value="6000000">6 Mbps</option><option value="10000000">10 Mbps</option><option value="16000000">16 Mbps</option></select></label>';
  $e('.export-options').after(outputControls); outputControls.after(brandControls);
  const cropControls = document.createElement('section'); cropControls.className = 'crop-position-controls'; cropControls.innerHTML = '<b>Crop position</b><label>Horizontal<input id="cropX" type="range" min="0" max="1" step="0.01"><output id="cropXValue"></output></label><label>Vertical<input id="cropY" type="range" min="0" max="1" step="0.01"><output id="cropYValue"></output></label><small>Drag the crop focus from left/top (0%) to right/bottom (100%). Preview and export use the same position.</small>';
  outputControls.after(cropControls);
  const portraitControls = document.createElement('section'); portraitControls.className = 'portrait-framing-controls';
  portraitControls.innerHTML = '<label>Portrait framing<select id="portraitBackground"><option value="blurred">Blurred background fill</option><option value="crop">Crop to fill</option></select></label><small>Used when exporting in 9:16. Landscape sources stay visible over a blurred full-frame background.</small>';
  cropControls.after(portraitControls);
  const videoEffects = document.createElement('section'); videoEffects.className = 'video-effects-controls';
  videoEffects.innerHTML = '<b>Video effects</b><label>Fade<select id="fadeEffect"><option value="none">None</option><option value="in">Fade in</option><option value="out">Fade out</option><option value="in-out">Fade in + out</option></select></label><label>Playback speed<select id="playbackSpeed"><option value="0.5">0.5× slower</option><option value="0.75">0.75× slower</option><option value="1">Normal</option><option value="1.25">1.25× faster</option><option value="1.5">1.5× faster</option><option value="2">2× faster</option></select></label><label>Color filter<select id="colorFilter"><option value="none">Original</option><option value="warm">Warm</option><option value="cool">Cool</option><option value="monochrome">Black &amp; white</option><option value="vintage">Vintage</option></select></label><label>Motion<select id="motionEffect"><option value="none">None</option><option value="slow-zoom-in">Slow zoom in</option><option value="slow-zoom-out">Slow zoom out</option></select></label><small>Effects are applied to the exported MP4. Fades are 0.5 seconds.</small>';
  cropControls.after(videoEffects);
  function populateAssets(selector, type, savedId) { const select = $e(selector); if (savedId && !Array.from(select.options).some(option => option.value === savedId)) { const saved = document.createElement('option'); saved.value = savedId; saved.textContent = 'Loading saved asset…'; select.append(saved) } select.value = savedId || ''; select.disabled = true; return api('/api/assets?type=' + type).then(({ assets }) => { const placeholder = select.options[0]; select.replaceChildren(placeholder); assets.forEach(asset => { const option = document.createElement('option'); option.value = asset.id; option.textContent = asset.name; select.append(option) }); if (savedId && !assets.some(asset => asset.id === savedId)) { const missing = document.createElement('option'); missing.value = savedId; missing.textContent = 'Saved asset unavailable'; select.append(missing) } select.value = savedId || ''; select.disabled = false }).catch(error => { if (savedId && !Array.from(select.options).some(option => option.value === savedId)) { const missing = document.createElement('option'); missing.value = savedId; missing.textContent = 'Saved asset unavailable'; select.append(missing) } if (savedId) select.value = savedId; select.disabled = false; toast(type === 'image' ? 'Logo library unavailable: ' + error.message : 'Audio library unavailable: ' + error.message) }) }
  populateAssets('#logoAsset', 'image', edit.logoAssetId); populateAssets('#musicAsset', 'audio', edit.musicAssetId);
  let audioTracks = Array.isArray(edit.audioTracks) ? JSON.parse(JSON.stringify(edit.audioTracks)) : [];
  let videoSegments = Array.isArray(edit.videoSegments) && edit.videoSegments.length ? JSON.parse(JSON.stringify(edit.videoSegments)).map(item => ({ sourceStart: Number(item.sourceStart), sourceEnd: Number(item.sourceEnd) })) : [{ sourceStart: Number(edit.start ?? clip.start), sourceEnd: Number(edit.end ?? clip.end) }];
  let selectedVideoSegment = 0;
  const videoTools = document.createElement('div'); videoTools.className = 'video-segment-tools'; videoTools.innerHTML = '<button type="button" id="splitVideoSegment">Split at playhead</button><button type="button" id="moveVideoSegmentUp">Move earlier</button><button type="button" id="moveVideoSegmentDown">Move later</button><button type="button" id="removeVideoSegment">Remove selected segment</button><span id="videoSegmentCount"></span>';
  $e('.timeline-heading').after(videoTools);
  const extraAudioPanel = document.createElement('section'); extraAudioPanel.className = 'extra-audio-panel';
  extraAudioPanel.innerHTML = '<div class="extra-audio-heading"><div><b>Additional audio tracks</b><span>Layer music, ambience, or sound effects over the source.</span></div><select id="extraAudioAsset" aria-label="Audio asset"><option value="">Choose an audio asset</option></select><button type="button" id="addAudioTrack">Add track</button></div><div id="extraAudioTracks"></div>';
  brandControls.after(extraAudioPanel);
  const extraAudioNames = new Map();
  api('/api/assets?type=audio').then(({ assets }) => { const picker = $e('#extraAudioAsset'); assets.forEach(asset => { extraAudioNames.set(asset.id, asset.name); const option = document.createElement('option'); option.value = asset.id; option.textContent = asset.name; picker.append(option) }); renderAudioTracks() }).catch(error => toast('Audio tracks unavailable: ' + error.message));
  function renderAudioTracks() {
    const list = $e('#extraAudioTracks'), duration = Math.max(0.1, videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0)); list.replaceChildren();
    audioTracks.forEach((track, index) => {
      const row = document.createElement('div'); row.className = 'extra-audio-row'; row.dataset.index = String(index);
      const head = document.createElement('div'); head.className = 'extra-audio-row-head';
      const title = document.createElement('b'); title.textContent = (extraAudioNames.get(track.assetId) || 'Audio asset') + ' · Track ' + (index + 1); head.append(title);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-audio-track'; remove.dataset.index = String(index); remove.textContent = 'Remove'; head.append(remove); row.append(head);
      const lane = document.createElement('div'); lane.className = 'extra-audio-lane'; lane.setAttribute('aria-label', 'Track ' + (index + 1) + ' timeline position');
      const block = document.createElement('span'); block.style.left = (100 * track.start / duration) + '%'; block.style.width = (100 * (track.end - track.start) / duration) + '%'; block.textContent = 'AUDIO'; lane.append(block); row.append(lane);
      const fields = document.createElement('div'); fields.className = 'extra-audio-fields';
      [['start', 'Start'], ['end', 'End'], ['volume', 'Volume'], ['fadeIn', 'Fade in'], ['fadeOut', 'Fade out']].forEach(([key, label]) => {
        const wrapper = document.createElement('label'); wrapper.textContent = label;
        const input = document.createElement('input'); input.type = 'number'; input.step = key === 'volume' ? '0.05' : '0.1'; input.min = '0'; input.max = key === 'volume' ? '2' : key === 'start' || key === 'end' ? String(duration) : '10'; input.dataset.field = key; input.dataset.index = String(index); input.value = String(track[key]); wrapper.append(input); fields.append(wrapper);
      });
      row.append(fields); list.append(row);
    });
  }
  function fitAudioTracksToDuration() {
    const duration = Math.max(.2, videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0));
    audioTracks.forEach(track => {
      track.start = Math.min(Math.max(0, Number(track.start) || 0), duration - .2);
      track.end = Math.min(Math.max(track.start + .2, Number(track.end) || duration), duration);
      track.fadeIn = Math.min(Number(track.fadeIn) || 0, track.end - track.start);
      track.fadeOut = Math.min(Number(track.fadeOut) || 0, Math.max(0, track.end - track.start - track.fadeIn));
    });
  }
  extraAudioPanel.addEventListener('input', event => { const input = event.target.closest('input[data-index][data-field]'); if (!input) return; const index = Number(input.dataset.index), track = audioTracks[index]; if (!track) return; track[input.dataset.field] = Number(input.value); const duration = Math.max(.1, videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0)), block = $e('#extraAudioTracks').children[index]?.querySelector('.extra-audio-lane span'); if (block) { block.style.left = (100 * track.start / duration) + '%'; block.style.width = (100 * (track.end - track.start) / duration) + '%' } scheduleSave() });
  extraAudioPanel.addEventListener('click', event => {
    const remove = event.target.closest('.remove-audio-track');
    if (remove) { audioTracks.splice(Number(remove.dataset.index), 1); renderAudioTracks(); scheduleSave(); return; }
    if (event.target.closest('#addAudioTrack')) {
      const assetId = $e('#extraAudioAsset').value; if (!assetId) { toast('Choose an audio asset first.'); return; }
      if (audioTracks.some(track => track.assetId === assetId)) { toast('That audio asset is already in an additional track.'); return; }
      if (audioTracks.length >= 8) { toast('A clip can have up to eight additional audio tracks.'); return; }
      const duration = Math.max(0.2, videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0));
      audioTracks.push({ assetId, start: 0, end: duration, volume: 1, fadeIn: 0, fadeOut: 0 }); renderAudioTracks(); scheduleSave();
    }
  });
  $e('#captionFont').value = edit.fontFamily || 'Arial'; $e('#captionColor').value = edit.captionColor || '#FFFFFF'; $e('#captionBackground').value = edit.captionBackground || '#141414'; $e('#captionHighlightColor').value = edit.captionHighlightColor || '#8AAB4A';
  $e('#sourceVolume').value = edit.sourceVolume ?? 1; $e('#musicVolume').value = edit.musicVolume ?? 0.2; $e('#musicFadeIn').value = edit.musicFadeIn ?? 0; $e('#musicFadeOut').value = edit.musicFadeOut ?? 0;
  $e('#outputResolution').value = String(edit.outputResolution ?? 1080); $e('#frameRate').value = String(edit.frameRate ?? 30); $e('#videoBitrate').value = String(edit.videoBitrate ?? 6000000);
  $e('#cropX').value = String(edit.cropX ?? 0.5); $e('#cropY').value = String(edit.cropY ?? 0.5);
  $e('#portraitBackground').value = edit.portraitBackground || ($e('#editAspect').value === '9:16' ? 'blurred' : 'crop');
  function updatePortraitFramingControl() { $e('#portraitBackground').disabled = $e('#editAspect').value !== '9:16'; }
  updatePortraitFramingControl();
  function updateCropPreview() { const x = Number($e('#cropX').value), y = Number($e('#cropY').value), ratio = $e('#editAspect').value.split(':').map(Number); videoPreview.style.setProperty('--preview-ratio', String(ratio[0] / ratio[1])); videoPreview.style.aspectRatio = ratio[0] + ' / ' + ratio[1]; video.style.objectPosition = (x * 100) + '% ' + (y * 100) + '%'; $e('#cropXValue').textContent = Math.round(x * 100) + '%'; $e('#cropYValue').textContent = Math.round(y * 100) + '%' }
  updateCropPreview();
  const captionLayout = document.createElement('div'); captionLayout.className = 'caption-layout-controls';
  captionLayout.innerHTML = '<label>Caption size <span><input id="captionSize" type="range" min="40" max="160" step="5"><output id="captionSizeValue"></output></span></label><label>Vertical position<select id="captionPosition"><option value="bottom">Bottom · safe area</option><option value="middle">Middle</option><option value="top">Top · safe area</option></select></label><label>Text alignment<select id="captionAlignment"><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label><label>Outline <span><input id="captionOutline" type="range" min="0" max="5" step="1"><output id="captionOutlineValue"></output></span></label><label>Caption animation<select id="captionAnimation"><option value="none">Static captions</option><option value="word-highlight">Highlight each spoken word</option></select></label><button type="button" id="resetCaptionLayout">Use compact caption defaults</button><p id="portraitCaptionHint" class="caption-animation-hint" hidden>Portrait captions stay centered in a fixed-width lower safe area.</p><p id="captionAnimationHint" class="caption-animation-hint"></p>';
  brandControls.after(captionLayout);
  const captionPreview = document.createElement('div'); captionPreview.className = 'caption-style-preview'; captionPreview.innerHTML = '<span id="captionPreviewText">Your captions will look like this</span>';
  captionLayout.after(captionPreview);
  const exportProgress = document.createElement('section'); exportProgress.className = 'export-job-progress'; exportProgress.hidden = true;
  exportProgress.innerHTML = '<div><b id="exportJobStage">Queueing export…</b><span id="exportJobPercent">0%</span></div><progress id="exportJobBar" max="100" value="0"></progress><div class="export-job-actions"><button type="button" id="cancelExportJob">Cancel export</button><button type="button" id="retryExportJob" hidden>Retry export</button></div>';
  $e('.dialog-foot').after(exportProgress);
  const renderedPreview = document.createElement('section'); renderedPreview.className = 'rendered-clip-preview'; renderedPreview.hidden = true;
  renderedPreview.innerHTML = '<div><b>Edited clip preview</b><a class="primary-button" id="downloadPreviewClip" download>Download MP4</a></div><video controls playsinline preload="metadata" id="renderedClipVideo"></video>';
  exportProgress.after(renderedPreview);
  const renderedVideo = $e('#renderedClipVideo');
  let previewVersion = null;
  function showRenderedPreview(downloadUrl) {
    previewVersion = baseVersion;
    renderedVideo.src = '/api/clips/' + encodeURIComponent(clip.id) + '/preview?version=' + encodeURIComponent(baseVersion) + '&t=' + Date.now();
    renderedPreview.hidden = false;
    $e('#downloadPreviewClip').href = downloadUrl || '/api/clips/' + encodeURIComponent(clip.id) + '/download';
    renderedVideo.load();
  }
  function markPreviewStale() {
    if (previewVersion === null) return;
    renderedVideo.pause(); renderedVideo.removeAttribute('src'); renderedVideo.load();
    renderedPreview.hidden = true; previewVersion = null;
  }
  if (clip.export_path) showRenderedPreview('/api/clips/' + encodeURIComponent(clip.id) + '/download');
  $e('#captionSize').value = edit.captionSize ?? 50; $e('#captionPosition').value = edit.captionPosition || 'bottom'; $e('#captionAlignment').value = edit.captionAlignment || 'center'; $e('#captionOutline').value = edit.captionOutline ?? 2; $e('#captionAnimation').value = edit.captionAnimation || 'none';
  $e('#resetCaptionLayout').addEventListener('click', () => {
    $e('#captionStyle').value = 'classic';
    $e('#captionSize').value = '50';
    $e('#captionPosition').value = 'bottom';
    $e('#captionAlignment').value = 'center';
    $e('#captionOutline').value = '2';
    $e('#captionAnimation').value = 'none';
    updateCaptionPreview();
    scheduleSave();
    toast('Compact caption settings applied. Export again to update this clip.');
  });
  $e('#fadeEffect').value = edit.fadeEffect || 'none'; $e('#playbackSpeed').value = String(edit.playbackSpeed ?? 1); $e('#colorFilter').value = edit.colorFilter || 'none'; $e('#motionEffect').value = edit.motionEffect || 'none';
  function updateVideoEffectPreview() {
    const filters = { none: 'none', warm: 'sepia(.18) saturate(1.15)', cool: 'saturate(.9) hue-rotate(8deg)', monochrome: 'grayscale(1)', vintage: 'sepia(.28) saturate(.85) contrast(1.08)' };
    video.style.filter = filters[$e('#colorFilter').value] || 'none';
    video.style.transform = $e('#motionEffect').value === 'none' ? 'none' : 'scale(1.08)';
    video.style.transformOrigin = (Number($e('#cropX').value) * 100) + '% ' + (Number($e('#cropY').value) * 100) + '%';
    video.playbackRate = Number($e('#playbackSpeed').value) || 1;
  }
  updateVideoEffectPreview();
  ['#fadeEffect', '#playbackSpeed', '#colorFilter', '#motionEffect'].forEach(selector => $e(selector).addEventListener('change', () => { updateVideoEffectPreview(); scheduleSave(); }));
  function updateCaptionPreview() {
    const sample = $e('#captionPreviewText'), preview = $e('.caption-style-preview'), size = Number($e('#captionSize').value), outline = Number($e('#captionOutline').value), portrait = $e('#editAspect').value === '9:16', position = portrait ? 'bottom' : $e('#captionPosition').value, alignment = portrait ? 'center' : $e('#captionAlignment').value;
    $e('#captionPosition').disabled = portrait; $e('#captionAlignment').disabled = portrait; $e('#portraitCaptionHint').hidden = !portrait;
    $e('#captionSizeValue').textContent = size + '%'; $e('#captionOutlineValue').textContent = String(outline);
    preview.style.alignItems = position === 'top' ? 'flex-start' : position === 'middle' ? 'center' : 'flex-end'; preview.style.justifyContent = alignment === 'left' ? 'flex-start' : alignment === 'right' ? 'flex-end' : 'center';
    const wordHighlight = $e('#captionAnimation').value === 'word-highlight'; sample.innerHTML = wordHighlight ? 'Your captions <b>highlight</b> in sync' : 'Your captions will look like this';
    sample.style.fontSize = Math.round(14 * size / 100) + 'px'; sample.style.fontFamily = $e('#captionFont').value; sample.style.color = $e('#captionColor').value; sample.style.backgroundColor = $e('#captionStyle').value === 'boxed' ? $e('#captionBackground').value : 'transparent'; sample.style.textAlign = alignment;
    if (wordHighlight) { sample.querySelector('b').style.color = $e('#captionHighlightColor').value; $e('#captionAnimationHint').textContent = clip.word_timing_available ? 'Word timestamps are available; the exported MP4 will highlight words on their timings.' : 'No word timestamps are saved for this clip; export will use static captions.'; } else { $e('#captionAnimationHint').textContent = ''; }
    sample.style.textShadow = outline ? Array.from({ length: Math.min(outline, 3) }, (_, i) => `${i + 1}px ${i + 1}px 0 #000,-${i + 1}px -${i + 1}px 0 #000`).join(',') : 'none';
  }
  updateCaptionPreview();
  function readEdit() {
    return { title: $e('#editTitle').value, start: Number($e('#editStart').value), end: Number($e('#editEnd').value), videoSegments: videoSegments.map(item => ({ sourceStart: item.sourceStart, sourceEnd: item.sourceEnd })), cropX: Number($e('#cropX').value), cropY: Number($e('#cropY').value), aspectRatio: $e('#editAspect').value, portraitBackground: $e('#portraitBackground').value, outputResolution: Number($e('#outputResolution').value), frameRate: Number($e('#frameRate').value), videoBitrate: Number($e('#videoBitrate').value), fadeEffect: $e('#fadeEffect').value, playbackSpeed: Number($e('#playbackSpeed').value), colorFilter: $e('#colorFilter').value, motionEffect: $e('#motionEffect').value, burnCaptions: $e('#burnCaptions').checked, captionStyle: $e('#captionStyle').value, fontFamily: $e('#captionFont').value, captionColor: $e('#captionColor').value, captionBackground: $e('#captionBackground').value, captionSize: Number($e('#captionSize').value), captionPosition: $e('#captionPosition').value, captionAlignment: $e('#captionAlignment').value, captionOutline: Number($e('#captionOutline').value), captionAnimation: $e('#captionAnimation').value, captionHighlightColor: $e('#captionHighlightColor').value, logoAssetId: $e('#logoAsset').value || null, musicAssetId: $e('#musicAsset').value || null, sourceVolume: Number($e('#sourceVolume').value), musicVolume: Number($e('#musicVolume').value), musicFadeIn: Number($e('#musicFadeIn').value), musicFadeOut: Number($e('#musicFadeOut').value), audioTracks: JSON.parse(JSON.stringify(audioTracks)) };
  }
  const undoStack = [], redoStack = [];
  let lastSavedSnapshot = JSON.parse(JSON.stringify(readEdit())), historyAction = false;
  function updateHistoryButtons() { $e('#undoEdit').disabled = !undoStack.length; $e('#redoEdit').disabled = !redoStack.length }
  function applyEditSnapshot(snapshot) {
    $e('#editTitle').value = snapshot.title; $e('#editStart').value = snapshot.start; $e('#editEnd').value = snapshot.end; $e('#editAspect').value = snapshot.aspectRatio;
    $e('#portraitBackground').value = snapshot.portraitBackground || 'blurred'; updatePortraitFramingControl();
    $e('#cropX').value = String(snapshot.cropX ?? 0.5); $e('#cropY').value = String(snapshot.cropY ?? 0.5); updateCropPreview();
    $e('#fadeEffect').value = snapshot.fadeEffect || 'none'; $e('#playbackSpeed').value = String(snapshot.playbackSpeed ?? 1); $e('#colorFilter').value = snapshot.colorFilter || 'none'; $e('#motionEffect').value = snapshot.motionEffect || 'none'; updateVideoEffectPreview();
    $e('#outputResolution').value = String(snapshot.outputResolution); $e('#frameRate').value = String(snapshot.frameRate); $e('#videoBitrate').value = String(snapshot.videoBitrate);
    $e('#burnCaptions').checked = snapshot.burnCaptions; $e('#captionStyle').value = snapshot.captionStyle; $e('#captionFont').value = snapshot.fontFamily;
    $e('#captionColor').value = snapshot.captionColor; $e('#captionBackground').value = snapshot.captionBackground; $e('#captionHighlightColor').value = snapshot.captionHighlightColor;
    $e('#captionSize').value = snapshot.captionSize; $e('#captionPosition').value = snapshot.captionPosition; $e('#captionAlignment').value = snapshot.captionAlignment; $e('#captionOutline').value = snapshot.captionOutline; $e('#captionAnimation').value = snapshot.captionAnimation;
    $e('#logoAsset').value = snapshot.logoAssetId || ''; $e('#musicAsset').value = snapshot.musicAssetId || ''; $e('#sourceVolume').value = snapshot.sourceVolume; $e('#musicVolume').value = snapshot.musicVolume; $e('#musicFadeIn').value = snapshot.musicFadeIn; $e('#musicFadeOut').value = snapshot.musicFadeOut;
    audioTracks = Array.isArray(snapshot.audioTracks) ? JSON.parse(JSON.stringify(snapshot.audioTracks)) : []; renderAudioTracks();
    videoSegments = Array.isArray(snapshot.videoSegments) ? JSON.parse(JSON.stringify(snapshot.videoSegments)).map(item => ({ sourceStart: Number(item.sourceStart), sourceEnd: Number(item.sourceEnd) })) : [{ sourceStart: Number(snapshot.start), sourceEnd: Number(snapshot.end) }]; selectedVideoSegment = Math.min(selectedVideoSegment, videoSegments.length - 1); syncVideoBounds(); renderVideoSegments();
    updateCaptionPreview(); updateSelection();
  }
  async function stepHistory(source, destination) {
    if (!source.length) return;
    let target = null;
    try {
      clearTimeout(saveTimer);
      if (saveState.textContent === 'Unsaved changes' || saveState.textContent === 'Saving…') await saveEdit();
      const current = JSON.parse(JSON.stringify(lastSavedSnapshot)); target = source.pop(); destination.push(current); historyAction = true; applyEditSnapshot(target); await saveEdit(); historyAction = false; setSaveState('Saved · undo/redo as version ' + baseVersion); updateHistoryButtons();
    } catch (error) { historyAction = false; if (target) { source.push(target); destination.pop() } toast(error.message) }
  }
  $e('#undoEdit').addEventListener('click', () => stepHistory(undoStack, redoStack));
  $e('#redoEdit').addEventListener('click', () => stepHistory(redoStack, undoStack));
  overlay.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || !['z', 'y'].includes(event.key.toLowerCase())) return;
    if (event.target.matches('input,textarea,select,[contenteditable="true"]')) return;
    event.preventDefault(); stepHistory(event.key.toLowerCase() === 'z' ? undoStack : redoStack, event.key.toLowerCase() === 'z' ? redoStack : undoStack);
  });
  updateHistoryButtons();
  function setSaveState(message, failed = false) { saveState.textContent = message; saveState.classList.toggle('failed', failed); }
  function updateSelection() {
    const start = Number($e('#editStart').value), end = Number($e('#editEnd').value);
    const outputDuration = videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0);
    $e('#selectionDuration').textContent = Number.isFinite(start) && Number.isFinite(end) && end > start ? 'Selected output · ' + outputDuration.toFixed(1) + ' seconds' : 'Choose a valid in and out point.';
    updateTimelineSelection();
  }
  let timelineDragging = null;
  function sourceDuration() { return Math.max(0.1, Number(video.duration) || Number(project.duration) || Number(clip.end) || 1) }
  function updateTimelineSelection() {
    const duration = sourceDuration(), selected = videoSegments[selectedVideoSegment] || videoSegments[0], start = Math.max(0, selected?.sourceStart || 0), end = Math.max(start, selected?.sourceEnd || 0);
    const selection = $e('#timelineSelection'); selection.style.left = (100 * start / duration) + '%'; selection.style.width = (100 * (end - start) / duration) + '%';
  }
  function syncVideoBounds() { if (!videoSegments.length) return; $e('#editStart').value = Math.min(...videoSegments.map(item => item.sourceStart)).toFixed(1); $e('#editEnd').value = Math.max(...videoSegments.map(item => item.sourceEnd)).toFixed(1) }
  function renderVideoSegments() {
    const track = $e('#timelineTrack'); track.querySelectorAll('.timeline-video-item').forEach(node => node.remove()); const duration = sourceDuration();
    videoSegments.forEach((item, index) => { const block = document.createElement('button'); block.type = 'button'; block.className = 'timeline-video-item' + (index === selectedVideoSegment ? ' selected' : ''); block.style.left = (100 * item.sourceStart / duration) + '%'; block.style.width = (100 * (item.sourceEnd - item.sourceStart) / duration) + '%'; block.textContent = 'SEG ' + (index + 1); block.title = 'Source ' + item.sourceStart.toFixed(1) + '–' + item.sourceEnd.toFixed(1) + ' seconds'; block.dataset.index = String(index); track.append(block) });
    $e('#videoSegmentCount').textContent = videoSegments.length + ' segment' + (videoSegments.length === 1 ? '' : 's') + ' · ' + videoSegments.reduce((total, item) => total + item.sourceEnd - item.sourceStart, 0).toFixed(1) + ' sec output';
    $e('#removeVideoSegment').disabled = videoSegments.length <= 1; $e('#splitVideoSegment').disabled = videoSegments.length >= 20; $e('#moveVideoSegmentUp').disabled = selectedVideoSegment <= 0; $e('#moveVideoSegmentDown').disabled = selectedVideoSegment >= videoSegments.length - 1;
    $e('#editStart').disabled = videoSegments.length > 1; $e('#editEnd').disabled = videoSegments.length > 1;
    const handles = $e('#timelineSelection'); handles.style.display = 'block'; updateTimelineSelection();
  }
  function splitAtPlayhead() { const point = Number(video.currentTime) || 0, index = videoSegments.findIndex(item => point > item.sourceStart + .2 && point < item.sourceEnd - .2); if (index < 0) { toast('Place the playhead at least 0.2 seconds inside a segment.'); return } if (videoSegments.length >= 20) { toast('A clip can contain at most 20 segments.'); return } const item = videoSegments[index]; videoSegments.splice(index, 1, { sourceStart: item.sourceStart, sourceEnd: point }, { sourceStart: point, sourceEnd: item.sourceEnd }); selectedVideoSegment = index + 1; renderVideoSegments(); scheduleSave() }
  $e('#splitVideoSegment').addEventListener('click', splitAtPlayhead);
  $e('#moveVideoSegmentUp').addEventListener('click', () => { if (selectedVideoSegment <= 0) return;[videoSegments[selectedVideoSegment - 1], videoSegments[selectedVideoSegment]] = [videoSegments[selectedVideoSegment], videoSegments[selectedVideoSegment - 1]]; selectedVideoSegment--; renderVideoSegments(); scheduleSave() });
  $e('#moveVideoSegmentDown').addEventListener('click', () => { if (selectedVideoSegment >= videoSegments.length - 1) return;[videoSegments[selectedVideoSegment + 1], videoSegments[selectedVideoSegment]] = [videoSegments[selectedVideoSegment], videoSegments[selectedVideoSegment + 1]]; selectedVideoSegment++; renderVideoSegments(); scheduleSave() });
  $e('#removeVideoSegment').addEventListener('click', () => { if (videoSegments.length <= 1) return; videoSegments.splice(selectedVideoSegment, 1); selectedVideoSegment = Math.max(0, Math.min(selectedVideoSegment, videoSegments.length - 1)); syncVideoBounds(); fitAudioTracksToDuration(); renderAudioTracks(); renderVideoSegments(); scheduleSave() });
  function updateTimelinePlayhead() { const duration = sourceDuration(); $e('#timelinePlayhead').style.left = (100 * (Number(video.currentTime) || 0) / duration) + '%'; }
  function renderTimeline() {
    const duration = sourceDuration(), scroll = $e('#timelineScroll'), content = $e('#timelineContent'), zoom = Number($e('#timelineZoom').value);
    content.style.width = Math.max(scroll.clientWidth, duration * (1 + zoom * 1.5)) + 'px';
    const ruler = $e('#timelineRuler'), marks = 12; ruler.replaceChildren();
    for (let index = 0; index <= marks; index++) {
      const tick = document.createElement('span'); tick.className = 'timeline-tick'; tick.style.left = (100 * index / marks) + '%'; tick.textContent = formatTime(duration * index / marks); ruler.append(tick);
    }
    renderVideoSegments(); updateTimelinePlayhead();
  }
  $e('#timelineZoom').addEventListener('input', renderTimeline);
  $e('#timelineContent').addEventListener('pointerdown', event => {
    const segment = event.target.closest('.timeline-video-item'); if (segment) { event.preventDefault(); selectedVideoSegment = Number(segment.dataset.index); video.currentTime = videoSegments[selectedVideoSegment].sourceStart; renderVideoSegments(); updateTimelinePlayhead(); return; }
    const handle = event.target.closest('.timeline-handle');
    if (handle) { event.preventDefault(); timelineDragging = handle.dataset.edge; handle.setPointerCapture(event.pointerId); return; }
    if (event.target.closest('.timeline-track')) {
      const rect = $e('#timelineContent').getBoundingClientRect(), duration = sourceDuration();
      video.currentTime = Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration)); updateTimelinePlayhead();
    }
  });
  $e('#timelineContent').addEventListener('pointermove', event => {
    if (!timelineDragging) return;
    const rect = $e('#timelineContent').getBoundingClientRect(), duration = sourceDuration(), point = Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration));
    const item = videoSegments[selectedVideoSegment], others = videoSegments.filter((_, index) => index !== selectedVideoSegment), previousEnd = Math.max(0, ...others.filter(other => other.sourceEnd <= item.sourceStart + .001).map(other => other.sourceEnd)), nextStart = Math.min(duration, ...others.filter(other => other.sourceStart >= item.sourceEnd - .001).map(other => other.sourceStart));
    if (timelineDragging === 'in') item.sourceStart = Math.max(previousEnd, Math.min(point, item.sourceEnd - .2));
    else item.sourceEnd = Math.min(nextStart, Math.max(point, item.sourceStart + .2));
    syncVideoBounds(); fitAudioTracksToDuration(); renderAudioTracks(); renderVideoSegments(); updateSelection(); scheduleSave();
  });
  $e('#timelineContent').addEventListener('pointerup', () => { timelineDragging = null });
  function saveEdit() {
    clearTimeout(saveTimer);
    const snapshot = readEdit();
    setSaveState('Saving…');
    const request = saveChain.then(() => api('/api/clips/' + encodeURIComponent(clip.id) + '/edit', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...snapshot, baseVersion }) }));
    saveChain = request.catch(() => { });
    return request.then(result => { baseVersion = result.version; clip.edit_version = result.version; clip.edit = result.edit; const committed = JSON.parse(JSON.stringify(readEdit())); if (!historyAction && JSON.stringify(lastSavedSnapshot) !== JSON.stringify(committed)) { undoStack.push(lastSavedSnapshot); if (undoStack.length > 50) undoStack.shift(); redoStack.length = 0; } lastSavedSnapshot = committed; updateHistoryButtons(); const selector = $e('#editHistory'); if (result.id && !selector.querySelector('option[value="' + result.version + '"]')) { const option = document.createElement('option'); option.value = result.version; option.textContent = 'Version ' + result.version + ' · just now'; selector.append(option) } selector.value = String(result.version); $e('#restoreEdit').disabled = true; setSaveState('Saved · version ' + result.version); return result }).catch(error => { setSaveState('Save failed', true); toast(error.message); throw error });
  }
  function scheduleSave() { markPreviewStale(); updateSelection(); setSaveState('Unsaved changes'); clearTimeout(saveTimer); saveTimer = setTimeout(() => saveEdit().catch(() => { }), 500) }
  async function loadHistory() {
    try {
      const data = await api('/api/clips/' + encodeURIComponent(clip.id) + '/edits');
      const selector = $e('#editHistory'); selector.replaceChildren();
      data.versions.forEach(item => { const option = document.createElement('option'); option.value = item.version; option.textContent = 'Version ' + item.version + ' · ' + new Date(item.createdAt).toLocaleString(); selector.append(option) });
      if (data.versions.length) { selector.value = String(data.current.version); $e('#restoreEdit').disabled = true; }
      else { const option = document.createElement('option'); option.value = '0'; option.textContent = 'Unsaved draft'; selector.append(option); }
      setSaveState(data.current.version ? 'Saved · version ' + data.current.version : 'Unsaved draft');
      selector.onchange = () => { $e('#restoreEdit').disabled = Number(selector.value) === baseVersion || Number(selector.value) === 0 };
    } catch (error) { setSaveState('History unavailable', true); }
  }
  async function restoreSelectedVersion() {
    const version = Number($e('#editHistory').value); if (!version || version === baseVersion) return;
    clearTimeout(saveTimer); $e('#restoreEdit').disabled = true; setSaveState('Restoring…');
    try {
      const result = await api('/api/clips/' + encodeURIComponent(clip.id) + '/edits/' + version + '/restore', { method: 'POST' });
      undoStack.push(JSON.parse(JSON.stringify(lastSavedSnapshot))); redoStack.length = 0;
      markPreviewStale(); baseVersion = result.version; clip.edit_version = result.version; clip.edit = result.edit;
      $e('#editTitle').value = result.edit.title; $e('#editStart').value = result.edit.start; $e('#editEnd').value = result.edit.end;
      $e('#editAspect').value = result.edit.aspectRatio; $e('#portraitBackground').value = result.edit.portraitBackground || 'blurred'; updatePortraitFramingControl(); $e('#captionStyle').value = result.edit.captionStyle; $e('#burnCaptions').checked = result.edit.burnCaptions;
      $e('#cropX').value = String(result.edit.cropX ?? 0.5); $e('#cropY').value = String(result.edit.cropY ?? 0.5); updateCropPreview();
      $e('#fadeEffect').value = result.edit.fadeEffect || 'none'; $e('#playbackSpeed').value = String(result.edit.playbackSpeed ?? 1); $e('#colorFilter').value = result.edit.colorFilter || 'none'; $e('#motionEffect').value = result.edit.motionEffect || 'none'; updateVideoEffectPreview();
      $e('#outputResolution').value = String(result.edit.outputResolution ?? 1080); $e('#frameRate').value = String(result.edit.frameRate ?? 30); $e('#videoBitrate').value = String(result.edit.videoBitrate ?? 6000000);
      $e('#captionFont').value = result.edit.fontFamily || 'Arial'; $e('#captionColor').value = result.edit.captionColor || '#FFFFFF'; $e('#captionBackground').value = result.edit.captionBackground || '#141414'; $e('#captionHighlightColor').value = result.edit.captionHighlightColor || '#8AAB4A';
      $e('#logoAsset').value = result.edit.logoAssetId || '';
      $e('#musicAsset').value = result.edit.musicAssetId || ''; $e('#sourceVolume').value = result.edit.sourceVolume ?? 1; $e('#musicVolume').value = result.edit.musicVolume ?? 0.2; $e('#musicFadeIn').value = result.edit.musicFadeIn ?? 0; $e('#musicFadeOut').value = result.edit.musicFadeOut ?? 0;
      audioTracks = Array.isArray(result.edit.audioTracks) ? JSON.parse(JSON.stringify(result.edit.audioTracks)) : []; renderAudioTracks();
      videoSegments = Array.isArray(result.edit.videoSegments) && result.edit.videoSegments.length ? JSON.parse(JSON.stringify(result.edit.videoSegments)).map(item => ({ sourceStart: Number(item.sourceStart), sourceEnd: Number(item.sourceEnd) })) : [{ sourceStart: Number(result.edit.start), sourceEnd: Number(result.edit.end) }]; selectedVideoSegment = 0; renderVideoSegments();
      $e('#captionSize').value = result.edit.captionSize ?? 50; $e('#captionPosition').value = result.edit.captionPosition || 'bottom'; $e('#captionAlignment').value = result.edit.captionAlignment || 'center'; $e('#captionOutline').value = result.edit.captionOutline ?? 2; $e('#captionAnimation').value = result.edit.captionAnimation || 'none'; updateCaptionPreview();
      lastSavedSnapshot = JSON.parse(JSON.stringify(readEdit())); updateHistoryButtons(); updateSelection(); video.currentTime = result.edit.start; setSaveState('Restored as version ' + result.version); await loadHistory(); toast('Saved edit version restored.');
    } catch (error) { setSaveState('Restore failed', true); toast(error.message); }
  }
  const close = async () => {
    clearTimeout(saveTimer);
    if (JSON.stringify(readEdit()) !== JSON.stringify(lastSavedSnapshot)) {
      try {
        await saveChain;
        if (JSON.stringify(readEdit()) !== JSON.stringify(lastSavedSnapshot)) await saveEdit();
      } catch (error) { toast('Edits could not be saved; the editor is still open. ' + error.message); return; }
    }
    overlay.close();
    overlay.remove();
  };
  video.addEventListener('loadedmetadata', () => { video.currentTime = videoSegments[0].sourceStart; $e('#playhead').max = String(video.duration || 1); updateSelection(); renderTimeline() });
  video.addEventListener('timeupdate', () => { $e('#playhead').value = String(video.currentTime); $e('#playheadTime').textContent = formatTime(video.currentTime); updateTimelinePlayhead() });
  $e('#playhead').addEventListener('input', event => { video.currentTime = Number(event.target.value) });
  $e('#setInPoint').addEventListener('click', () => { const item = videoSegments[selectedVideoSegment], previousEnd = Math.max(0, ...videoSegments.filter((_, index) => index !== selectedVideoSegment && item.sourceStart >= _.sourceEnd).map(other => other.sourceEnd)); item.sourceStart = Math.max(previousEnd, Math.min(video.currentTime, item.sourceEnd - .2)); syncVideoBounds(); renderVideoSegments(); fitAudioTracksToDuration(); renderAudioTracks(); scheduleSave() });
  $e('#setOutPoint').addEventListener('click', () => { const item = videoSegments[selectedVideoSegment], duration = sourceDuration(), nextStart = Math.min(duration, ...videoSegments.filter((_, index) => index !== selectedVideoSegment && item.sourceEnd <= _.sourceStart).map(other => other.sourceStart)); item.sourceEnd = Math.min(nextStart, Math.max(video.currentTime, item.sourceStart + .2)); syncVideoBounds(); renderVideoSegments(); fitAudioTracksToDuration(); renderAudioTracks(); scheduleSave() });
  ['#editTitle', '#editStart', '#editEnd', '#editAspect', '#cropX', '#cropY', '#outputResolution', '#frameRate', '#videoBitrate', '#captionStyle', '#burnCaptions', '#captionFont', '#captionColor', '#captionBackground', '#captionHighlightColor', '#captionSize', '#captionPosition', '#captionAlignment', '#captionOutline', '#captionAnimation', '#logoAsset', '#musicAsset', '#sourceVolume', '#musicVolume', '#musicFadeIn', '#musicFadeOut'].forEach(selector => $e(selector).addEventListener('input', () => { updateCaptionPreview(); updateVideoEffectPreview(); if (selector === '#editAspect' || selector === '#cropX' || selector === '#cropY') updateCropPreview(); if (selector === '#editAspect') updatePortraitFramingControl(); if (selector === '#editStart' || selector === '#editEnd') { const first = videoSegments[0], last = videoSegments[videoSegments.length - 1]; if (selector === '#editStart') { const ceiling = videoSegments.length > 1 ? videoSegments[1].sourceStart - .2 : first.sourceEnd - .2; first.sourceStart = Math.max(0, Math.min(Number($e('#editStart').value), ceiling)); $e('#editStart').value = first.sourceStart.toFixed(1) } else { const floor = videoSegments.length > 1 ? last.sourceStart + .2 : first.sourceStart + .2; last.sourceEnd = Math.max(floor, Math.min(Number($e('#editEnd').value), sourceDuration())); $e('#editEnd').value = last.sourceEnd.toFixed(1) } fitAudioTracksToDuration(); renderAudioTracks(); renderVideoSegments() } scheduleSave() }));
  $e('#portraitBackground').addEventListener('change', scheduleSave);
  ['#outputResolution', '#frameRate', '#videoBitrate'].forEach(selector => $e(selector).addEventListener('change', scheduleSave));
  ['#logoAsset', '#musicAsset'].forEach(selector => $e(selector).addEventListener('change', scheduleSave));
  $e('#applyBrandDefaults').addEventListener('click', async () => { try { const { brand } = await api('/api/brand'), defaults = brand.caption_defaults; await Promise.all([populateAssets('#logoAsset', 'image', defaults.logoAssetId), populateAssets('#musicAsset', 'audio', defaults.musicAssetId)]); $e('#captionFont').value = defaults.fontFamily; $e('#captionColor').value = defaults.captionColor; $e('#captionBackground').value = defaults.captionBackground; $e('#captionHighlightColor').value = defaults.captionHighlightColor; $e('#captionStyle').value = defaults.captionStyle; $e('#captionSize').value = defaults.captionSize; $e('#captionPosition').value = defaults.captionPosition; $e('#captionAlignment').value = defaults.captionAlignment; $e('#captionOutline').value = defaults.captionOutline; $e('#captionAnimation').value = defaults.captionAnimation; $e('#sourceVolume').value = defaults.sourceVolume; $e('#musicVolume').value = defaults.musicVolume; $e('#musicFadeIn').value = defaults.musicFadeIn; $e('#musicFadeOut').value = defaults.musicFadeOut; updateCaptionPreview(); scheduleSave(); toast('Brand Kit defaults applied to this clip.'); } catch (error) { toast(error.message); } });
  $e('#closeClip').addEventListener('click', close);
  overlay.addEventListener('click', event => { if (event.target === overlay) close() });
  overlay.addEventListener('cancel', event => {
    event.preventDefault();
    close();
  });
  $e('#restoreEdit').addEventListener('click', restoreSelectedVersion);
  loadHistory(); updateSelection();
  async function waitForExport(jobId) {
    const panel = $e('.export-job-progress'), retry = $e('#retryExportJob'), cancel = $e('#cancelExportJob'), stage = $e('#exportJobStage'), percent = $e('#exportJobPercent'), bar = $e('#exportJobBar');
    panel.hidden = false;
    cancel.onclick = async () => { cancel.disabled = true; stage.textContent = 'Cancel requested…'; try { await api('/api/exports/' + encodeURIComponent(jobId), { method: 'DELETE' }) } catch (error) { toast(error.message) } };
    retry.onclick = async () => { retry.disabled = true; try { await api('/api/exports/' + encodeURIComponent(jobId) + '/retry', { method: 'POST' }); retry.hidden = true; retry.disabled = false } catch (error) { toast(error.message); retry.disabled = false } };
    while (true) {
      const { job, result } = await api('/api/exports/' + encodeURIComponent(jobId));
      stage.textContent = job.stage || job.status; percent.textContent = Math.max(0, Math.min(100, Number(job.progress) || 0)) + '%'; bar.value = Number(job.progress) || 0;
      if (job.status === 'Succeeded') { panel.hidden = true; return result }
      if (job.status === 'Canceled') throw new Error('Export canceled.');
      if (job.status === 'Failed') {
        cancel.hidden = true;
        if (job.attempts < job.max_attempts) { stage.textContent = job.error || 'Export failed. You can retry.'; retry.hidden = false; await new Promise(resolve => retry.onclick = async () => { retry.disabled = true; try { await api('/api/exports/' + encodeURIComponent(jobId) + '/retry', { method: 'POST' }); retry.hidden = true; cancel.hidden = false; retry.disabled = false; resolve() } catch (error) { toast(error.message); retry.disabled = false } }); continue }
        throw new Error(job.error || 'Export failed. The retry limit was reached.');
      }
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  }
  async function finishExport(result) {
    await refreshProject(project.id);
    showRenderedPreview(result.download);
    const button = $e('#exportClip'); button.disabled = false; button.innerHTML = 'Render preview <span>↻</span>';
    toast(result.captionAnimationRequested === 'word-highlight' && !result.captionAnimationApplied ? 'Word timings were unavailable; exported static captions.' : 'Clip exported from saved edit version ' + result.editVersion + '.');
  }
  $e('#exportClip').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true; button.textContent = 'Saving edit…';
    try {
      clearTimeout(saveTimer); await saveEdit(); button.textContent = 'Queueing export…';
      const queued = await api('/api/clips/' + encodeURIComponent(clip.id) + '/export-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ editVersion: baseVersion }) });
      button.textContent = 'Exporting…';
      const result = await waitForExport(queued.jobId);
      await finishExport(result);
    } catch (error) { toast(error.message); button.disabled = false; button.innerHTML = 'Render preview <span>▶</span>'; }
  });
  api('/api/clips/' + encodeURIComponent(clip.id) + '/export-jobs').then(async ({ jobs }) => {
    const active = jobs.find(job => job.status === 'Queued' || job.status === 'Running'); if (!active) return;
    const button = $e('#exportClip'); button.disabled = true; button.textContent = 'Resuming export…';
    try { await finishExport(await waitForExport(active.id)) } catch (error) { toast(error.message); button.disabled = false; button.innerHTML = 'Render preview <span>▶</span>' }
  }).catch(() => { });
}
