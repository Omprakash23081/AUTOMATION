// Workspace-scoped, searchable clip library.
(function(){
  let page=1,pages=1,searchTimer=null;
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[character]));
  const formatClipTime=seconds=>{seconds=Math.max(0,Math.floor(Number(seconds)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')};
  async function showAllClips(){
    const root=plannerNav('clips');
    root.innerHTML='<div class="module-head"><div><div class="eyebrow">YOUR WORKSPACE</div><h1>All clips</h1><p>Find clip drafts from every project, then reopen or download them.</p></div></div><div class="clip-library-tools"><label>Search clips<input id="clipLibrarySearch" type="search" placeholder="Title, transcript, or project"></label><label>Show<select id="clipLibraryFilter"><option value="">All clips</option><option value="0">Not exported</option><option value="1">Exported</option></select></label></div><div class="clip-library-count" id="clipLibraryCount"></div><div class="clip-library-grid" id="clipLibraryGrid"><div class="planner-loading">Loading clips…</div></div><nav class="project-pagination hidden" id="clipLibraryPagination"><button type="button" id="clipLibraryPrevious">← Previous</button><span id="clipLibraryPageLabel"></span><button type="button" id="clipLibraryNext">Next →</button></nav>';
    root.querySelector('#clipLibrarySearch').addEventListener('input',()=>{clearTimeout(searchTimer);page=1;searchTimer=setTimeout(loadClips,250)});
    root.querySelector('#clipLibraryFilter').addEventListener('change',()=>{page=1;loadClips()});
    root.querySelector('#clipLibraryPrevious').addEventListener('click',()=>{if(page>1){page--;loadClips()}});
    root.querySelector('#clipLibraryNext').addEventListener('click',()=>{if(page<pages){page++;loadClips()}});
    await loadClips();
  }
  async function loadClips(){
    const root=document.querySelector('#moduleView');if(!root)return;
    const grid=root.querySelector('#clipLibraryGrid');if(!grid)return;
    grid.innerHTML='<div class="planner-loading">Loading clips…</div>';
    const params=new URLSearchParams({page:String(page),perPage:'20',q:root.querySelector('#clipLibrarySearch').value.trim(),exported:root.querySelector('#clipLibraryFilter').value});
    try{
      const data=await api('/api/clips?'+params);page=data.page;pages=data.pages;
      root.querySelector('#clipLibraryCount').textContent=data.total+' clip'+(data.total===1?'':'s');
      const pagination=root.querySelector('#clipLibraryPagination');pagination.classList.toggle('hidden',pages<=1);
      root.querySelector('#clipLibraryPageLabel').textContent=`Page ${page} of ${pages}`;root.querySelector('#clipLibraryPrevious').disabled=page<=1;root.querySelector('#clipLibraryNext').disabled=page>=pages;
      if(!data.clips.length){grid.innerHTML='<div class="planner-empty">No clips match these filters yet. Generate clips from a video project to see them here.</div>';return;}
      grid.innerHTML=data.clips.map(clip=>'<article class="clip-library-card"><div class="clip-library-thumb '+(clip.has_thumbnail?'has-image':'')+'" '+(clip.has_thumbnail?'style="background-image:linear-gradient(180deg,#1112,#1119),url(\'/api/projects/'+encodeURIComponent(clip.project_id)+'/thumbnail\')"':'')+'><span>'+escapeHtml(clip.source_type==='youtube'?'YOUTUBE':'VIDEO')+'</span></div><div class="clip-library-body"><div class="clip-library-project">'+escapeHtml(clip.project_name)+'</div><h3>'+escapeHtml(clip.title)+'</h3><p>'+escapeHtml(clip.hook||clip.transcript||'')+'</p><div class="clip-library-meta"><span>'+formatClipTime(clip.start)+'–'+formatClipTime(clip.end)+'</span><span>Score '+Number(clip.score||0)+'</span><span class="clip-library-status '+(clip.exported?'done':'')+'">'+(clip.exported?'Exported':'Draft')+'</span></div><div class="clip-library-actions"><button type="button" data-open-clip="'+escapeHtml(clip.id)+'" data-project="'+escapeHtml(clip.project_id)+'">Edit</button>'+(clip.exported?'<button type="button" data-download-clip="'+escapeHtml(clip.id)+'">Download MP4</button><button type="button" data-download-captions="'+escapeHtml(clip.id)+'">Captions</button><button type="button" data-upload-youtube-clip="'+escapeHtml(clip.id)+'" data-clip-title="'+escapeHtml(clip.title)+'">Upload</button>':'')+'<button type="button" class="clip-delete-button" data-delete-clip="'+escapeHtml(clip.id)+'">Delete</button></div></div></article>').join('');
      grid.querySelectorAll('[data-open-clip]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{const {project}=await api('/api/projects/'+encodeURIComponent(button.dataset.project));const clip=project.clips.find(item=>item.id===button.dataset.openClip);if(!clip)throw new Error('Clip not found in this project.');openClipEditor(project,clip);}catch(error){toast(error.message);}finally{button.disabled=false;}}));
      grid.querySelectorAll('[data-download-clip]').forEach(button=>button.addEventListener('click',()=>{location.href='/api/clips/'+encodeURIComponent(button.dataset.downloadClip)+'/download'}));
      grid.querySelectorAll('[data-download-captions]').forEach(button=>button.addEventListener('click',()=>{location.href='/api/clips/'+encodeURIComponent(button.dataset.downloadCaptions)+'/captions'}));
      grid.querySelectorAll('[data-upload-youtube-clip]').forEach(button=>button.addEventListener('click',()=>{
        button.disabled=true;button.textContent='Preparing…';
        const clip=data.clips.find(item=>item.id===button.dataset.uploadYoutubeClip);
        window.dispatchEvent(new CustomEvent('cutroom:upload-clip-to-youtube',{detail:{clipId:button.dataset.uploadYoutubeClip,title:button.dataset.clipTitle,transcript:clip?.transcript||'',button}}));
      }));
      grid.querySelectorAll('[data-delete-clip]').forEach(button=>button.addEventListener('click',async()=>{if(!window.confirm('Permanently delete this clip and its rendered video? This cannot be undone.'))return;button.disabled=true;try{await api('/api/clips/'+encodeURIComponent(button.dataset.deleteClip),{method:'DELETE'});toast('Clip deleted.');await loadClips();}catch(error){toast(error.message);button.disabled=false;}}));
    }catch(error){grid.innerHTML='<div class="planner-empty">'+escapeHtml(error.message)+'</div>';}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('.nav-item[data-view="clips"]');if(!button)return;
    event.preventDefault();event.stopImmediatePropagation();showAllClips();
  },true);
})();
