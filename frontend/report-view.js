/* Own conversation reflections are separate from per-turn risk scans and admin metrics. */
(() => {
  let context, days = 30, revision = 0;
  const $ = selector => document.querySelector(selector);
  const escape = window.escapeHtml;
  const shortDate = value => value ? String(value).slice(5, 10).replace('-', '.') : '—';
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"/></svg>`;

  function renderRiskDistribution(items) {
    const max = Math.max(1, ...items.map((item) => item.count));
    $("#riskDistribution").innerHTML = items.length
      ? items.map((item) => `
        <div class="bar-row">
          <span class="tag ${escape(item.risk)}">${escape(item.risk)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.round(item.count / max * 100)}%"></div></div>
          <strong>${item.count}</strong>
        </div>
      `).join("")
      : context.empty("暂无风险数据");
  }

  function renderEmotionTrend(items) {
    $("#emotionTrend").innerHTML = items.length
      ? items.map((item) => `
        <div class="trend-point">
          <span>${escape(item.date)}</span>
          <span>${escape(item.emotion)}</span>
          <span class="tag">${item.count} 次</span>
        </div>
      `).join("")
      : context.empty("暂无趋势数据");
  }

  function metric(label, value) {
    return `<div class="metric"><span>${escape(label)}</span><strong>${escape(String(value))}</strong></div>`;
  }

  function reportItem(item) {
    return `
      <article class="list-item">
        <h3>${escape(item.emotion || "未知情绪")}</h3>
        <p>
          <span class="tag ${escape(item.risk || "")}">${escape(item.risk || "unknown")}</span>
          <span class="tag">${escape(context.formatTime(item.createdAt))}</span>
        </p>
        <p>${escape(item.message || "")}</p>
      </article>
    `;
  }

  function bind(options) {
    context = options;
    $('#reportContent').addEventListener('click', event => {
      const button = event.target.closest('[data-report-session]');
      if (button) context.continueConversation(Number(button.dataset.reportSession));
    });
    $('#refreshReportsBtn').addEventListener('click', () => load(true));
    document.querySelectorAll('[data-report-days]').forEach(button => button.addEventListener('click', () => {
      days = Number(button.dataset.reportDays);
      document.querySelectorAll('[data-report-days]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      load();
    }));
    $('#adminReportDetails').addEventListener('toggle', async event => {
      if (!event.target.open || context.state.user?.role !== 'ADMIN') return;
      try {
        const dashboard = await context.api('/reports/dashboard?recentLimit=10&topRiskLimit=5');
        const reports = dashboard.reportSummaryResponse || [];
        $('#dashboardGrid').innerHTML = metric('自己的近期记录', reports.length) + metric('全局风险用户', dashboard.topRiskUserResponse?.length || 0);
        renderRiskDistribution(dashboard.riskDistribution || []);
        renderEmotionTrend(dashboard.emotionTrend || []);
        $('#reportsList').innerHTML = reports.map(reportItem).join('');
      } catch (error) { if (error.name !== 'AbortError') $('#reportsList').textContent = error.message; }
    });
  }

  async function load(refresh = false) {
    if (!context?.state.token) return;
    const request = ++revision;
    $('#refreshReportsBtn').disabled = true;
    $('#refreshReportsBtn').textContent = '正在整理…';
    $('#reportContent').setAttribute('aria-busy', 'true');
    $('#reportContent').innerHTML = `<div class="report-loading"><span class="report-eyebrow">连接几段对话中的线索</span><h3>正在整理最近的表达</h3><p>会从你的原话中回顾近期话题与情绪表达。</p><div class="report-skeleton"></div><div class="report-skeleton short"></div></div>`;
    try {
      const overview = await context.api(`/reports/overview?days=${days}&refresh=${refresh}`, {cache:'no-store'});
      if (request !== revision) return;
      render(overview);
    } catch (error) {
      if (request !== revision || error.name === 'AbortError') return;
      $('#reportContent').innerHTML = `<section class="report-empty"><span class="report-mark">${icon('report')}</span><h3>这次回顾暂时没有整理好</h3><p>${escape(error.message)}</p><button type="button" data-report-retry>再试一次</button></section>`;
      $('[data-report-retry]').addEventListener('click', () => load(true));
    } finally {
      if (request === revision) {
        $('#refreshReportsBtn').disabled = false;
        $('#refreshReportsBtn').textContent = '重新整理';
        $('#reportContent').setAttribute('aria-busy','false');
      }
    }
  }

  function evidenceDetails(ids, data, label = '查看原话依据') {
    const selected = (data.evidence || []).filter(item => ids.includes(item.messageId));
    if (!selected.length) return '';
    return `<details class="report-evidence"><summary>${escape(label)}<span>${selected.length} 条</span></summary><div class="report-quotes">${selected.map(item => {
      const source = (data.sources || []).find(source => source.sessionId === item.sessionId);
      return `<blockquote><p>${escape(item.text)}</p><footer><span>${escape(shortDate(item.createdAt))} · ${escape(source?.title || '对话')}</span><button type="button" data-report-session="${Number(item.sessionId)}">回到对话 ${icon('chevron')}</button></footer></blockquote>`;
    }).join('')}</div></details>`;
  }

  function insight(item, index, data, type) {
    const sessions = new Set((data.evidence || []).filter(e => item.messageIds.includes(e.messageId)).map(e=>e.sessionId));
    return `<article class="report-insight ${type}"><header><span class="report-insight-index">${String(index + 1).padStart(2,'0')}</span><h4>${escape(item.title)}</h4></header><p>${escape(item.description)}</p><div class="report-insight-meta">来自 ${sessions.size} 段对话中的表达</div>${evidenceDetails(item.messageIds,data)}</article>`;
  }

  function section(items, data, type, title, subtitle, emptyText) {
    return `<section class="report-section ${type}-section"><div class="report-section-heading"><h3>${escape(title)}</h3><p>${escape(subtitle)}</p></div><div class="report-insights ${type}-insights">${items.length ? items.map((item,index)=>insight(item,index,data,type)).join('') : `<p class="report-muted-empty">${escape(emptyText)}</p>`}</div></section>`;
  }

  function activity(data) {
    const end = new Date(`${data.periodEnd}T00:00:00Z`);
    const length = Math.min(14, data.days);
    const counts = new Map((data.activity || []).map(item=>[item.date,item.expressionCount]));
    const cells = [];
    for (let offset = length - 1; offset >= 0; offset--) {
      const date = new Date(end); date.setUTCDate(end.getUTCDate() - offset);
      const value = date.toISOString().slice(0,10), count = counts.get(value) || 0;
      cells.push(`<span class="report-day ${count ? 'has-record' : ''}" title="${escape(value)} · 采样中 ${Number(count)} 次表达" aria-label="${escape(value)}，${count ? '有记录' : '采样中无记录'}">${date.getUTCDate()}</span>`);
    }
    return `<div class="report-activity"><header><strong>最近 ${length} 天聊过的日子</strong><span>有记录 <i></i></span></header><div class="report-days">${cells.join('')}</div><p>标记本次回顾采样中的表达，不代表情绪好坏。</p></div>`;
  }

  function render(data) {
    const coverage = data.coverage || {};
    $('#reportsSubtitle').textContent = `${data.periodStart.replaceAll('-','.')} — ${data.periodEnd.replaceAll('-','.')} · 根据你自己的对话整理`;
    if (data.status === 'empty') {
      $('#reportContent').innerHTML = `<section class="report-empty"><span class="report-mark">${icon('feather')}</span><span class="report-eyebrow">回顾会从一次真实的表达开始</span><h3>最近这段时间，你过得怎么样？</h3><p>${escape(data.summary)}</p><button type="button" class="primary-action" data-report-chat>聊聊最近的感受 ${icon('chevron')}</button></section>`;
      $('[data-report-chat]').addEventListener('click', () => { context.startNewConversation(); $('#messageInput').value='想聊聊我最近的感受。'; $('#messageInput').dispatchEvent(new Event('input',{bubbles:true})); $('#messageInput').focus(); });
      return;
    }
    const ready = data.status === 'ready';
    const hero = `<div class="report-overview-grid"><section class="report-summary-card"><div class="report-summary-label">${icon('spark')}<span>${ready ? '把几次聊天连起来看' : '先回到真实的表达'}</span></div><h3>${ready ? '这段时间的整体情况' : data.status === 'limited' ? '再多一点表达，再慢慢形成回顾' : '对话已收集，概要暂未生成'}</h3><p class="report-summary-text">${escape(data.summary)}</p>${ready ? evidenceDetails(data.summaryMessageIds || [], data, '展开概要的依据') : ''}<span class="report-generated">${ready ? 'AI 整理 · ' : ''}${escape(shortDate(data.generatedAt))} 更新</span></section><aside class="report-coverage-card"><span class="report-eyebrow">这份回顾来自</span><div class="report-coverage-number">${Number(coverage.conversationCount || 0)}<span>段对话</span></div><div class="report-coverage-lines"><span><b>${Number(coverage.expressionCount || 0)}</b> 次原话表达</span><span><b>${Number(coverage.activeDays || 0)}</b> 天留下记录</span></div><p>${coverage.sampled ? '选取近期对话中的部分表达，未覆盖全部内容。' : '只使用这个时间范围内的用户原话。'}</p>${activity(data)}</aside></div>`;
    const content = ready ? `${window.PsychReportCharts.render(data)}${section(data.themes || [],data,'theme','最近在意的话题','看看哪些事情占据了你的心思','目前没有足够依据概括主要话题。')}${(data.reflectionQuestions || []).length ? `<section class="report-reflection"><span class="report-eyebrow">如果愿意，可以继续想一想</span>${data.reflectionQuestions.map(question=>`<p>${escape(question)}</p>`).join('')}<button type="button" data-report-goals>去看看我的目标 ${icon('chevron')}</button></section>` : ''}` : `<section class="report-section"><div class="report-section-heading"><h3>本期的原话片段</h3><p>这里是记录本身，暂时不作心理判断。</p></div>${evidenceDetails((data.evidence || []).map(e=>e.messageId),data,'展开最近的表达')}</section>`;
    const sources = `<details class="report-sources"><summary>本次回顾依据<span>${(data.sources || []).length} 段对话</span></summary><div class="report-source-list">${(data.sources || []).map(source=>`<button type="button" data-report-session="${Number(source.sessionId)}"><span>${icon('chat')}<strong>${escape(source.title)}</strong></span><span>${escape(shortDate(source.lastMentionAt))} · ${Number(source.expressionCount)} 次表达 ${icon('chevron')}</span></button>`).join('')}</div></details><p class="report-footnote">这是一份基于对话的回顾，不是诊断或心理量表评估。你可以展开原话，确认这些观察是否符合自己的感受。</p>`;
    $('#reportContent').innerHTML = hero + content + sources;
    if (ready) window.PsychReportCharts.bind(data, evidenceDetails);
    $('[data-report-goals]')?.addEventListener('click',()=>context.switchView('goalsView'));
  }

  function reset() {
    revision++; days = 30;
    $('#reportContent').replaceChildren();
    $('#reportContent').setAttribute('aria-busy','false');
    $('#reportsSubtitle').textContent = '把几次聊天放在一起，看见最近的感受与困扰。';
    $('#refreshReportsBtn').disabled = false; $('#refreshReportsBtn').textContent = '重新整理';
    $('#adminReportDetails').open = false;
    document.querySelectorAll('[data-report-days]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.reportDays === '30')));
  }
  window.PsychReportView = Object.freeze({bind,load,reset});
})();
