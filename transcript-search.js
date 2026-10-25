// Search and edit persisted transcript segments inside each project workspace.
const refreshProjectWithTranscriptTools=window.refreshProject;
window.refreshProject=async function(projectId){
  await refreshProjectWithTranscriptTools(projectId);
  const clipList=document.querySelector('.clip-list');
  if(!clipList||document.querySelector('.transcript-tool'))return;
  const panel=document.createElement('section');
  panel.className='transcript-tool';
  panel.innerHTML='<div class="transcript-tool-head"><div><b>Transcript</b><span>Correct text, remove segments, or search for moments</span></div><div class="transcript-tool-actions"><button type="button" data-load-transcript>Load transcript</button><button type="button" data-show-removed>Show removed</button></div></div><form class="transcript-search"><input name="q" type="search" placeholder="Search the transcript…" aria-label="Search transcript"><button type="submit">Search</button></form><div class="transcript-results" aria-live="polite"></div><div class="transcript-editor-list"></div><button type="button" class="transcript-load-more hidden">Load more transcript</button>';
  clipList.before(panel);
  const results=panel.querySelector('.transcript-results'),list=panel.querySelector('.transcript-editor-list'),loadMore=panel.querySelector('.transcript-load-more');
  let offset=0,total=0,includeDeleted=false,loading=false;
  const endpoint='/api/projects/'+encodeURIComponent(projectId)+'/transcript';
  const notify=message=>{if(typeof toast==='function')toast(message)};
  function makeLine(segment){
    const row=document.createElement('article');row.className='transcript-line'+(segment.is_deleted?' removed':'');row.dataset.segmentId=segment.id;
    const jump=document.createElement('button');jump.type='button';jump.className='transcript-time';jump.textContent=formatTime(segment.start);jump.title='Seek source video to this moment';
    jump.addEventListener('click',()=>{const source=document.querySelector('#projectDetail .source-panel video,#projectDetail .source-panel audio');if(source){source.currentTime=segment.start;source.play().catch(()=>{});}});
    const text=document.createElement('textarea');text.rows=2;text.maxLength=5000;text.value=segment.text;text.disabled=Boolean(segment.is_deleted);text.setAttribute('aria-label','Transcript text at '+formatTime(segment.start));
    const controls=document.createElement('div');controls.className='transcript-line-controls';
    const state=document.createElement('span');state.className='transcript-edit-state';state.textContent='';
    if(segment.is_deleted){
      const restore=document.createElement('button');restore.type='button';restore.textContent='Restore';restore.addEventListener('click',async()=>{restore.disabled=true;try{await api(endpoint+'/'+segment.id+'/restore',{method:'POST'});await loadTranscript(false);notify('Transcript segment restored.');}catch(error){restore.disabled=false;state.textContent=error.message;}});controls.append(restore);
    }else{
      const save=document.createElement('button');save.type='button';save.textContent='Save edit';save.disabled=true;
      text.addEventListener('input',()=>{save.disabled=text.value.trim()===segment.text;state.textContent=save.disabled?'':'Unsaved edit';});
      save.addEventListener('click',async()=>{save.disabled=true;state.textContent='Saving…';try{const result=await api(endpoint+'/'+segment.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text.value,baseRevision:segment.revision})});segment.text=result.segment.text;segment.revision=result.segment.revision;text.value=segment.text;state.textContent='Saved';notify('Transcript correction saved.');}catch(error){save.disabled=false;state.textContent=error.message;}});
      const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.addEventListener('click',async()=>{remove.disabled=true;state.textContent='Removing…';try{await api(endpoint+'/'+segment.id,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({baseRevision:segment.revision})});row.remove();notify('Transcript segment removed. Use Show removed to restore it.');}catch(error){remove.disabled=false;state.textContent=error.message;}});
      const draft=document.createElement('button');draft.type='button';draft.textContent='Create clip draft';draft.addEventListener('click',()=>createDraft(segment,draft));
      controls.append(save,remove,draft);
    }
    controls.append(state);row.append(jump,text,controls);return row;
  }
  async function createDraft(segment,button){
    button.disabled=true;button.textContent='Creating…';
    try{const project=await api('/api/projects/'+encodeURIComponent(projectId));const start=Math.max(0,segment.start-8),end=Math.min(project.project.duration||segment.end+22,Math.max(segment.end,segment.start+22));await api('/api/projects/'+encodeURIComponent(projectId)+'/clips/from-transcript',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,end,title:segment.text.slice(0,72)})});await window.refreshProject(projectId);notify('Transcript clip added to your drafts.');}
    catch(error){button.disabled=false;button.textContent='Create clip draft';notify(error.message);}
  }
  async function loadTranscript(append=false){
    if(loading)return;loading=true;
    const button=panel.querySelector('[data-load-transcript]');button.disabled=true;button.textContent='Loading…';
    if(!append){offset=0;list.replaceChildren();}
    try{
      const query=new URLSearchParams({limit:'100',offset:String(offset),includeDeleted:includeDeleted?'1':'0'});
      const data=await api(endpoint+'?'+query);total=data.total;
      data.segments.forEach(segment=>list.append(makeLine(segment)));
      offset+=data.segments.length;loadMore.classList.toggle('hidden',offset>=total);
      button.textContent='Reload transcript';
      if(!total)list.innerHTML='<div class="transcript-none">No transcript segments are available yet.</div>';
    }catch(error){button.textContent='Retry loading';list.textContent=error.message;}
    finally{button.disabled=false;loading=false;}
  }
  panel.querySelector('[data-load-transcript]').addEventListener('click',()=>loadTranscript(false));
  panel.querySelector('[data-show-removed]').addEventListener('click',event=>{includeDeleted=!includeDeleted;event.currentTarget.textContent=includeDeleted?'Hide removed':'Show removed';loadTranscript(false);});
  loadMore.addEventListener('click',()=>loadTranscript(true));
  panel.querySelector('.transcript-search').addEventListener('submit',async event=>{
    event.preventDefault();const query=new FormData(event.currentTarget).get('q').toString().trim();
    if(!query){notify('Enter a word or phrase to search.');return;}
    results.textContent='Searching transcript…';
    try{
      const data=await api(endpoint+'?q='+encodeURIComponent(query)+'&limit=100'+(includeDeleted?'&includeDeleted=1':''));
      results.replaceChildren();
      if(!data.segments.length){results.innerHTML='<div class="transcript-none">No matching transcript lines. Try another phrase.</div>';return;}
      data.segments.forEach(segment=>results.append(makeLine(segment)));
    }catch(error){results.textContent=error.message;}
  });
  loadTranscript(false);
};
