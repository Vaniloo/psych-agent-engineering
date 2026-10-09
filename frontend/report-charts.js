/* Charts count cited expressions; they never interpolate mood scores or missing days. */
(() => {
  const categories = Object.freeze([
    {key:'tense',label:'紧张 / 压力',face:'😟'},
    {key:'sad',label:'低落 / 难过',face:'😔'},
    {key:'angry',label:'烦躁 / 生气',face:'😠'},
    {key:'calm',label:'平静 / 放松',face:'😌'},
    {key:'positive',label:'开心 / 期待',face:'🙂'}
  ]);
  const dateLabel = date => date.slice(5).replace('-','.');
  function model(data) {
    const evidence = new Map((data.evidence || []).map(item=>[item.messageId,item]));
    const seen = new Set(), observations = [];
    for (const item of data.emotionObservations || []) {
      const original = evidence.get(item.messageId), key = `${item.messageId}:${item.category}`;
      if (!original || !categories.some(c=>c.key===item.category) || !item.quote || !original.text.includes(item.quote) || seen.has(key)) continue;
      const date = original.createdAt.slice(0,10);
      if (date < data.periodStart || date > data.periodEnd) continue;
      seen.add(key); observations.push({...item,date});
    }
    const start = Date.parse(`${data.periodStart}T00:00:00Z`), dayMs=86400000;
    const length = Math.round((Date.parse(`${data.periodEnd}T00:00:00Z`) - start) / dayMs) + 1;
    const size = length <= 7 ? 1 : length <= 30 ? 5 : 14, bins = [];
    for (let offset=0;offset<length;offset+=size) {
      const from=new Date(start+offset*dayMs).toISOString().slice(0,10);
      const to=new Date(start+Math.min(length-1,offset+size-1)*dayMs).toISOString().slice(0,10);
      const items=observations.filter(item=>item.date>=from&&item.date<=to);
      const ids=[...new Set(items.map(item=>item.messageId))];
      bins.push({from,to,items,ids,counts:categories.map(c=>items.filter(item=>item.category===c.key).length)});
    }
    const groups=categories.map(c=>({...c,ids:observations.filter(item=>item.category===c.key).map(item=>item.messageId)}));
    return {groups,bins,expressionCount:new Set(observations.map(item=>item.messageId)).size,observationCount:observations.length};
  }
  function render(data) {
    const m=model(data), max=Math.max(1,...m.groups.map(c=>c.ids.length));
    const total=m.bins.map(b=>b.items.length), peak=Math.max(1,...total);
    const top=m.groups.filter(g=>g.ids.length).sort((a,b)=>b.ids.length-a.ids.length).slice(0,3);
    const distribution=m.expressionCount ? `<div class="report-chart-tags">${top.map(g=>`<span>${g.label}</span>`).join('')}</div><div class="report-distribution" role="group" aria-label="各类感受的表达次数">${m.groups.map((g,i)=>`<button type="button" class="report-emotion-bar mood-${g.key}" data-emotion-group="${i}" aria-pressed="false" aria-label="${g.label}，${g.ids.length} 条表达，查看原话" ${g.ids.length?'':'disabled'}><span class="report-bar-track"><b class="report-bar-value">${g.ids.length}</b><i style="height:${g.ids.length/max*100}%"></i></span><span class="report-mood-face" aria-hidden="true">${g.face}</span><span class="report-mood-label">${g.label.replace(' / ','<br>')}</span></button>`).join('')}</div>` : `<div class="report-chart-empty"><span>还没有足够的情绪表达</span><p>本期没有提取到可引用的明确感受，暂时不绘制分布。</p></div>`;
    const trend=m.expressionCount ? `<div class="report-trend-legend">${categories.map(g=>`<span class="mood-${g.key}"><i></i>${g.label.split(' / ')[0]}</span>`).join('')}</div><div class="report-trend-plot" role="group" aria-label="按日期统计情绪表达次数"><span class="report-trend-axis">${peak} 次标注</span><div class="report-trend-bins">${m.bins.map((bin,i)=>`<button type="button" class="report-trend-bin" data-emotion-bin="${i}" aria-pressed="false" aria-label="${dateLabel(bin.from)} 至 ${dateLabel(bin.to)}，${bin.ids.length} 条表达，${bin.items.length} 次情绪标注${bin.items.length?'，查看原话':'，无情绪标注'}" ${bin.items.length?'':'disabled'}><span class="report-stack-track"><b class="report-bar-value">${bin.items.length||'·'}</b><span class="report-stack" style="height:${bin.items.length/peak*100}%">${categories.map((g,j)=>bin.counts[j]?`<i class="mood-${g.key}" style="flex:${bin.counts[j]}"></i>`:'').join('')}</span></span><span class="report-bin-label">${dateLabel(bin.from)}</span></button>`).join('')}</div></div><p class="report-chart-note">${dateLabel(data.periodStart)} — ${dateLabel(data.periodEnd)} · 空白处表示没有可引用的情绪表达。</p>` : `<div class="report-chart-empty"><span>让变化慢慢留下记录</span><p>更多不同时点的真实表达，会让时间分布更清楚。</p></div>`;
    return `<section class="report-visuals" aria-label="对话中的情绪分布与时间变化"><article class="report-chart-card"><header><div><span class="report-eyebrow">EMOTIONAL LANDSCAPE</span><h3>这段时间的情绪分布</h3></div><span class="report-chart-count">${m.expressionCount} 条表达</span></header><p class="report-chart-subtitle">原话里出现过的感受，按表达次数统计</p>${distribution}<div id="reportEmotionEvidence" class="report-chart-evidence"><p class="report-chart-hint">点击有颜色的柱子，看看对应的表达</p></div></article><article class="report-chart-card"><header><div><span class="report-eyebrow">OVER TIME</span><h3>情绪表达的时间变化</h3></div></header><p class="report-chart-subtitle">颜色对应不同感受，柱高表示提及次数</p>${trend}<div id="reportTrendEvidence" class="report-chart-evidence"><p class="report-chart-hint">点击有记录的时段，回顾当时聊过什么</p></div></article><p class="report-chart-caption">AI 从本次采样原话中归类；同一条表达可能包含多种感受。次数反映谈及频率，不代表情绪强度。</p></section>`;
  }
  function bind(data, evidenceDetails) {
    const m=model(data);
    for (const [selector,groups,target,label] of [
      ['[data-emotion-group]',m.groups,'#reportEmotionEvidence','这类感受的原话'],
      ['[data-emotion-bin]',m.bins,'#reportTrendEvidence','这个时段的原话']
    ]) document.querySelectorAll(selector).forEach(button=>button.addEventListener('click',()=>{
      const index=Number(button.dataset.emotionGroup ?? button.dataset.emotionBin), group=groups[index];
      document.querySelectorAll(selector).forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
      const container=document.querySelector(target);
      container.innerHTML=evidenceDetails(group.ids,data,label);
      container.querySelector('details')?.setAttribute('open','');
    }));
  }
  window.PsychReportCharts=Object.freeze({model,render,bind});
})();
