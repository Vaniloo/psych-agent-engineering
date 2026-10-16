/* Per-turn delivery receipts. Saved chat and updated long-term memory are distinct. */
(() => {
  const copy = Object.freeze({
    pending: ['正在整理记忆', '回复已保存，记忆正在后台整理。你可以继续聊天。'],
    indexed: ['近期记忆已更新', '这轮对话已加入近期记忆，后续相关对话可以查找；这不代表新增了长期记忆。'],
    updated: ['记忆已更新', '已根据这轮对话补充或更新记忆，后续相关对话可以使用。'],
    partial: ['部分记忆已更新', '部分信息已更新，另一些尚未确认。近期对话仍可查找。'],
    unchanged: ['记忆已整理', '本轮没有新增长期记忆，近期对话仍可查找。'],
    review: ['长期记忆待确认', '本轮暂未确认长期记忆的变更，先保留对话记录，避免误改已有记忆。'],
    unconfirmed: ['记忆更新待确认', '聊天记录已保存，暂时无法确认记忆整理结果。可以稍后重新检查。'],
    not_saved: ['本轮保存待确认', '暂未确认这轮对话已保存，因此无法确认记忆更新结果。'],
  });
  function describe(payload) {
    const status = Object.hasOwn(copy, payload?.status) ? payload.status : 'unconfirmed';
    const [label, detail] = copy[status];
    return {status, label: status === 'unchanged' && payload?.retentionOptout === true ? '未新增长期记忆' : label,
      detail: status === 'unchanged' && payload?.retentionOptout === true
        ? '按你的要求，本轮没有写入长期记忆。原始聊天记录仍按对话记录保存。' : detail};
  }

  function attach(message, options) {
    const group = document.createElement('details');
    group.className = 'message-memory'; group.hidden = true;
    group.setAttribute('aria-label', '本轮记忆更新');
    const heading = document.createElement('summary');
    heading.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.8 12 7l4.5 2-4.5 2-2 4.2L8 11 3.5 9 8 7Z"/><path d="m15.5 13 .8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8Z"/></svg>';
    const label = document.createElement('span');
    label.setAttribute('aria-live', 'polite'); label.setAttribute('aria-atomic', 'true');
    heading.append(label);
    const panel = document.createElement('div'); panel.className = 'memory-status-detail';
    const detail = document.createElement('p');
    const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重新检查';
    panel.append(detail, retry); group.append(heading, panel);
    message.insertBefore(group, message.querySelector('.message-actions'));
    let timer, requestId, attempts = 0, started = 0, controller, stopped = false;
    const current = () => !stopped && message.isConnected && options.isCurrent();
    function show(payload) {
      if (!current()) return;
      const value = describe(payload);
      group.hidden = false; group.dataset.status = value.status;
      label.textContent = value.label; detail.textContent = value.detail;
      retry.hidden = !['unconfirmed', 'partial', 'review'].includes(value.status) || !requestId;
      options.onChange?.(value);
    }
    function stop() { clearTimeout(timer); controller?.abort(); }
    async function poll() {
      if (!current()) { stop(); return; }
      controller = new AbortController();
      let result;
      try { result = await options.fetchStatus(requestId, controller.signal); }
      catch { result = {status: 'pending'}; }
      if (!current()) { stop(); return; }
      attempts++;
      if (result?.status !== 'pending') { show(result); return; }
      if (attempts >= 10 || Date.now() - started >= 45000) { show({status: 'unconfirmed'}); return; }
      show(result);
      timer = setTimeout(poll, [800, 1200, 2000, 2000, 3000, 4000, 4000, 5000, 5000][attempts - 1]);
    }
    function check() {
      stop(); attempts = 0; started = Date.now(); show({status: 'pending'});
      timer = setTimeout(poll, 0);
    }
    retry.addEventListener('click', check); // Read status only; never retries a memory write.
    return Object.freeze({
      pending: () => show({status: 'pending'}),
      complete: (id, saved) => {
        if (!saved || typeof id !== 'string' || !/^[\w-]{1,128}$/.test(id)) {
          show({status: 'not_saved'}); return;
        }
        requestId = id; check();
      },
      interrupt: () => { stop(); if (!group.hidden) show({status: 'not_saved'}); },
      destroy: () => { stopped = true; stop(); },
    });
  }
  window.PsychMemoryStatus = Object.freeze({attach, describe});
})();
