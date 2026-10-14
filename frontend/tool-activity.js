/* Per-reply observed tool calls. No inferred calls, raw arguments or raw results. */
(() => {
  const tools = Object.freeze({search_knowledge:'检索心理支持知识',memory_search:'查找相关记忆',get_goal_context:'读取我的目标',get_dashboard:'读取心理概要',get_support_resources:'查找支持资源',crisis_support:'读取紧急支持资源',create_goal:'创建目标与小步骤',sync_goal_progress:'同步目标进展'});
  const states = new Set(['running','ok','error','no_change','unconfirmed']);
  const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  const icon = (kind) => `<svg viewBox="0 0 20 20" aria-hidden="true">${kind==='check'?'<path d="m5 10 3.2 3.2L15 6.5"/>':kind==='error'?'<path d="m6 6 8 8M14 6l-8 8"/>':kind==='waiting'?'<circle cx="10" cy="10" r="6.5"/><path d="M10 6v4l2.5 1.5"/>':'<circle cx="8.5" cy="8.5" r="4.5"/><path d="m12 12 4 4"/>'}</svg>`;
  const chevron = '<svg class="activity-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg>';
  const duration = ms => Number.isFinite(ms) && ms>=0 ? (ms<1000?`${Math.round(ms)} ms`:`${(ms/1000).toFixed(1)} 秒`) : '';
  const defaults = {ok:'调用已完成',error:'暂时无法完成调用',no_change:'本次未修改记录',unconfirmed:'未收到最终结果，不能确认调用是否完成'};

  function model() {
    const calls = new Map();
    function update(payload) {
      if(!payload || !Object.hasOwn(tools,payload.name) || typeof payload.callId!=='string' || !/^[\w-]{1,80}$/.test(payload.callId) || !states.has(payload.status)) return false;
      const previous = calls.get(payload.callId);
      if(previous && (previous.name!==payload.name || (previous.status!=='running' && payload.status==='running')))return false;
      calls.set(payload.callId,{...previous,id:payload.callId,name:payload.name,status:payload.status,
        summary: typeof payload.summary==='string'?payload.summary.slice(0,240):previous?.summary||'',
        durationMs:Number.isFinite(payload.durationMs)&&payload.durationMs>=0?payload.durationMs:previous?.durationMs,
        transport:['local','mcp','mcp→local'].includes(payload.transport)?payload.transport:previous?.transport});
      return true;
    }
    function reconcile(metrics) {
      if(!Array.isArray(metrics?.capabilityCalls))return;
      const counts = new Map();
      for(const item of metrics.capabilityCalls) {
        if(!item || !Object.hasOwn(tools,item.name) || !['ok','error','no_change'].includes(item.status))continue;
        const n=counts.get(item.name)||0;counts.set(item.name,n+1);
        const previous=[...calls.values()].filter(c=>c.name===item.name)[n];
        update({...item,callId:previous?.id||`metrics-${item.name}-${n}`,summary:previous?.status==='unconfirmed'?defaults[item.status]:previous?.summary||defaults[item.status]});
      }
    }
    function settle() {for(const c of calls.values())if(c.status==='running')update({...c,callId:c.id,status:'unconfirmed',summary:defaults.unconfirmed});}
    return Object.freeze({update,reconcile,settle,rows:()=>[...calls.values()]});
  }

  function attach(message) {
    const data=model(), group=document.createElement('details');
    group.className='message-activity';group.open=true;group.hidden=true;
    const heading=document.createElement('summary');heading.className='activity-heading';
    const list=document.createElement('ol');list.className='activity-list';
    group.append(heading,list);message.prepend(group);
    const rows=new Map();let ended=false,ending='done';
    function draw() {
      const calls=data.rows();group.hidden=!calls.length;message.classList.toggle('has-tool-activity',!!calls.length);
      const active=calls.find(c=>c.status==='running');
      const errors=calls.filter(c=>c.status==='error').length,unknown=calls.filter(c=>c.status==='unconfirmed').length;
      const headingText=active&&!ended?'正在使用工具':ending==='stopped'?'已停止接收':unknown?'部分调用结果未确认':`已调用 ${calls.length} 个工具`;
      heading.innerHTML=`<span class="activity-heading-mark ${active&&!ended?'is-running':''}">${icon(active&&!ended?'waiting':'check')}</span><span class="activity-heading-label">${esc(headingText)}</span>${errors?'<span class="activity-warning">有调用未完成</span>':''}${chevron}`;
      for(const c of calls) {
        let row=rows.get(c.id);
        if(!row){row=document.createElement('li');row.innerHTML='<details class="activity-call"><summary></summary><div class="activity-call-detail"></div></details>';rows.set(c.id,row);list.append(row);}
        row.dataset.status=c.status;
        const label={running:'调用中',ok:'已完成',error:'未完成',no_change:'未更新',unconfirmed:'未确认'}[c.status];
        row.querySelector('summary').innerHTML=`<span class="activity-call-icon ${c.status==='running'?'is-running':''}">${icon(c.status==='running'||c.status==='unconfirmed'?'waiting':c.status==='error'?'error':'check')}</span><span class="activity-call-label">${esc(tools[c.name])}</span><span class="activity-call-state">${label}</span><span class="activity-call-duration">${duration(c.durationMs)}</span>${chevron}`;
        row.querySelector('.activity-call-detail').innerHTML=`<p>${esc(c.summary|| (c.status==='running'?'正在获取本轮需要的信息。':defaults[c.status]))}</p><dl><div><dt>工具</dt><dd><code>${esc(c.name)}</code></dd></div>${c.transport?`<div><dt>执行方式</dt><dd>${esc({local:'本地调用',mcp:'MCP 调用','mcp→local':'MCP 不可用，已回退本地'}[c.transport])}</dd></div>`:''}${Number.isFinite(c.durationMs)?`<div><dt>耗时</dt><dd>${duration(c.durationMs)}</dd></div>`:''}</dl>`;
      }
    }
    return Object.freeze({
      update:payload=>{if(data.update(payload))draw();},
      reconcile:metrics=>{data.reconcile(metrics);draw();},
      finish:status=>{ended=true;ending=status||'done';data.settle();draw();},
      hasCalls:()=>data.rows().length>0,
    });
  }
  window.PsychToolActivity=Object.freeze({attach,model});
})();
