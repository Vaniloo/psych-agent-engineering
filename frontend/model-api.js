/* Per-account model settings. Secrets stay in the form until sent to our backend. */
(() => {
  const $=s=>document.querySelector(s);
  let ctx,revision=0,known=null;
  function bind(context){
    ctx=context;
    const dialog=document.createElement('dialog');dialog.id='modelApiDialog';dialog.className='model-api-dialog';dialog.setAttribute('aria-labelledby','modelApiTitle');
    dialog.innerHTML=`<form id="modelApiForm"><header><div><h2 id="modelApiTitle">模型 API</h2></div><button id="closeModelApi" type="button" class="icon-button" aria-label="关闭模型API设置">×</button></header>
      <div class="model-api-body"><label>使用方式<select id="modelApiMode"><option value="default">应用默认模型</option><option value="custom">自定义 API</option></select></label>
      <div id="modelApiFields" class="model-api-fields" hidden>
        <label>Base URL<input id="modelApiUrl" type="url" required maxlength="500" placeholder="https://api.deepseek.com" autocomplete="off" spellcheck="false"></label>
        <label>API Key<input id="modelApiKey" type="password" maxlength="4096" placeholder="填写你的 API Key" autocomplete="new-password" spellcheck="false"></label>
        <label>模型名<input id="modelApiModel" required maxlength="160" placeholder="例如 deepseek-flash" autocomplete="off" spellcheck="false"></label>
        <p class="model-api-help">支持公开 HTTPS 的 Chat Completions 接口，需支持流式回复和 JSON 模式。密钥按账号加密保存，不回显。</p>
      </div>
      <p class="model-api-scope">用于聊天回复及对话中的理解与检索判断。目标规划、报告和本地模型沿用应用配置。</p>
      <p id="modelApiNotice" class="model-api-notice" role="status"></p>
      </div><footer><button id="testModelApi" type="button" hidden>测试连接</button><button id="saveModelApi" type="submit" class="primary-action">保存</button></footer>
    </form>`;
    document.body.appendChild(dialog);
    $('#modelApiSettingsBtn').addEventListener('click',open);
    $('#closeModelApi').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',clearSecret);
    $('#modelApiMode').addEventListener('change',syncMode);
    $('#modelApiForm').addEventListener('submit',e=>{e.preventDefault();save();});
    $('#testModelApi').addEventListener('click',test);
    $('#modelApiUrl').addEventListener('input',()=>{$('#modelApiKey').placeholder=known?.keyConfigured&&$('#modelApiUrl').value.trim()===known.baseUrl?'已保存；留空保持原密钥':'更换地址时需重新填写 API Key';});
  }
  function clearSecret(){revision++;$('#modelApiKey').value='';}
  function reset(){if(!$('#modelApiDialog'))return;$('#modelApiDialog').close();clearSecret();known=null;$('#modelApiForm').reset();$('#modelApiNotice').textContent='';}
  function busy(value){$('#saveModelApi').disabled=value;$('#testModelApi').disabled=value;$('#modelApiMode').disabled=value;}
  function syncMode(){const custom=$('#modelApiMode').value==='custom';$('#modelApiFields').hidden=!custom;$('#testModelApi').hidden=!custom;$('#modelApiFields').querySelectorAll('input').forEach(el=>el.disabled=!custom);$('#modelApiNotice').textContent='';}
  async function open(){
    if(!ctx.state.token)return;
    ctx.closeMenu();clearSecret();const turn=revision,token=ctx.state.token;
    $('#modelApiDialog').showModal();busy(true);$('#modelApiNotice').textContent='正在读取配置…';
    try{const data=await ctx.api('/model-api',{cache:'no-store'});if(turn!==revision||token!==ctx.state.token)return;known=data;
      $('#modelApiMode').value=data.configured?'custom':'default';$('#modelApiUrl').value=data.baseUrl;$('#modelApiModel').value=data.model;
      $('#modelApiKey').placeholder=data.keyConfigured?'已保存；留空保持原密钥':'填写你的 API Key';syncMode();
      if(!data.available)$('#modelApiNotice').textContent='模型接口设置暂未开放，请联系管理员。';
    }catch(e){if(turn===revision&&e.name!=='AbortError')$('#modelApiNotice').textContent=e.message;}
    finally{if(turn===revision)busy(false);}
  }
  function input(){return {baseUrl:$('#modelApiUrl').value.trim(),model:$('#modelApiModel').value.trim(),apiKey:$('#modelApiKey').value.trim()};}
  function validate(){if(!$('#modelApiForm').reportValidity())return false;const data=input();if(!data.apiKey&&(!known?.keyConfigured||data.baseUrl.replace(/\/+$/,'')!==known.baseUrl)){$('#modelApiNotice').textContent='新建配置或更换地址时，请填写 API Key。';return false;}return true;}
  async function test(){
    if(!validate())return;const turn=revision,token=ctx.state.token;busy(true);$('#modelApiNotice').textContent='正在测试连接…';
    try{const result=await ctx.api('/model-api/test',{method:'POST',body:JSON.stringify(input())});if(turn!==revision||token!==ctx.state.token)return;$('#modelApiNotice').textContent=result.message;}
    catch(e){if(turn===revision&&e.name!=='AbortError')$('#modelApiNotice').textContent=e.message;}
    finally{if(turn===revision)busy(false);}
  }
  async function save(){
    const custom=$('#modelApiMode').value==='custom';if(custom&&!validate())return;
    const turn=revision,token=ctx.state.token;busy(true);$('#modelApiNotice').textContent='正在保存…';
    try{await ctx.api('/model-api',custom?{method:'PUT',body:JSON.stringify(input())}:{method:'DELETE'});if(turn!==revision||token!==ctx.state.token)return;
      $('#modelApiDialog').close();ctx.toast(custom?'模型 API 已保存，下一轮对话生效':'已恢复应用默认模型');
    }catch(e){if(turn===revision&&e.name!=='AbortError')$('#modelApiNotice').textContent=e.message;}
    finally{if(turn===revision)busy(false);}
  }
  window.AttuneModelApi=Object.freeze({bind,reset});
})();
