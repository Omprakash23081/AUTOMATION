// Hydrate project cards and source details with private, workspace-scoped media metadata.
(function(){
  const checkedCards=new WeakSet();
  let detailId='',detailCheckedAt=0;
  const fileInput=document.querySelector('#fileInput');
  if(fileInput)fileInput.accept='.mp4,.mov,.webm,.m4v,.mp3,.m4a,.wav,video/mp4,video/quicktime,video/webm,audio/mpeg,audio/mp4,audio/wav,audio/x-wav';
  window.uploadMediaWithProgress=function(url,body){
    return new Promise((resolve,reject)=>{
      const dialog=document.querySelector('#setupDialog');
      const detailPanel=document.querySelector('#projectDetail .source-panel');
      let panel=(dialog||detailPanel)?.querySelector('.upload-progress');
      if(!panel&&(dialog||detailPanel)){
        panel=document.createElement('div');panel.className='upload-progress';
        panel.innerHTML='<div class="upload-progress-label"></div><progress max="100" value="0"></progress><button type="button">Cancel upload</button>';
        if(dialog)dialog.querySelector('.dialog-foot')?.before(panel);else detailPanel.append(panel);
      }
      if(!panel){reject(new Error('Upload progress panel is unavailable.'));return;}
      const file=body.get('file'),label=panel.querySelector('.upload-progress-label'),bar=panel.querySelector('progress');
      if(!file){reject(new Error('Choose a media file to upload.'));return;}
      const projectId=url.match(/\/api\/projects\/([^/]+)\/upload/)?.[1]||'';
      const storageKey='cutroom-upload:'+projectId+':'+file.name+':'+file.size+':'+file.lastModified;
      let uploadId=localStorage.getItem(storageKey)||'',activeXhr=null,cancelled=false,settled=false;
      const cancel=panel.querySelector('button');
      panel.hidden=false;bar.value=0;cancel.disabled=false;cancel.textContent='Cancel upload';
      const finish=(error,result)=>{if(settled)return;settled=true;cancel.onclick=null;if(error)reject(error);else resolve(result)};
      const requestJson=async(path,method='GET',data=null)=>{
        const token=await window.getCsrfToken(),options={method,headers:{'X-CSRF-Token':token}};
        if(data){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
        const response=await fetch(path,options);let payload={};try{payload=await response.json()}catch{}
        if(!response.ok)throw new Error(payload.error||'Upload request failed ('+response.status+')');
        return payload;
      };
      const cancelSession=async()=>{
        if(cancelled)return;cancelled=true;cancel.disabled=true;cancel.textContent='Cancelling…';activeXhr?.abort();
        if(uploadId){try{await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId),'DELETE')}catch{}}
        localStorage.removeItem(storageKey);panel.hidden=true;
        finish(new Error('Upload cancelled and temporary data discarded.'));
      };
      cancel.onclick=cancelSession;
      const showProgress=offset=>{const percent=Math.min(100,Math.round(offset/file.size*100));bar.value=percent;label.textContent='Uploading '+file.name+' · '+percent+'% · resumable';};
      const sendChunk=(offset,end,attempt=0)=>new Promise((resolveChunk,rejectChunk)=>{
        if(cancelled){rejectChunk(new Error('Upload cancelled.'));return;}
        const xhr=new XMLHttpRequest();activeXhr=xhr;xhr.withCredentials=true;
        xhr.open('PUT',window.cutroomApiUrl('/api/upload-sessions/'+encodeURIComponent(uploadId)+'/chunk?offset='+offset));
        window.getCsrfToken().then(token=>{
          if(cancelled){xhr.abort();return;}
          xhr.setRequestHeader('X-CSRF-Token',token);xhr.setRequestHeader('Content-Type','application/octet-stream');
          xhr.upload.onprogress=event=>{if(event.lengthComputable)showProgress(offset+Math.min(end-offset,event.loaded));};
          xhr.onload=async()=>{
            let result={};try{result=JSON.parse(xhr.responseText||'{}')}catch{}
            if(xhr.status>=200&&xhr.status<300){resolveChunk(result.offset);return;}
            if(xhr.status===409){try{const status=await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId));resolveChunk(status.offset);return;}catch{}}
            rejectChunk(new Error(result.error||'Chunk upload failed ('+xhr.status+')'));
          };
          xhr.onerror=async()=>{
            if(attempt<3&&!cancelled){
              try{const status=await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId));resolveChunk(status.offset);return;}
              catch(error){if(attempt<2){setTimeout(()=>sendChunk(offset,end,attempt+1).then(resolveChunk,rejectChunk),350*(attempt+1));return;}}
            }
            rejectChunk(new Error('Connection interrupted. Your upload is saved; select the same file to resume.'));
          };
          xhr.onabort=()=>rejectChunk(new Error('Upload cancelled.'));
          xhr.send(file.slice(offset,end));
        }).catch(rejectChunk);
      });
      (async()=>{
        try{
          let status=null;
          if(uploadId){try{status=await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId));if(status.filename!==file.name||status.expectedBytes!==file.size)status=null;}catch{status=null;}}
          if(!status){
            if(uploadId){try{await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId),'DELETE')}catch{}}
            const started=await requestJson('/api/projects/'+encodeURIComponent(projectId)+'/upload-sessions','POST',{filename:file.name,size:file.size});
            uploadId=started.uploadId;status={offset:started.offset,expectedBytes:file.size,chunkSize:started.chunkSize};localStorage.setItem(storageKey,uploadId);
          }
          let offset=status.offset,chunkSize=status.chunkSize||8*1024*1024;
          showProgress(offset);
          while(offset<file.size){
            const end=Math.min(file.size,offset+chunkSize);
            offset=await sendChunk(offset,end);
            if(!Number.isFinite(offset)||offset<0||offset>end)throw new Error('Server returned an invalid upload offset.');
            showProgress(offset);
          }
          label.textContent='Checking '+file.name+'…';
          const completed=await requestJson('/api/upload-sessions/'+encodeURIComponent(uploadId)+'/complete','POST',{});
          localStorage.removeItem(storageKey);panel.hidden=true;finish(null,completed);
        }catch(error){
          if(!cancelled){const percent=Math.min(100,Math.round(bar.value||0));label.textContent='Upload paused at '+percent+'%. Select the same file to resume, or discard it.';cancel.textContent='Discard upload';cancel.disabled=false;cancel.onclick=cancelSession;}
          finish(error);
        }
      })();
    });
  };
  function hydrateCards(){
    document.querySelectorAll('.project-card').forEach(card=>{
      if(checkedCards.has(card))return;
      checkedCards.add(card);
      const id=card.dataset.project;
      const thumb=card.querySelector('.thumb');
      if(!id||!thumb)return;
      const url='/api/projects/'+encodeURIComponent(id)+'/thumbnail';
      fetch(url,{method:'HEAD'}).then(response=>{
        if(response.ok){thumb.style.backgroundImage=`linear-gradient(180deg,rgba(15,17,22,.08),rgba(15,17,22,.58)),url('${url}')`;thumb.style.backgroundSize='cover';thumb.style.backgroundPosition='center';}
      }).catch(()=>{});
    });
  }
  function hydrateSource(){
    const video=document.querySelector('#projectDetail .source-panel video');
    const panel=video?.closest('.source-panel');
    const match=video?.getAttribute('src')?.match(/^\/api\/projects\/([a-f0-9]+)\/source$/i);
    if(!panel||!match)return;
    const id=match[1],now=Date.now();
    if(detailId===id&&now-detailCheckedAt<3000)return;
    detailId=id;detailCheckedAt=now;
    fetch('/api/projects/'+encodeURIComponent(id)).then(response=>response.ok?response.json():null).then(data=>{
      if(!data?.project||detailId!==id||!panel.isConnected)return;
      const p=data.project;
      const properties=[];
      if(p.media_width&&p.media_height)properties.push(`${p.media_width} × ${p.media_height}`);
      if(p.media_fps)properties.push(`${Number(p.media_fps).toFixed(2)} fps`);
      if(p.media_has_video!==null&&p.media_has_video!==undefined)properties.push(p.media_has_video?'Video':'Audio only');
      if(p.media_has_audio!==null&&p.media_has_audio!==undefined)properties.push(p.media_has_audio?'Audio':'No audio');
      let row=panel.querySelector('.source-properties');
      if(!row){row=document.createElement('div');row.className='source-properties';panel.querySelector('.source-name')?.after(row);}
      row.textContent=properties.join(' · ')||'Media properties will appear after source inspection.';
      if(!p.source_name&&p.source_type==='upload'&&!panel.querySelector('.source-upload-retry')){
        const retry=document.createElement('div');retry.className='source-upload-retry';
        retry.innerHTML='<label>Choose a video or audio file<input type="file" accept=".mp4,.mov,.webm,.m4v,.mp3,.m4a,.wav"></label><button type="button" disabled>Upload source</button>';
        panel.append(retry);
        const input=retry.querySelector('input'),button=retry.querySelector('button');
        input.addEventListener('change',()=>{button.disabled=!input.files?.length;});
        button.addEventListener('click',async()=>{
          const file=input.files?.[0];if(!file)return;
          const body=new FormData();body.append('file',file,file.name);button.disabled=true;button.textContent='Uploading…';
          try{await window.uploadMediaWithProgress('/api/projects/'+encodeURIComponent(id)+'/upload',body);window.location.reload();}
          catch(error){button.disabled=false;button.textContent='Upload source';const toast=document.querySelector('#toast');if(toast){toast.textContent=error.message;toast.classList.add('visible');setTimeout(()=>toast.classList.remove('visible'),3600);}}
        });
      }
      if(p.source_name&&(p.media_has_video===false||p.media_has_video===0)&&p.media_has_audio){
        const source=panel.querySelector('video');
        if(source){const audio=document.createElement('audio');audio.controls=true;audio.preload='metadata';audio.src=source.src;audio.className='source-audio';source.replaceWith(audio);}
      }
    }).catch(()=>{});
  }
  const projects=document.querySelector('#projectGrid');
  if(projects)new MutationObserver(hydrateCards).observe(projects,{childList:true});
  const detail=document.querySelector('#projectDetail');
  if(detail)new MutationObserver(hydrateSource).observe(detail,{childList:true,subtree:true});
  hydrateCards();hydrateSource();
})();
