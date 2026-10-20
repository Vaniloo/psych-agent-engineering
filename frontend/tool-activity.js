/* Per-reply observed tool calls. No inferred calls, raw arguments or raw results. */
(() => {
  const tools = Object.freeze({search_web:'搜索网络',search_knowledge:'检索心理支持知识',memory_search:'查找相关记忆',get_goal_context:'读取我的目标',get_dashboard:'读取心理概要',get_support_resources:'查找支持资源',crisis_support:'读取紧急支持资源',create_goal:'创建目标与小步骤',sync_goal_progress:'同步目标进展',manage_goal:'调整目标与步骤',advance_goal:'判断目标完成与规划下一步'});
  const states = new Set(['running','ok','error','no_change','unconfirmed']);
  const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  const icon = (kind) => `<svg viewBox="0 0 20 20" aria-hidden="true">${kind==='check'?'<path d="m5 10 3.2 3.2L15 6.5"/>':kind==='error'?'<path d="m6 6 8 8M14 6l-8 8"/>':kind==='waiting'?'<circle cx="10" cy="10" r="6.5"/><path d="M10 6v4l2.5 1.5"/>':'<circle cx="8.5" cy="8.5" r="4.5"/><path d="m12 12 4 4"/>'}</svg>`;
  const chevron = '<svg class="activity-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg>';
  const duration = ms => Number.isFinite(ms) && ms>=0 ? (ms<1000?`${Math.round(ms)} ms`:`${(ms/1000).toFixed(1)} 秒`) : '';
  const defaults = {ok:'调用已完成',error:'暂时无法完成调用',no_change:'本次未修改记录',unconfirmed:'未收到最终结果，不能确认调用是否完成'};

  function sources(value) {
    return (Array.isArray(value)?value:[]).slice(0,5).flatMap(row=>{try{const url=new URL(row.url);return ['https:','http:'].includes(url.protocol)?[{title:String(row.title||url.hostname).slice(0,200),url:url.href}]:[];}catch{return [];}});
  }

  function model() {
    const calls = new Map(),knowledgeSteps=new Map();let loopState=null;
    function observeKnowledge(payload) {
      if(!payload || !['plan','retrieve','assess','complete'].includes(payload.stage)
        || !['running','ok','unconfirmed'].includes(payload.status)
        || !Number.isInteger(payload.round) || payload.round<1 || payload.round>2)return false;
      const key=`${payload.round}:${payload.stage}`,previous=knowledgeSteps.get(key);
      if(previous && previous.status!=='running' && payload.status==='running')return false;
      knowledgeSteps.set(key,{stage:payload.stage,status:payload.status,round:payload.round,
        evidenceStatus:['sufficient','partial','insufficient','needs_user_input','unavailable'].includes(payload.evidenceStatus)?payload.evidenceStatus:null,
        retained:Number.isInteger(payload.retained)&&payload.retained>=0&&payload.retained<=6?payload.retained:null,
        retry:payload.retry===true,sources:sources(payload.sources)});
      return true;
    }
    function observeLoop(payload) {
      if(!payload || typeof payload!=='object')return false;
      const budget=payload.budget??loopState?.budget, count=payload.readCalls??payload.calls;
      if(!Number.isInteger(budget)||budget<1||budget>16||!Number.isInteger(count)||count<0||count>budget)return false;
      loopState={budget:Math.max(budget,loopState?.budget||0),calls:Math.max(count,loopState?.calls||0)};
      return true;
    }
    function update(payload) {
      if(!payload || !(Object.hasOwn(tools,payload.name) || (payload.registeredRead===true && /^[A-Za-z][\w-]{0,19}__[A-Za-z][\w-]{0,39}$/.test(payload.name))) || typeof payload.callId!=='string' || !/^[\w-]{1,80}$/.test(payload.callId) || !states.has(payload.status)) return false;
      const previous = calls.get(payload.callId);
      if(previous && (previous.name!==payload.name || (previous.status!=='running' && payload.status==='running')))return false;
      calls.set(payload.callId,{...previous,id:payload.callId,name:payload.name,status:payload.status,
        label:tools[payload.name] || (typeof payload.label==='string'?payload.label.slice(0,80):payload.name),registeredRead:payload.registeredRead===true,
        ...(payload.name==='search_web'&&Array.isArray(payload.sources)?{sources:sources(payload.sources)}:{}),
        ...(payload.name==='search_web'&&payload.searchContext?{searchContext:searchContext(payload.searchContext)}:{}),
        summary: typeof payload.summary==='string'?payload.summary.slice(0,240):previous?.summary||'',
        durationMs:Number.isFinite(payload.durationMs)&&payload.durationMs>=0?payload.durationMs:previous?.durationMs,
        transport:['local','mcp','mcp→local'].includes(payload.transport)?payload.transport:previous?.transport});
      return true;
    }
    function reconcile(metrics) {
      const rag=metrics?.pipeline?.agenticRag;
      if(rag?.enabled && Number.isInteger(rag.rounds) && rag.rounds>0)observeKnowledge({
        stage:'complete',status:rag.status==='unavailable'?'unconfirmed':'ok',round:rag.rounds,
        evidenceStatus:rag.responseCoverage?.status||rag.status,retained:rag.responseCoverage?.deliveredChunks??rag.retained});
      if(!Array.isArray(metrics?.capabilityCalls))return;
      const counts = new Map();
      for(const item of metrics.capabilityCalls) {
        if(!item || !(Object.hasOwn(tools,item.name) || [...calls.values()].some(c=>c.name===item.name && c.registeredRead)) || !['ok','error','no_change'].includes(item.status))continue;
        const n=counts.get(item.name)||0;counts.set(item.name,n+1);
        const previous=[...calls.values()].filter(c=>c.name===item.name)[n];
        update({...item,registeredRead:previous?.registeredRead,label:previous?.label,callId:previous?.id||`metrics-${item.name}-${n}`,summary:previous?.status==='unconfirmed'?defaults[item.status]:previous?.summary||defaults[item.status]});
      }
    }
    function settle() {
      for(const c of calls.values())if(c.status==='running')update({...c,callId:c.id,status:'unconfirmed',summary:defaults.unconfirmed});
      for(const step of knowledgeSteps.values())if(step.status==='running')observeKnowledge({...step,status:'unconfirmed'});
    }
    return Object.freeze({update,reconcile,settle,observeLoop,observeKnowledge,knowledge:()=>[...knowledgeSteps.values()],progress:()=>loopState&&({...loopState}),rows:()=>[...calls.values()]});
  }

  function attach(message) {
    const data=model(), group=document.createElement('details');
    group.className='message-activity';group.open=true;group.hidden=true;
    const heading=document.createElement('summary');heading.className='activity-heading';
    const list=document.createElement('ol');list.className='activity-list';
    const knowledge=document.createElement('div');knowledge.className='activity-knowledge';knowledge.hidden=true;
    group.append(heading,list,knowledge);message.prepend(group);
    const rows=new Map();let ended=false,ending='done';
    function draw() {
      const calls=data.rows(),steps=data.knowledge();group.hidden=!calls.length&&!steps.length;message.classList.toggle('has-tool-activity',!!calls.length||!!steps.length);
      const active=calls.find(c=>c.status==='running');
      const errors=calls.filter(c=>c.status==='error').length,unknown=calls.filter(c=>c.status==='unconfirmed').length;
      const checking=steps.some(s=>s.status==='running');
      const busy=(active||checking)&&!ended;
      const headingText=checking&&!ended?'正在核对资料':active&&!ended?'正在使用工具':ending==='stopped'?'已停止接收':unknown?'部分调用结果未确认':calls.length?`已调用 ${calls.length} 个工具`:'知识检索';
      const progress=data.progress();
      heading.innerHTML=`<span class="activity-heading-mark ${busy?'is-running':''}">${icon(busy?'waiting':'check')}</span><span class="activity-heading-label">${esc(headingText)}</span>${progress?`<span class="activity-budget" title="本轮信息读取次数与上限；目标保存另行记录">读取 ${progress.calls} / ${progress.budget}</span>`:''}${errors?'<span class="activity-warning">有调用未完成</span>':''}${chevron}`;
      knowledge.hidden=!steps.length;
      const coverage={sufficient:'资料足以支持本轮回答',partial:'找到部分依据，仍有信息缺口',insufficient:'资料不足以支持本轮回答',needs_user_input:'需要先补充你的情况',unavailable:'资料核对暂未完成'};
      const knowledgeSources=[...new Map((steps.filter(s=>s.stage==='assess').at(-1)?.sources||[]).map(s=>[s.url,s])).values()];
      knowledge.innerHTML=steps.length?`<span class="activity-knowledge-title">知识检索过程</span><ol>${steps.map(s=>`<li data-status="${s.status}"><span class="activity-knowledge-icon ${s.status==='running'?'is-running':''}">${icon(s.status==='running'||s.status==='unconfirmed'?'waiting':'check')}</span><span>${esc(s.stage==='complete'?coverage[s.evidenceStatus]||'本轮检索结束':s.stage==='plan'?'理解检索问题':s.stage==='retrieve'?(s.round===2?'补充检索资料':'检索知识资料'):'核对资料依据')}${s.stage==='assess'&&s.retained!==null?` · 保留 ${s.retained} 条`:''}${s.stage==='assess'&&s.retry?' · 将补充检索':''}${s.status==='unconfirmed'?' · 未确认':''}</span></li>`).join('')}</ol>`:'';
      if(knowledgeSources.length)knowledge.innerHTML+=`<p class="activity-knowledge-sources">资料来源：${knowledgeSources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join(' · ')}<br>原文可免费访问；整理资料包含待专业复核的自动译文，引用不代表来源机构认可本应用。</p>`;
      for(const c of calls) {
        let row=rows.get(c.id);
        if(!row){row=document.createElement('li');row.innerHTML='<details class="activity-call"><summary></summary><div class="activity-call-detail"></div></details>';rows.set(c.id,row);list.append(row);}
        row.dataset.status=c.status;
        const label={running:'调用中',ok:'已完成',error:'未完成',no_change:'未更新',unconfirmed:'未确认'}[c.status];
        row.querySelector('summary').innerHTML=`<span class="activity-call-icon ${c.status==='running'?'is-running':''}">${icon(c.status==='running'||c.status==='unconfirmed'?'waiting':c.status==='error'?'error':'check')}</span><span class="activity-call-label">${esc(c.label)}</span><span class="activity-call-state">${label}</span><span class="activity-call-duration">${duration(c.durationMs)}</span>${chevron}`;
        row.querySelector('.activity-call-detail').innerHTML=`<p>${esc(c.summary|| (c.status==='running'?'正在获取本轮需要的信息。':defaults[c.status]))}</p>${c.searchContext?`<p class="activity-source-note">候选 ${c.searchContext.candidatePages} 页 → 读取 ${c.searchContext.pagesRead} 页 → 预选 ${c.searchContext.chunksSelected} 个片段<br>${c.searchContext.selection==='laya_ranking_with_lexical_guard'?'本地 Laya 排序，保留检索兜底 · 实验版本':'使用检索兜底，未完成正文模型筛选'}</p>`:''}${c.sources?.length?`<ul class="activity-web-sources">${c.sources.map(s=>`<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></li>`).join('')}</ul>`:''}<dl><div><dt>工具</dt><dd><code>${esc(c.name)}</code></dd></div>${c.transport?`<div><dt>执行方式</dt><dd>${esc({local:'本地调用',mcp:'MCP 调用','mcp→local':'MCP 不可用，已回退本地'}[c.transport])}</dd></div>`:''}${Number.isFinite(c.durationMs)?`<div><dt>耗时</dt><dd>${duration(c.durationMs)}</dd></div>`:''}</dl>`;
      }
    }
    return Object.freeze({
      update:payload=>{if(data.update(payload))draw();},
      reconcile:metrics=>{data.reconcile(metrics);data.observeLoop(metrics?.pipeline?.loop);draw();},
      observeLoop:payload=>{if(data.observeLoop(payload))draw();},
      observeKnowledge:payload=>{if(data.observeKnowledge(payload))draw();},
      finish:status=>{ended=true;ending=status||'done';data.settle();draw();},
      hasCalls:()=>data.rows().length>0,
    });
  }
  function searchContext(value) {
    if(!value||typeof value!=='object')return null;
    const keys=['candidatePages','pagesRead','chunksSelected'];
    if(keys.some(k=>!Number.isInteger(value[k])||value[k]<0||value[k]>32))return null;
    return {candidatePages:value.candidatePages,pagesRead:value.pagesRead,chunksSelected:value.chunksSelected,
      selection:value.selection==='laya_ranking_with_lexical_guard'?value.selection:'lexical_fallback'};
  }
  window.PsychToolActivity=Object.freeze({attach,model});
})();
