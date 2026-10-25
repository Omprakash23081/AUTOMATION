// Authentication UI and an API response hook that opens the sign-in gate on expired sessions.
const nativeFetch=window.fetch.bind(window);
const apiUrl=window.cutroomApiUrl||((path)=>path);
const apiOrigin=new URL(apiUrl('/api/'),location.href).origin;
let csrfToken='';
window.getCsrfToken=async function(force=false){
  if(csrfToken&&!force)return csrfToken;
  const response=await nativeFetch(apiUrl('/api/csrf'),{credentials:'include'});
  if(!response.ok)throw new Error('Could not initialize the request security token.');
  const data=await response.json();csrfToken=data.csrfToken||'';return csrfToken;
};
window.fetch=async(input,init={})=>{
  const rawUrl=typeof input==='string'?input:input.url,initialUrl=new URL(rawUrl,location.href);
  const rewrittenUrl=initialUrl.pathname.startsWith('/api/')?apiUrl(initialUrl.pathname+initialUrl.search+initialUrl.hash):rawUrl;
  const url=new URL(rewrittenUrl,location.href),method=(init.method||(input instanceof Request?input.method:'GET')).toUpperCase();
  const isApi=url.origin===apiOrigin&&url.pathname.startsWith('/api/'),openAuth=['/api/auth/register','/api/auth/login','/api/auth/logout'].includes(url.pathname);
  const options={...init};
  if(isApi)options.credentials='include';
  if(isApi&&['POST','PUT','PATCH','DELETE'].includes(method)&&!openAuth){
    const headers=new Headers(init.headers||(input instanceof Request?input.headers:undefined));
    headers.set('X-CSRF-Token',await window.getCsrfToken());options.headers=headers;
  }
  const fetchInput=input instanceof Request?new Request(rewrittenUrl,input):rewrittenUrl;
  const response=await nativeFetch(fetchInput,options);
  if(isApi){
    try{const body=await response.clone().json();if(body.csrfToken)csrfToken=body.csrfToken;if(response.status===401&&body.auth_required)document.getElementById('authScreen')?.classList.remove('hidden')}catch{}
  }
  return response;
};
document.addEventListener('DOMContentLoaded',()=>{
  const screen=document.getElementById('authScreen'),form=document.getElementById('authForm');
  let mode='register';
  function setMode(next){
    mode=next;document.getElementById('authHeading').textContent=mode==='register'?'Create your creator workspace':mode==='forgot'?'Reset your password':'Welcome back';
    document.getElementById('authSubmit').textContent=mode==='register'?'Create account':mode==='forgot'?'Send reset link':'Sign in';
    document.getElementById('authToggle').textContent=mode==='register'?'Already have an account? Sign in':'Back to sign in';
    document.querySelectorAll('[data-register-only]').forEach(el=>el.classList.toggle('hidden',mode!=='register'));
    document.querySelectorAll('[data-password-only]').forEach(el=>{el.classList.toggle('hidden',mode==='forgot');const input=el.querySelector('input');if(input)input.required=mode!=='forgot'});
    document.getElementById('forgotPassword').classList.toggle('hidden',mode!=='login');
    document.getElementById('resendVerification').classList.add('hidden');
    document.getElementById('authError').classList.add('hidden');
  }
  setMode('register');
  async function refreshIdentity(){
    const profile=await api('/api/auth/me');
    document.querySelector('.workspace b').textContent=profile.workspace.name;
    document.querySelector('.profile b').textContent=profile.user.display_name;
    document.querySelector('.profile small').textContent='Sign out · '+profile.user.email;
    document.getElementById('accountSettingsButton').textContent=(profile.user.display_name||profile.user.email).trim().split(/\s+/).slice(0,2).map(part=>part[0]).join('').toUpperCase();
    document.getElementById('accountIdentity').textContent=profile.user.display_name+' · '+profile.user.email;
    document.getElementById('teamAdmin').classList.toggle('hidden',!['owner','admin'].includes(profile.workspace.role));
    const keyButton=document.getElementById('keyButton'),canManageAiKey=['owner','admin'].includes(profile.workspace.role);keyButton.disabled=!canManageAiKey;keyButton.title=canManageAiKey?'Manage this workspace’s OpenAI and Gemini keys':'Ask a workspace owner or admin to connect or change AI provider keys.';
    const workspaceData=await api('/api/workspaces');
    const list=document.getElementById('workspaceList');list.replaceChildren();
    workspaceData.workspaces.forEach(workspace=>{const button=document.createElement('button');button.type='button';button.className='workspace-option'+(workspace.id===profile.workspace.id?' selected':'');button.textContent=workspace.name+' · '+workspace.role;button.addEventListener('click',async()=>{try{await api('/api/workspaces/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:workspace.id})});document.getElementById('workspaceDialog').close();await load();await refreshIdentity()}catch(error){showWorkspaceError(error.message)}});list.append(button)});
    if(['owner','admin'].includes(profile.workspace.role)){
      try{
        const memberData=await api('/api/workspaces/members'),memberList=document.getElementById('workspaceMemberList');memberList.replaceChildren();
        memberData.members.forEach(member=>{
          const row=document.createElement('div');row.className='workspace-member';
          const identity=document.createElement('span');identity.className='workspace-member-identity';identity.textContent=member.display_name+' · '+member.email+' · '+member.role;row.append(identity);
          const actions=document.createElement('span');actions.className='workspace-member-actions';
          if(memberData.actorRole==='owner'&&member.role!=='owner'){
            const transfer=document.createElement('button');transfer.type='button';transfer.textContent='Make owner';transfer.addEventListener('click',async()=>{if(!window.confirm('Transfer workspace ownership to '+member.email+'? You will become an admin.'))return;try{await api('/api/workspaces/members/transfer-owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:member.id})});toast('Workspace ownership transferred.');await refreshIdentity()}catch(error){showWorkspaceError(error.message)}});actions.append(transfer);
          }
          if(member.id!==profile.user.id&&member.role!=='owner'&&(memberData.actorRole==='owner'||['editor','viewer'].includes(member.role))){
            const remove=document.createElement('button');remove.type='button';remove.className='remove-member';remove.textContent='Remove';remove.addEventListener('click',async()=>{if(!window.confirm('Remove '+member.email+' from this workspace?'))return;try{await api('/api/workspaces/members/'+encodeURIComponent(member.id),{method:'DELETE'});toast('Member removed.');await refreshIdentity()}catch(error){showWorkspaceError(error.message)}});actions.append(remove);
          }
          if(actions.childElementCount)row.append(actions);memberList.append(row);
        });
      }catch(error){showWorkspaceError(error.message)}
    }
  }
  function showWorkspaceError(message){const error=document.getElementById('workspaceError');error.textContent=message;error.classList.remove('hidden')}
  refreshIdentity().catch(()=>{});
  document.getElementById('workspaceButton').addEventListener('click',async()=>{document.getElementById('workspaceError').classList.add('hidden');try{await refreshIdentity();document.getElementById('workspaceDialog').showModal()}catch(error){showWorkspaceError(error.message)}});
  document.getElementById('accountSettingsButton').addEventListener('click',()=>{document.getElementById('accountDeleteError').classList.add('hidden');document.getElementById('accountDialog').showModal()});
  document.getElementById('deleteAccountForm').addEventListener('submit',async event=>{
    event.preventDefault();const deleteForm=event.currentTarget,fields=new FormData(deleteForm),button=deleteForm.querySelector('button[type="submit"]');button.disabled=true;
    try{await api('/api/auth/account',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:fields.get('password'),confirmation:fields.get('confirmation')})});document.getElementById('accountDialog').close();deleteForm.reset();document.getElementById('accountSettingsButton').textContent='OK';document.getElementById('accountIdentity').textContent='';screen.classList.remove('hidden');setMode('login');form.elements.email.value='';document.getElementById('authError').textContent='Your account and private workspace data were deleted.';document.getElementById('authError').classList.remove('hidden');toast('Account deleted.')}
    catch(error){document.getElementById('accountDeleteError').textContent=error.message;document.getElementById('accountDeleteError').classList.remove('hidden')}
    finally{button.disabled=false}
  });
  document.getElementById('joinWorkspaceForm').addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/workspaces/invites/accept',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:document.getElementById('inviteToken').value.trim()})});document.getElementById('inviteToken').value='';await load();await refreshIdentity();toast('Joined workspace.')}catch(error){showWorkspaceError(error.message)}});
  document.getElementById('inviteForm').addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/workspaces/invites',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('inviteEmail').value,role:document.getElementById('inviteRole').value})});const box=document.getElementById('inviteResult');box.textContent='Invitation token: '+result.inviteToken+' — share this securely with '+result.email+'.';box.classList.remove('hidden');document.getElementById('inviteEmail').value=''}catch(error){showWorkspaceError(error.message)}});
  document.getElementById('authToggle').addEventListener('click',()=>setMode(mode==='register'?'login':'login'));
  document.getElementById('forgotPassword').addEventListener('click',()=>setMode('forgot'));
  document.getElementById('resendVerification').addEventListener('click',async()=>{try{await api('/api/auth/verification/resend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.elements.email.value})});toast('If that account needs verification, an email will be sent.')}catch(error){document.getElementById('authError').textContent=error.message;document.getElementById('authError').classList.remove('hidden')}});
  form.addEventListener('submit',async event=>{
    event.preventDefault();const fields=new FormData(form),button=document.getElementById('authSubmit');button.disabled=true;
    try{
      if(mode==='forgot'){
        const result=await api('/api/auth/password/forgot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:fields.get('email')})});
        setMode('login');document.getElementById('authError').textContent=result.message;document.getElementById('authError').classList.remove('hidden');return;
      }
      const data={email:fields.get('email'),password:fields.get('password'),displayName:fields.get('displayName'),workspaceName:fields.get('workspaceName')};
      const result=await api(mode==='register'?'/api/auth/register':'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if(result.verification_required){document.getElementById('authError').textContent=result.message||result.error||'Verify your email before continuing. Check your inbox or resend the link.';document.getElementById('authError').classList.remove('hidden');document.getElementById('resendVerification').classList.remove('hidden');return;}
      screen.classList.add('hidden');toast(mode==='register'?'Workspace created.':'Signed in.');await load();await refreshIdentity();
    }catch(error){document.getElementById('authError').textContent=error.message;document.getElementById('authError').classList.remove('hidden');if(/verif/i.test(error.message))document.getElementById('resendVerification').classList.remove('hidden')}
    finally{button.disabled=false}
  });
  document.getElementById('resetPasswordForm').addEventListener('submit',async event=>{
    event.preventDefault();const params=new URLSearchParams(location.search),token=document.getElementById('resetToken').value||params.get('reset')||'',password=document.getElementById('resetPassword').value;
    try{await api('/api/auth/password/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,password})});history.replaceState({},'',location.pathname);document.getElementById('resetPanel').classList.add('hidden');form.classList.remove('hidden');document.getElementById('resetPassword').value='';setMode('login');document.getElementById('authError').textContent='Password changed. Sign in with your new password.';document.getElementById('authError').classList.remove('hidden');toast('Password changed.');}
    catch(error){document.getElementById('resetError').textContent=error.message;document.getElementById('resetError').classList.remove('hidden')}
  });
  const params=new URLSearchParams(location.search),resetToken=params.get('reset'),verifyToken=params.get('verify');
  if(resetToken){screen.classList.remove('hidden');form.classList.add('hidden');document.getElementById('resetPanel').classList.remove('hidden');document.getElementById('resetToken').value=resetToken;}
  if(verifyToken){screen.classList.remove('hidden');api('/api/auth/verify-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:verifyToken})}).then(async()=>{history.replaceState({},'',location.pathname);screen.classList.add('hidden');toast('Email verified. Your account is ready.');await load();await refreshIdentity()}).catch(error=>{history.replaceState({},'',location.pathname);setMode('login');document.getElementById('authError').textContent=error.message;document.getElementById('authError').classList.remove('hidden')});}
  document.getElementById('signOut').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});screen.classList.remove('hidden');setMode('login')}catch(error){toast(error.message)}});
});
