/* Voice settings: compact picker and an on-demand persona/example editor. */
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $=s=>document.querySelector(s);
  const fields={studioName:'name',studioDescription:'description',studioPersonality:'personality',studioExamples:'examples'};
  let ctx,selected=null,draft=null,upload=null,revision=0,cards=[],busy=false;
  const blank=()=>({name:'',description:'',personality:'',examples:'',tone:'自然',responseStyle:'遵循角色的交流习惯'});
  function bind(context){ctx=context;}
  function shell(){
    if($('#roleStudio'))return;
    const host=document.createElement('div');host.id='roleStudio';host.hidden=true;
    host.innerHTML=`<div class="studio-toolbar"><span>可用风格</span><div class="studio-actions"><button type="button" id="studioImport" class="studio-upload">导入角色卡</button><input id="studioFile" type="file" accept=".png,.json,image/png,application/json" hidden><button type="button" id="studioNew">＋ 新建风格</button></div></div>
      <div id="studioCards" class="studio-cards"></div>
      <dialog id="studioEditorDialog" class="studio-editor" aria-labelledby="studioEditorTitle"><form id="studioForm">
        <header class="studio-editor-heading"><div><h3 id="studioEditorTitle">新建风格</h3><p id="studioSaveHint">写下人设，用几句对话呈现语气。</p></div><button type="button" id="studioClose" class="icon-button" aria-label="关闭风格编辑">×</button></header>
        <label>名称<input id="studioName" required maxlength="80" placeholder="例如：轻快的同伴"></label>
        <fieldset class="studio-persona"><legend>人设</legend><label>角色设定<textarea id="studioDescription" rows="4" maxlength="8000" placeholder="这是谁，有什么性格，通常怎样与你交流。"></textarea></label><label>说话习惯<textarea id="studioPersonality" rows="3" maxlength="4000" placeholder="例如：习惯短句，轻快直接，会用一点幽默，但不急着给建议。"></textarea></label></fieldset>
        <label>对话示例<textarea id="studioExamples" rows="5" maxlength="12000" placeholder="用户：今天有点累。&#10;角色：那今天先歇一会儿？&#10;&#10;写两三组对话，表达会更贴近你想要的样子。"></textarea></label>
        <footer class="studio-save-bar"><details id="studioExport" class="studio-export" hidden><summary>导出</summary><button id="studioExportJson" type="button">JSON</button><button id="studioExportPng" type="button" hidden>PNG</button></details><button id="studioCopy" type="button" hidden>复制为新风格</button><button id="studioSave" class="primary-action" type="submit">保存</button></footer>
      </form></dialog>
      <dialog id="studioImportDialog" class="studio-import-dialog" aria-labelledby="studioImportTitle"><div class="studio-import-title"><h3 id="studioImportTitle">导入角色卡</h3><button type="button" id="studioCancelImport" class="icon-button" aria-label="关闭导入预览">×</button></div><div id="studioImportBody"></div><p>只使用人设和语气示例，保存后可自行启用。</p><div class="studio-actions"><button type="button" id="studioConfirmImport" class="primary-action">导入</button></div></dialog>`;
    $('#roleCardView').appendChild(host);
    $('#studioNew').addEventListener('click',openNew);
    $('#studioImport').addEventListener('click',()=>$('#studioFile').click());
    $('#studioClose').addEventListener('click',()=>$('#studioEditorDialog').close());
    $('#studioFile').addEventListener('change',e=>previewFile(e.target.files[0]));
    $('#studioForm').addEventListener('submit',save);
    $('#studioCards').addEventListener('click',e=>{const b=e.target.closest('[data-studio-open],[data-studio-activate]');if(!b)return;if(b.dataset.studioActivate)activate(Number(b.dataset.studioActivate));else open(Number(b.dataset.studioOpen));});
    $('#studioCopy').addEventListener('click',copy);
    $('#studioExportJson').addEventListener('click',()=>download('json'));
    $('#studioExportPng').addEventListener('click',()=>download('png'));
    $('#studioCancelImport').addEventListener('click',()=>{$('#studioImportDialog').close();upload=null;});
    $('#studioImportDialog').addEventListener('cancel',()=>{upload=null;});
    $('#studioConfirmImport').addEventListener('click',confirmImport);
  }
  function render(available,enabled){shell();cards=available;$('#roleStudio').hidden=!enabled;$('#legacyRoleCardLayout').hidden=enabled;if(!enabled){reset(false);return;}renderCards();}
  function renderCards(){
    $('#studioCards').innerHTML=cards.map(c=>`<article class="studio-card ${c.active?'is-active':''}"><button class="studio-card-select" type="button" data-studio-activate="${Number(c.id)}" aria-pressed="${c.active===true}" ${c.active?'disabled':''}><span class="studio-card-avatar">${esc(c.name.slice(0,1))}</span><span class="studio-card-info"><strong>${esc(c.name)}</strong><small>${esc(c.description.slice(0,72)||c.tone||'自然交流')}</small></span><span class="studio-card-check" aria-hidden="true">${c.active?'✓':''}</span></button><button class="studio-card-edit" type="button" data-studio-open="${Number(c.id)}" aria-label="${esc(c.name)} · ${c.experimental?'编辑':'查看'}">${c.experimental?'编辑':'查看'}</button></article>`).join('');
  }
  function openNew(){if(busy)return;revision++;selected=null;draft=blank();fill(false);$('#studioEditorDialog').showModal();}
  async function open(id){if(busy)return;const turn=++revision,token=ctx.state.token;try{const result=await ctx.api(`/role-cards/studio/${id}`);if(turn!==revision||token!==ctx.state.token)return;selected=id;draft=result.config;fill(!result.editable,result.avatarDataUrl);$('#studioEditorDialog').showModal();}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}}
  function fill(readonly,avatar){
    for(const [id,key] of Object.entries(fields))$('#'+id).value=draft[key]||'';
    $('#studioEditorTitle').textContent=selected?draft.name:'新建风格';
    $('#studioCopy').hidden=!selected;$('#studioExport').hidden=!selected;$('#studioExport').open=false;$('#studioExportPng').hidden=!avatar;
    $('#studioForm').dataset.readonly=String(readonly);$('#studioSave').hidden=readonly;
    $('#studioSaveHint').textContent=readonly?'喜欢这个方向？复制后可以调整人设和示例。':'写下人设，用几句对话呈现语气。';
    $('#studioForm').querySelectorAll('input,textarea').forEach(el=>el.disabled=readonly);
  }
  function read(){const c={...draft};for(const [id,key] of Object.entries(fields))c[key]=$('#'+id).value.trim();return c;}
  async function save(e){e.preventDefault();if(busy)return;busy=true;const token=ctx.state.token;$('#studioSave').disabled=true;try{const result=await ctx.api(selected?`/role-cards/studio/${selected}`:'/role-cards/studio',{method:selected?'PUT':'POST',body:JSON.stringify(read())});if(token!==ctx.state.token)return;selected=result.id;draft=result.config;$('#studioEditorDialog').close();await ctx.reload();ctx.toast('风格已保存');}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}finally{busy=false;$('#studioSave').disabled=false;}}
  async function copy(){if(!selected||busy)return;busy=true;const token=ctx.state.token;try{const result=await ctx.api(`/role-cards/studio/${selected}/copy`,{method:'POST'});if(token!==ctx.state.token)return;selected=result.id;draft=result.config;fill(false,result.avatarDataUrl);await ctx.reload();ctx.toast('已创建副本');}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}finally{busy=false;}}
  async function activate(id){if(busy)return;if(ctx.state.activeStream)return ctx.toast('请等当前回复结束后再切换风格');busy=true;const token=ctx.state.token;try{await ctx.api(`/role-cards/${id}/activate`,{method:'POST'});if(token!==ctx.state.token)return;await ctx.reload();ctx.toast('已切换，下一条消息生效');}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}finally{busy=false;}}
  async function previewFile(file){if(!file||busy)return;const token=ctx.state.token;try{if(file.size>3*1024*1024)throw new Error('角色卡最多3MB');const format=file.name.toLowerCase().endsWith('.png')?'png':file.name.toLowerCase().endsWith('.json')?'json':null;if(!format)throw new Error('请选择PNG或JSON角色卡');const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});const preview=await ctx.api('/role-cards/studio/preview',{method:'POST',body:JSON.stringify({format,data})});if(token!==ctx.state.token)return;upload={format,data};const c=preview.config;$('#studioImportBody').innerHTML=`<h4>${esc(c.name)}</h4><p>${esc([c.description,c.personality].filter(Boolean).join('\n').slice(0,800))}</p><label>对话示例<textarea rows="5" readonly>${esc(c.examples||'这张卡暂时没有对话示例。')}</textarea></label>`;$('#studioImportDialog').showModal();}catch(e){if(e.name!=='AbortError')ctx.toast(e.message||'无法读取文件');}finally{$('#studioFile').value='';}}
  async function confirmImport(){if(!upload||busy)return;busy=true;$('#studioConfirmImport').disabled=true;const token=ctx.state.token;try{const result=await ctx.api('/role-cards/studio/import',{method:'POST',body:JSON.stringify(upload)});if(token!==ctx.state.token)return;upload=null;$('#studioImportDialog').close();selected=result.id;draft=result.config;fill(false,result.avatarDataUrl);await ctx.reload();$('#studioEditorDialog').showModal();ctx.toast('已导入，可调整后启用');}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}finally{busy=false;$('#studioConfirmImport').disabled=false;}}
  async function download(format){if(!selected)return;const token=ctx.state.token;try{let blob;if(format==='json'){const data=await ctx.api(`/role-cards/studio/${selected}/export`);blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});}else{const r=await fetch(`${ctx.state.apiBaseUrl}/role-cards/studio/${selected}/export?format=png`,{headers:{Authorization:`Bearer ${token}`}});if(!r.ok)throw new Error('PNG导出失败');blob=await r.blob();}if(token!==ctx.state.token)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${(draft.name||'角色卡').replace(/[\\/:*?"<>|]/g,'_')}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}}
  function reset(clear=true){revision++;selected=null;draft=null;upload=null;cards=[];if($('#roleStudio')){for(const id of ['studioEditorDialog','studioImportDialog'])$('#'+id).close();if(clear){$('#roleStudio').hidden=true;$('#studioImportBody').replaceChildren();$('#studioCards').replaceChildren();$('#studioForm').reset();}}}
  window.PsychRoleStudio=Object.freeze({bind,render,reset});
})();
