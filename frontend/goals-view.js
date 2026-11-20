/* Goal progress is user-reported. Positive feedback follows a successful persisted write. */
(() => {
  let ctx, goals=[], entries=[], filter='active', revision=0, weeklyRevision=0, busy=false, editing=null, recording=null, weekly=null, weeklyDate=null, feedbackEntryId=null;
  const planningGoals=new Set();
  const $=s=>document.querySelector(s);
  const esc = window.escapeHtml;
  const svg=n=>`<svg class="icon" aria-hidden="true"><use href="#icon-${n}"/></svg>`;
  const outcomes={partial:'做了一点',done:'完成了',blocked:'遇到阻碍',rest:'留出休息',revised:'步骤已调整',goal_completed:'整个目标已完成',paused:'目标已暂缓'};
  const dateKey=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const localDate=v=>v?.slice(0,10);
  function bind(context) {
    ctx=context;
    $('#goalAutoSync').addEventListener('change',async()=>{
      const input=$('#goalAutoSync'), enabled=input.checked;input.disabled=true;
      try {const result=await ctx.api('/goals/preferences',{method:'PUT',body:JSON.stringify({autoSync:enabled})});input.checked=result.autoSync;$('#goalNotice').textContent=enabled?'明确的行动进展会从对话同步；匹配不清时保留原样。':'已关闭从对话自动同步，你仍可以在这里手动记录。';}
      catch(error){if(error.name!=='AbortError'){input.checked=!enabled;$('#goalNotice').textContent=error.message;}}
      finally {input.disabled=false;}
    });
    $('#goalRecentList').addEventListener('click',event=>{const b=event.target.closest('[data-checkin-undo]');if(b)undo(b);});
    $('#refreshGoalsBtn').addEventListener('click',()=>load());
    $('#newGoalBtn').addEventListener('click',()=>openEditor());
    document.querySelectorAll('[data-goal-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.goalFilter;render();}));
    $('#goalForm').addEventListener('submit',saveEditor);
    $('#checkInForm').addEventListener('submit',event=>{event.preventDefault();const outcome=$('#checkInOutcome').value,progress=$('#checkInProgress').value.trim();if(outcome==='partial'&&!progress){$('#checkInError').textContent='写下已经做过的一点点，会更容易找到下一步。';return;}record(recording.goal,recording.action,outcome,(outcome==='partial'?`已推进：${progress}\n`:'')+$('#checkInNote').value.trim());});
    $('#checkInOutcome').addEventListener('change',syncRecordForm);
    document.querySelectorAll('[data-goal-page]').forEach(b=>b.addEventListener('click',()=>{const isWeekly=b.dataset.goalPage==='weekly';$('#goalStepsPage').hidden=isWeekly;$('#goalWeeklyPage').hidden=!isWeekly;document.querySelectorAll('[data-goal-page]').forEach(t=>t.setAttribute('aria-pressed',String(t===b)));if(isWeekly)loadWeekly();}));
    $('#goalWeeklyOffset').addEventListener('change',()=>loadWeekly());
    $('#goalWeeklyDays').addEventListener('click',e=>{const b=e.target.closest('[data-week-date]');if(b){weeklyDate=weeklyDate===b.dataset.weekDate?null:b.dataset.weekDate;renderWeekly();}});
    document.querySelectorAll('[data-close-goal-dialog]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
    document.querySelectorAll('[data-goal-preset]').forEach(button=>button.addEventListener('click',()=>{
      const presets={rest:['给忙碌留一点休息','离开桌前，走一走'],start:['让一件事情开始','打开文档，写下一个标题'],needs:['表达自己的需要','先写下我希望对方理解的一句话']};
      [$('#goalTitle').value,$('#goalAction').value]=presets[button.dataset.goalPreset];$('#goalTitle').focus();
    }));
    $('#goalDeleteBtn').addEventListener('click',()=>{$('#goalDeleteConfirm').hidden=false;});
    $('#confirmDeleteGoalBtn').addEventListener('click',deleteGoal);
    $('#goalList').addEventListener('click',event=>{
      const button=event.target.closest('[data-goal-operation]'); if(!button||busy)return;
      const goal=goals.find(g=>String(g.id)===button.dataset.goalId);if(!goal||planningGoals.has(goal.id))return;
      const action=goal.actions.find(a=>a.id===button.dataset.actionId);
      if(button.dataset.goalOperation==='edit')openEditor(goal);
      if(button.dataset.goalOperation==='chat')talk(goal);
      if(button.dataset.goalOperation==='add')openEditor(goal);
      if(button.dataset.goalOperation==='checkin'&&action) {
        const outcome=button.dataset.outcome;
        openCheckIn(goal,action,outcome==='note'?'blocked':outcome);
      }
    });
    $('#goalFeedback').addEventListener('click',event=>{const b=event.target.closest('[data-feedback-chat]');if(b)talk(goals.find(g=>String(g.id)===b.dataset.feedbackChat));});
  }
  async function load() {
    if(!ctx?.state.token)return;
    const request=++revision; $('#refreshGoalsBtn').disabled=true; $('#goalList').setAttribute('aria-busy','true');
    try {
      const result=await Promise.all([ctx.api('/goals'),ctx.api('/goals/check-ins'),ctx.api('/goals/preferences')]);
      if(request!==revision)return;
      [goals,entries]=result;$('#goalAutoSync').checked=result[2].autoSync;render();if(!$('#goalWeeklyPage').hidden)loadWeekly();
    } catch(error) {if(request!==revision||error.name==='AbortError')return;$('#goalNotice').textContent=error.message;}
    finally {if(request===revision){$('#refreshGoalsBtn').disabled=false;$('#goalList').setAttribute('aria-busy','false');}}
  }
  function week() {
    const end=new Date();end.setHours(0,0,0,0);const dates=[];
    for(let i=6;i>=0;i--){const d=new Date(end);d.setDate(end.getDate()-i);dates.push(dateKey(d));}
    return dates;
  }
  function render() {
    const dates=week(), recent=entries.filter(e=>dates.includes(localDate(e.createdAt)));
    const steps=recent.filter(e=>!e.undone&&(e.outcome==='done'||e.outcome==='partial'));
    const days=new Set(steps.map(e=>localDate(e.createdAt)));
    $('#goalWeekCount').textContent=String(steps.length);
    $('#goalWeekDays').textContent=days.size ? `${days.size} 天留下了尝试，不必每天都一样。`:'从愿意尝试的一小步开始。';
    $('#goalWeekActivity').innerHTML=dates.map((date,i)=>{const count=steps.filter(e=>localDate(e.createdAt)===date).length;return `<div class="goal-week-day ${count?'has-step':''}" title="${date} · ${count} 次尝试"><span>${['日','一','二','三','四','五','六'][new Date(`${date}T12:00:00`).getDay()]}</span><i>${count?svg('check'):''}</i><small>${i===6?'今天':date.slice(5).replace('-','.')}</small></div>`;}).join('');
    document.querySelectorAll('[data-goal-filter]').forEach(button=>{const key=button.dataset.goalFilter;button.setAttribute('aria-pressed',String(key===filter));button.querySelector('span').textContent=goals.filter(g=>g.status===key).length;});
    const selected=goals.filter(g=>g.status===filter);
    $('#goalList').innerHTML=selected.length?selected.map(card).join(''):`<section class="goal-empty">${svg('feather')}<h3>${filter==='active'?'从一件愿意尝试的小事开始':'这里还没有目标'}</h3><p>${filter==='active'?'不必先做完整的计划，写下你在意的事就可以。':'目标可以随时调整，按自己的节奏继续。'}</p>${filter==='active'?'<button type="button" data-empty-goal>选一个小目标</button>':''}</section>`;
    $('[data-empty-goal]')?.addEventListener('click',()=>openEditor());
    $('#goalRecentList').innerHTML=entries.length?entries.slice(0,8).map(entry=>`<article class="goal-history-entry outcome-${entry.outcome}"><span class="goal-history-mark">${svg(entry.outcome==='done'?'check':entry.outcome==='rest'?'feather':'spark')}</span><div><header><strong>${outcomes[entry.outcome]||'留下记录'}</strong><time>${esc(entry.createdAt.slice(5,10).replace('-','.'))}</time></header><p>${esc(entry.actionTitle)}</p>${entry.note?`<blockquote>${esc(entry.note)}</blockquote>`:''}<small>${esc(entry.goalTitle)}${entry.source==='conversation'?' · 来自对话':''}</small>${entry.undone?'<span class="goal-undone-label">已撤销</span>':entry.source==='conversation'?`<button type="button" class="goal-undo-link" data-checkin-undo="${Number(entry.id)}">撤销这次同步</button>`:''}</div></article>`).join(''):'<p class="goal-history-empty">下一次尝试，会从这里留下记录。哪怕只做了一点，也可以记下来。</p>';
  }
  function attrs(goal,action,operation,outcome='') {return `${planningGoals.has(goal.id)?'disabled':''} data-goal-operation="${operation}" data-goal-id="${Number(goal.id)}" ${action?`data-action-id="${esc(action.id)}"`:''} data-outcome="${outcome}"`;}
  function card(goal) {
    const done=goal.actions.filter(a=>a.status==='done'), next=goal.actions.find(a=>a.status==='planned');
    const latest=entries.find(e=>e.goalId===goal.id&&!e.undone);
    const rows=goal.actions.map((action,index)=>{
      const current=goal.status==='active'&&action===next, complete=action.status==='done';
      return `<li class="goal-ordered-step ${current?'is-current':''} ${complete?'is-done':''}"><span class="goal-step-number">${complete?svg('check'):index+1}</span><div><div class="goal-ordered-title">${esc(action.title)}${action.status==='skipped'?'<small>暂缓</small>':''}</div>${current?`<span class="goal-current-label">当前下一步</span><div class="goal-current-controls"><button type="button" class="goal-done-button" ${attrs(goal,action,'checkin','done')}>${svg('check')} 完成这一步</button><button type="button" ${attrs(goal,action,'checkin','partial')}>做了一点</button><button type="button" class="goal-step-options" aria-label="记录这一步的感受或阻碍" ${attrs(goal,action,'checkin','note')}>记录感受</button></div>`:''}</div></li>`;
    }).join('');
    return `<article class="goal-progress-card"><header class="goal-card-heading"><div><h3>${esc(goal.title)}</h3><p>${done.length} / ${goal.actions.length} 个步骤完成${goal.status==='paused'?' · 已暂缓':goal.status==='completed'?' · 目标已完成':''}</p></div><button class="icon-button" type="button" aria-label="调整目标：${esc(goal.title)}" ${attrs(goal,null,'edit')}>${svg('more')}</button></header>${goal.description?`<p class="goal-description">${esc(goal.description)}</p>`:''}<div class="goal-step-track" aria-hidden="true"><i style="width:${goal.actions.length?done.length/goal.actions.length*100:0}%"></i></div>
      ${rows?`<ol class="goal-ordered-steps">${rows}</ol>`:''}
      ${goal.status==='active'&&!next?`<div class="goal-no-step"><p>${goal.actions.length&&done.length===goal.actions.length?'这一组小步骤已经完成。':goal.actions.length?'剩下的步骤已暂缓，可以调整后继续。':'从几分钟就能尝试的事情开始。'}</p><button type="button" ${attrs(goal,null,'add')}>${svg('plus')} 调整小步骤</button></div>`:''}
      <footer>${planningGoals.has(goal.id)?'<span>进展已保存，正在安排下一步…</span>':latest?`<span>${esc(outcomes[latest.outcome])} · ${esc(latest.createdAt.slice(5,10).replace('-','.'))}</span>`:'<span>一次只关注一小步。</span>'}<button type="button" ${attrs(goal,null,'chat')}>一起聊聊 ${svg('chevron')}</button></footer></article>`;
  }
  function openEditor(goal=null) {
    editing=goal; $('#goalForm').reset();$('#goalEditorTitle').textContent=goal?'调整我的目标':'给自己定一个小目标';
    $('#goalTitle').value=goal?.title||'';$('#goalDescription').value=goal?.description||'';$('#goalCompletionCriteria').value=goal?.completionCriteria||'';$('#goalStatus').value=goal?.status||'active';
    $('#goalStatusField').hidden=!goal;$('#goalPresetList').hidden=!!goal;$('#goalActionLabel').textContent=goal?'追加小步骤（每行一个）':'拆成小步骤（每行一个，选填）';
    $('#goalExistingActions').innerHTML=goal?.actions.map((a,i)=>`<label class="goal-edit-action"><input data-edit-title="${i}" value="${esc(a.title)}" required maxlength="300" aria-label="第 ${i+1} 步" /><select data-edit-action="${i}" aria-label="行动状态：${esc(a.title)}"><option value="planned" ${a.status==='planned'?'selected':''}>准备尝试</option><option value="done" ${a.status==='done'?'selected':''}>已完成</option><option value="skipped" ${a.status==='skipped'?'selected':''}>暂缓这一步</option></select></label>`).join('')||'';
    $('#goalAction').disabled=goal?.actions.length>=12;$('#goalDeleteBtn').hidden=!goal;$('#goalDeleteConfirm').hidden=true;$('#goalEditorError').textContent='';
    $('#goalEditorDialog').showModal();
  }
  function openCheckIn(goal,action,outcome) {
    recording={goal,action};$('#checkInTitle').textContent=action.title;$('#checkInOutcome').value=outcome;$('#checkInProgress').value='';$('#checkInNote').value='';$('#checkInError').textContent='';
    syncRecordForm();$('#goalCheckInDialog').showModal();
  }
  function syncRecordForm(){const outcome=$('#checkInOutcome').value;$('#checkInProgressField').hidden=outcome!=='partial';$('#checkInProgress').required=outcome==='partial';$('#checkInNote').maxLength=outcome==='partial'?250:500;$('#checkInSubmit').textContent=outcome==='done'?'完成并生成下一步':outcome==='partial'?'记录进展，找下一小步':'保存这次记录';}
  function disabled(value) {busy=value;$('#goalsView').querySelectorAll('button').forEach(b=>b.disabled=value);document.querySelectorAll('.goal-dialog button').forEach(b=>b.disabled=value);if(!value)document.querySelectorAll('#goalList [data-goal-id]').forEach(b=>{if(planningGoals.has(Number(b.dataset.goalId)))b.disabled=true;});}
  async function record(goal,action,outcome,note) {
    if(busy||!goal||!action)return;const token=ctx.state.token;disabled(true);$('#goalNotice').textContent='';$('#checkInError').textContent='';
    try {
      const result=await ctx.api(`/goals/${goal.id}/check-ins`,{method:'POST',body:JSON.stringify({actionId:action.id,outcome,note,version:goal.version,confirmed:true})});
      if(token!==ctx.state.token)return;
      goals=goals.map(g=>g.id===goal.id?result.goal:g);entries=[result.checkIn,...entries].slice(0,100);$('#goalCheckInDialog').close();render();
      const title=outcome==='done'?'这一步，完成了。':outcome==='partial'?'做了一点，也值得留下。':outcome==='blocked'?'阻碍也可以被好好记下。':'给自己留一点余地。';
      const text=outcome==='done'?`你完成了「${action.title}」。这次行动已经保存。`:outcome==='partial'?`「${action.title}」有了新的尝试，进度已经同步。`:outcome==='blocked'?'这次记录已经保存。可以把下一步缩小，或一起理清哪里最难。':'今天的休息已经记下。准备好时，再继续这一小步。';
      feedbackEntryId=result.checkIn.id;$('#goalFeedback').hidden=false;$('#goalFeedback').innerHTML=`<span class="goal-feedback-icon">${svg(outcome==='done'?'check':'spark')}</span><div><strong>${esc(title)}</strong><p>${esc(text)}</p></div><button type="button" data-feedback-chat="${Number(goal.id)}">聊聊这次尝试 ${svg('chevron')}</button>`;
      if(outcome==='done'||outcome==='partial'){planningGoals.add(goal.id);reflect(result.goal,result.checkIn,token);}
    } catch(error) {if(error.name==='AbortError')return;$('#goalNotice').textContent=error.message;$('#checkInError').textContent=error.message;}
    finally {if(token===ctx.state.token)disabled(false);}
  }
  async function reflect(goal,entry,token) {
    if(feedbackEntryId===entry.id)$('#goalNotice').textContent=entry.outcome==='done'?'已保存完成记录，正在结合感受和近期对话安排下一步…':'进展已保存，正在寻找更容易继续的一小步…';
    try {const result=await ctx.api(`/goals/${goal.id}/reflection`,{method:'POST',body:JSON.stringify({checkInId:entry.id,version:goal.version})});if(token!==ctx.state.token)return;
      if(!result.available){if(feedbackEntryId===entry.id)$('#goalNotice').textContent='行动记录已保存。下一步建议暂时不可用，原有步骤仍可继续。';return;}
      goals=goals.map(g=>g.id===goal.id?result.goal:g);render();const r=result.reflection;
      if(feedbackEntryId!==entry.id)return;
      $('#goalFeedback').innerHTML=`<span class="goal-feedback-icon">${svg('spark')}</span><div><strong>${r.goalCompleted?'这个目标，已经走到了。':entry.outcome==='done'?'接下来，试试这一小步。':'已经做过的部分，留下来了。'}</strong><p>${esc(r.feedback)}</p>${r.goalCompleted?`<p>达成依据：${esc(r.completionReason)}</p>`:(r.nextStep||r.suggestedNextStep)?`<p class="goal-coach-next">${entry.outcome==='done'&&r.nextStep?'下一步已保存':'下次可以先试试'}：${esc(r.nextStep||r.suggestedNextStep)}</p>`:''}${r.limitReached?'<p>步骤已达到 12 个，可在调整目标中整理后继续。</p>':''}${r.sources?.length?`<details class="goal-coach-sources"><summary>参考了你最近的 ${r.sources.length} 条表达</summary>${r.sources.map(m=>`<blockquote>${esc(m.text)}</blockquote>`).join('')}</details>`:''}</div><button type="button" data-feedback-chat="${Number(goal.id)}">聊聊下一步 ${svg('chevron')}</button>`;
      $('#goalNotice').textContent=r.goalCompleted?'目标已归入“已完成”，完成标准与步骤仍可调整。':'建议已生成，可以随时调整。';
    }catch(error){if(error.name!=='AbortError'&&feedbackEntryId===entry.id)$('#goalNotice').textContent=`记录已保存；下一步未更新：${error.message}`;}
    finally{if(token===ctx.state.token){planningGoals.delete(goal.id);render();if(!busy)disabled(false);}}
  }
  async function loadWeekly(){const request=revision,weekRequest=++weeklyRevision;$('#goalWeeklySummary').textContent='正在整理这一周的记录…';try{const result=await ctx.api(`/goals/weekly?weekOffset=${Number($('#goalWeeklyOffset').value)}`);if(request!==revision||weekRequest!==weeklyRevision)return;weekly=result;weeklyDate=null;renderWeekly();}catch(error){if(request===revision&&weekRequest===weeklyRevision&&error.name!=='AbortError')$('#goalWeeklySummary').textContent=error.message;}}
  function renderWeekly(){if(!weekly)return;const w=weekly;$('#goalWeeklyRange').textContent=`${w.startDate} — ${w.endDate}`;$('#goalWeeklySummary').innerHTML=`<article class="goal-weekly-summary"><span class="goal-eyebrow">这一周的行动概要</span><p>${esc(w.summary)}</p><div><span><strong>${w.completedSteps}</strong> 小步骤完成</span><span><strong>${w.partialRecords}</strong> 次小小尝试</span><span><strong>${w.goalCount}</strong> 个目标有记录</span></div>${w.limited?'<small>本周超过 1000 条记录，当前回顾使用前 1000 条。</small>':''}</article>`;
    const max=Math.max(1,...w.days.map(d=>d.done+d.partial+d.rest+d.blocked));
    $('#goalWeeklyDays').innerHTML=w.days.map(d=>`<button type="button" class="goal-week-column ${d.future?'is-future':''}" data-week-date="${esc(d.date)}" aria-pressed="${d.date===weeklyDate}" aria-label="${esc(d.date)}：完成 ${d.done}，尝试 ${d.partial}，休息 ${d.rest}，阻碍 ${d.blocked}"><strong>${d.future?'—':d.done+d.partial+d.rest+d.blocked}</strong><div class="goal-week-bars">${['done','partial','rest','blocked'].map(k=>`<i class="${k}" style="height:${d[k]/max*100}%"></i>`).join('')}</div><span>${['日','一','二','三','四','五','六'][new Date(d.date+'T12:00:00').getDay()]}</span></button>`).join('');
    const peak=Math.max(1,...w.goals.map(g=>g.done+g.partial+g.rest+g.blocked));$('#goalWeeklyGoals').innerHTML=w.goals.length?w.goals.map(g=>`<article><strong>${esc(g.title)}</strong><div class="goal-week-goal-track">${['done','partial','rest','blocked'].map(k=>`<i class="${k}" style="width:${g[k]/peak*100}%"></i>`).join('')}</div><p>${g.done} 次完成 · ${g.partial} 次尝试${g.blocked?` · ${g.blocked} 次阻碍`:''}${g.rest?` · ${g.rest} 次休息`:''}</p></article>`).join(''):'<p class="goal-weekly-empty">有了行动记录，这里就会长出轨迹。</p>';
    const notes=w.evidence.filter(e=>!weeklyDate||e.createdAt.startsWith(weeklyDate));$('#goalWeeklyEvidence').innerHTML=notes.length?notes.map(e=>`<article><span>${esc(e.createdAt.slice(5,10))} · ${esc(outcomes[e.outcome])}</span><blockquote>${esc(e.note)}</blockquote><small>${esc(e.goalTitle)} · ${esc(e.actionTitle)}</small></article>`).join(''):'<p class="goal-weekly-empty">当前回顾中没有这段时间的文字记录。做完后留下一句话，可以帮助下一步更贴近你。</p>';
  }
  async function saveEditor(event) {
    event.preventDefault();if(busy)return;const token=ctx.state.token;disabled(true);$('#goalEditorError').textContent='';
    const added=$('#goalAction').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean), old=editing;
    const actions=old?old.actions.map((a,i)=>({...a,title:$(`[data-edit-title="${i}"]`).value.trim(),status:$(`[data-edit-action="${i}"]`).value})):[];
    if(actions.length+added.length>12||added.some(s=>s.length>300)){$('#goalEditorError').textContent='最多 12 个步骤，每一步不超过 300 字。';disabled(false);return;}
    for(const title of added)actions.push({id:crypto.randomUUID(),title,status:'planned',feedback:[]});
    try {
      const saved=await ctx.api(old?`/goals/${old.id}`:'/goals',{method:old?'PUT':'POST',body:JSON.stringify({title:$('#goalTitle').value.trim(),description:$('#goalDescription').value.trim(),completionCriteria:$('#goalCompletionCriteria').value.trim(),status:$('#goalStatus').value,version:old?.version,confirmed:true,actions})});
      if(token!==ctx.state.token)return;
      goals=old?goals.map(g=>g.id===saved.id?saved:g):[saved,...goals];filter=saved.status;$('#goalEditorDialog').close();render();$('#goalNotice').textContent=old?'调整已保存，按现在合适的节奏继续。':'目标已保存。只需要从第一小步开始。';
    } catch(error){if(error.name!=='AbortError')$('#goalEditorError').textContent=error.message;}
    finally {if(token===ctx.state.token)disabled(false);}
  }
  async function deleteGoal() {
    if(busy||!editing)return;const token=ctx.state.token, id=editing.id;disabled(true);
    try {await ctx.api(`/goals/${id}`,{method:'DELETE'});if(token!==ctx.state.token)return;goals=goals.filter(g=>g.id!==id);entries=entries.filter(e=>e.goalId!==id);$('#goalEditorDialog').close();render();$('#goalNotice').textContent='目标和相关进度记录已删除。';}
    catch(error){if(error.name!=='AbortError')$('#goalEditorError').textContent=error.message;}
    finally {if(token===ctx.state.token)disabled(false);}
  }
  function talk(goal) {
    if(!goal||ctx.state.activeStream)return;
    ctx.startNewConversation();$('#messageInput').value=`我想聊聊目标「${goal.title}」的进展：`;$('#messageInput').dispatchEvent(new Event('input',{bubbles:true}));$('#messageInput').focus();
  }
  async function undo(button) {
    const id=Number(button.dataset.checkinUndo);button.disabled=true;
    try {
      const result=await ctx.api(`/goals/check-ins/${id}/undo`,{method:'POST',body:'{}'});
      goals=goals.map(g=>g.id===result.goal.id?result.goal:g);entries=entries.map(e=>e.id===id?result.checkIn:e);render();
      button.textContent='已撤销同步';button.removeAttribute('data-checkin-undo');$('#goalNotice').textContent='这次自动同步已撤销。';
      const notice=button.closest('[data-synced-checkin]');if(notice){notice.dataset.undone='true';const summary=notice.querySelector('div');if(summary)summary.innerHTML='<strong>这次同步已撤销</strong><p>这次自动安排的下一步或目标完成状态也已撤销。</p>';}
    } catch(error){if(error.name!=='AbortError'){button.disabled=false;$('#goalNotice').textContent=error.message;ctx.toast(error.message);}}
  }
  function showSynced(updates,message) {
    if(!updates?.length||!message?.isConnected)return;
    let container=message.querySelector('.message-goal-updates');
    if(!container){container=document.createElement('div');container.className='message-goal-updates';container.setAttribute('aria-live','polite');message.appendChild(container);container.addEventListener('click',event=>{const button=event.target.closest('[data-checkin-undo]');if(button)undo(button);});}
    for(const entry of updates) {
      let notice=container.querySelector(`[data-synced-checkin="${Number(entry.id)}"]`);
      if(notice&&(!entry.planningStatus||entry.planningStatus==='pending'&&['ready','unavailable'].includes(notice.dataset.planningStatus)||notice.dataset.undone==='true'))continue;
      if(!notice){notice=document.createElement('div');notice.dataset.syncedCheckin=String(Number(entry.id));container.appendChild(notice);}
      notice.dataset.planningStatus=entry.planningStatus||'';
      const r=entry.reflection, summary=entry.outcome==='goal_completed'?'已按你的决定结束目标，不再追加步骤。':entry.outcome==='paused'?'目标已暂缓，不再追加步骤。':entry.outcome==='revised'?'指定步骤已修改，可以在我的目标中查看。':entry.planningStatus==='pending'?'已保存，正在判断目标与下一步…':entry.planningStatus==='unavailable'?'完成记录已保存，下一步规划暂时不可用。':r?.goalCompleted?'目标已达成。':r?.nextStep?`${r.stepAdjustments?.length?'已结合这次反馈调整后续。':''}下一步已保存：${r.nextStep}`:'';
      notice.innerHTML=`${svg('check')}<div><strong>已同步到「${esc(entry.goalTitle)}」</strong><p>${esc(entry.actionTitle)} · ${esc(outcomes[entry.outcome]||'进度已更新')}</p>${summary?`<p>${esc(summary)}</p>`:''}</div><button type="button" data-checkin-undo="${Number(entry.id)}">撤销</button>`;
    }
  }
  function showCreated(created,message) {
    if(!created?.length||!message?.isConnected)return;
    let container=message.querySelector('.message-goal-created');
    if(!container){container=document.createElement('div');container.className='message-goal-updates message-goal-created';container.setAttribute('aria-live','polite');message.appendChild(container);container.addEventListener('click',()=>ctx.switchView('goalsView'));}
    for(const goal of created){if(container.querySelector(`[data-created-goal="${Number(goal.id)}"]`))continue;
      const card=document.createElement('div');card.dataset.createdGoal=String(Number(goal.id));card.innerHTML=`${svg('goal')}<div><strong>已创建「${esc(goal.title)}」</strong><p>${goal.actions.length} 个小步骤 · 可以随时调整</p></div><button type="button">查看目标</button>`;container.appendChild(card);}
  }
  function reset() {
    revision++;goals=[];entries=[];filter='active';editing=null;recording=null;weekly=null;weeklyDate=null;feedbackEntryId=null;planningGoals.clear();disabled(false);
    $('#goalEditorDialog').close();$('#goalCheckInDialog').close();$('#goalForm').reset();$('#checkInForm').reset();
    ['#goalList','#goalRecentList','#goalWeekActivity','#goalNotice','#goalFeedback','#goalExistingActions','#goalEditorError','#checkInError','#checkInTitle'].forEach(s=>$(s).replaceChildren());
    $('#goalFeedback').hidden=true;$('#goalStepsPage').hidden=false;$('#goalWeeklyPage').hidden=true;['#goalWeeklySummary','#goalWeeklyDays','#goalWeeklyGoals','#goalWeeklyEvidence','#goalWeeklyRange'].forEach(s=>$(s).replaceChildren());document.querySelectorAll('[data-goal-page]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.goalPage==='steps')));$('#goalWeekCount').textContent='0';$('#goalWeekDays').textContent='从愿意尝试的一小步开始。';
    $('#goalList').setAttribute('aria-busy','false');
  }
  window.PsychGoalsView=Object.freeze({bind,load,reset,showSynced,showCreated});
})();
