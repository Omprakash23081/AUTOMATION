// Workspace brand defaults that feed versioned clip captions and FFmpeg exports.
(function(){
  const fonts=['Arial','DejaVu Sans','Liberation Sans'];
  async function showBrandKit(){
    const root=plannerNav('brand');
    root.innerHTML='<div class="module-head"><div><div class="eyebrow">WORKSPACE IDENTITY</div><h1>Brand kit</h1><p>Save your default caption and audio style for every new clip. Existing edits stay unchanged until you apply the defaults in the clip editor.</p></div></div><div class="brand-kit-loading">Loading brand settings…</div>';
    try{
      const [{brand},{assets}]=await Promise.all([api('/api/brand'),api('/api/assets')]);
      const defaults=brand.caption_defaults;
      const assetOptions=(type,selected)=>'<option value="">No '+type+'</option>'+assets.filter(asset=>asset.type===(type==='logo'?'image':'audio')).map(asset=>'<option value="'+esc(asset.id)+'" '+(asset.id===selected?'selected':'')+'>'+esc(asset.name)+'</option>').join('');
      root.innerHTML='<div class="module-head"><div><div class="eyebrow">WORKSPACE IDENTITY</div><h1>Brand kit</h1><p>Save your default caption and audio style for every new clip. Existing edits stay unchanged until you apply the defaults in the clip editor.</p></div></div><div class="brand-kit-layout"><form id="brandKitForm" class="brand-kit-form"><label>Brand name<input name="brandName" maxlength="80" required value="'+esc(brand.brand_name)+'"></label><label>Accent / highlight color<input name="accentColor" type="color" value="'+brand.accent_color+'"></label><label>Caption font<select name="fontFamily">'+fonts.map(font=>'<option '+(font===brand.font_family?'selected':'')+'>'+font+'</option>').join('')+'</select></label><label>Default caption preset<select name="defaultCaptionStyle"><option value="classic">Classic</option><option value="bold">Bold</option><option value="boxed">Boxed</option></select></label><label>Caption text color<input name="captionColor" type="color" value="'+brand.caption_color+'"></label><label>Caption box color<input name="captionBackground" type="color" value="'+brand.caption_background+'"></label><label>Caption size (%)<input name="captionSize" type="number" min="40" max="160" step="5" value="'+defaults.captionSize+'"></label><label>Vertical position<select name="captionPosition"><option value="bottom">Bottom · safe area</option><option value="middle">Middle</option><option value="top">Top · safe area</option></select></label><label>Text alignment<select name="captionAlignment"><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></label><label>Outline<select name="captionOutline"><option value="0">None</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option></select></label><label>Caption animation<select name="captionAnimation"><option value="none">Static captions</option><option value="word-highlight">Highlight each spoken word</option></select></label><label>Logo overlay<select name="logoAssetId">'+assetOptions('logo',defaults.logoAssetId)+'</select></label><label>Background music<select name="musicAssetId">'+assetOptions('music',defaults.musicAssetId)+'</select></label><label>Source volume (0–2)<input name="sourceVolume" type="number" min="0" max="2" step="0.05" value="'+defaults.sourceVolume+'"></label><label>Music volume (0–2)<input name="musicVolume" type="number" min="0" max="2" step="0.05" value="'+defaults.musicVolume+'"></label><label>Fade in (seconds)<input name="musicFadeIn" type="number" min="0" max="10" step="0.1" value="'+defaults.musicFadeIn+'"></label><label>Fade out (seconds)<input name="musicFadeOut" type="number" min="0" max="10" step="0.1" value="'+defaults.musicFadeOut+'"></label><div class="brand-kit-actions"><span id="brandKitStatus">Saved workspace defaults</span><button class="primary-button" type="submit">Save Brand Kit</button></div></form><aside class="brand-preview-card"><div class="eyebrow">CAPTION PREVIEW</div><div class="brand-video-preview"><div id="brandCaptionPreview">A moment worth sharing</div></div><p>New clip edits use these settings automatically. To update an existing edit, choose “Use Brand Kit defaults” in its editor.</p></aside></div>';
      const form=root.querySelector('#brandKitForm');
      form.elements.defaultCaptionStyle.value=brand.default_caption_style;
      form.elements.captionPosition.value=defaults.captionPosition;
      form.elements.captionAlignment.value=defaults.captionAlignment;
      form.elements.captionOutline.value=String(defaults.captionOutline);
      form.elements.captionAnimation.value=defaults.captionAnimation;
      const preview=root.querySelector('#brandCaptionPreview');
      function updatePreview(){
        preview.style.color=form.elements.captionColor.value;
        preview.style.backgroundColor=form.elements.defaultCaptionStyle.value==='boxed'?form.elements.captionBackground.value:'transparent';
        preview.style.fontFamily=form.elements.fontFamily.value;
        preview.style.fontSize=Math.max(12,Number(form.elements.captionSize.value)*0.4)+'px';
        preview.style.fontWeight=form.elements.defaultCaptionStyle.value==='bold'?'800':'600';
        preview.style.textAlign=form.elements.captionAlignment.value;
        preview.style.borderBottom='4px solid '+form.elements.accentColor.value;
        preview.style.textShadow=Number(form.elements.captionOutline.value)?'1px 1px 0 #000,-1px -1px 0 #000':'none';
      }
      form.addEventListener('input',updatePreview);form.addEventListener('change',updatePreview);updatePreview();
      form.addEventListener('submit',async event=>{
        event.preventDefault();
        const button=form.querySelector('button[type="submit"]'),status=root.querySelector('#brandKitStatus');
        button.disabled=true;status.textContent='Saving…';
        const values=Object.fromEntries(new FormData(form).entries());
        try{
          const result=await api('/api/brand',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
          form.elements.accentColor.value=result.brand.accent_color;
          form.elements.captionColor.value=result.brand.caption_color;
          form.elements.captionBackground.value=result.brand.caption_background;
          status.textContent='Saved · '+new Date(result.brand.updated_at).toLocaleTimeString();
          updatePreview();toast('Brand Kit defaults saved to this workspace.');
        }catch(error){status.textContent=error.message;toast(error.message);}
        finally{button.disabled=false;}
      });
    }catch(error){root.querySelector('.brand-kit-loading').textContent=error.message;}
  }
  document.addEventListener('click',event=>{const button=event.target.closest('.nav-item[data-view="brand"]');if(!button)return;event.preventDefault();event.stopImmediatePropagation();showBrandKit();},true);
})();
