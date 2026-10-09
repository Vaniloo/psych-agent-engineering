const config = window.PSYCH_AGENT_CONFIG || {};
const state = {
  apiBaseUrl: config.forceApiBaseUrl
    ? config.apiBaseUrl
    : localStorage.getItem("psychAgentApiBaseUrl") || config.apiBaseUrl || "http://localhost:8080",
  token: localStorage.getItem("psychAgentToken") || "",
  user: JSON.parse(localStorage.getItem("psychAgentUser") || "null"),
  currentSessionId: null,
  profiles: [],
  adminMemories: [],
  backendOnline: false,
  conversations: [],
  currentRagTrace: null,
  currentMetrics: null,
  activeStream: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function init() {
  normalizeStoredSession();
  bindNavigation();
  bindAuth();
  bindChat();
  bindRoleCards();
  bindProfile();
  bindReports();
  bindGoals();
  bindMetrics();
  bindHelp();
  bindKnowledge();
  bindSettings();
  syncConnectionLabel();
  syncAuthUi();
  applyRoleVisibility();
  hydrateSettings();
  renderRagTrace(null);
  resetExecutionTimeline();
  syncRunState();
  addMessage("assistant", "你好，我是 Psych Agent。登录后可以开始对话、查看报告和帮助资源。");
  pingBackend();
  window.setInterval(pingBackend, 60000);
  if (state.token) {
    loadConversations();
    loadRoleCards();
    refreshReports();
    loadMetrics({ silent: true });
    loadHelpResources();
    if (isAdmin()) {
      refreshProfile();
    }
  }
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (state.token && options.auth !== false) {
    headers.Authorization = `Bearer ${state.token}`;
  }

  const { auth, ...fetchOptions } = options;
  return requestJson(path, { ...fetchOptions, headers }, ["GET", "HEAD"].includes((fetchOptions.method || "GET").toUpperCase()));
}

async function requestJson(path, options, allowFallback) {
  try {
    const response = await fetch(`${state.apiBaseUrl}${path}`, options);
    const text = await response.text();
    const data = text ? parseJson(text) : null;
    if (response.status === 401 && options.headers?.Authorization) {
      clearSession(false);
    }
    if (!response.ok || data?.success === false) {
      throw new Error(data?.message || text || `请求失败：${response.status}`);
    }
    state.backendOnline = true;
    syncConnectionLabel();
    return data ?? text;
  } catch (error) {
    const fallbackUrl = getLoopbackFallbackUrl(state.apiBaseUrl);
    if (error instanceof TypeError && allowFallback && fallbackUrl) {
      state.apiBaseUrl = fallbackUrl;
      localStorage.setItem("psychAgentApiBaseUrl", state.apiBaseUrl);
      hydrateSettings();
      syncConnectionLabel();
      return requestJson(path, options, false);
    }
    if (error instanceof TypeError) {
      state.backendOnline = false;
      syncConnectionLabel();
      throw new Error(`无法连接后端：${state.apiBaseUrl}。请确认后端已启动，并打开 ${state.apiBaseUrl}/test/ping 检查。`);
    }
    throw error;
  }
}

function getLoopbackFallbackUrl(apiBaseUrl) {
  if (apiBaseUrl.includes("localhost")) {
    return apiBaseUrl.replace("localhost", "127.0.0.1");
  }
  if (apiBaseUrl.includes("127.0.0.1")) {
    return apiBaseUrl.replace("127.0.0.1", "localhost");
  }
  return "";
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function bindNavigation() {
  $$(".nav-tab").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".nav-tab").forEach((tab) => tab.classList.remove("active"));
      $$(".view").forEach((view) => view.classList.remove("active-view"));
      button.classList.add("active");
      $(`#${button.dataset.view}`).classList.add("active-view");
      if (button.dataset.view === "goalsView") loadGoals();
      if (button.dataset.view === "metricsView") {
        loadMetrics();
      }
    });
  });


}

function bindAuth() {
  $("#loginBtn").addEventListener("click", async () => {
    const payload = readCredentials();
    if (!payload) return;
    try {
      const data = await api("/auth/login", {
        method: "POST",
        auth: false,
        body: JSON.stringify(payload),
      });
      state.token = data.token;
      state.user = { id: data.id, username: data.username, role: data.role };
      localStorage.setItem("psychAgentToken", state.token);
      localStorage.setItem("psychAgentUser", JSON.stringify(state.user));
      $("#password").value = "";
      syncConnectionLabel();
      syncAuthUi();
      applyRoleVisibility();
      activateDefaultView();
      toast("登录成功");
      await loadConversations();
      await loadRoleCards();
      await refreshReports();
      await loadMetrics({ silent: true });
      await loadHelpResources();
      if (isAdmin()) {
        await refreshProfile();
      }
    } catch (error) {
      toast(error.message);
    }
  });

  $("#registerBtn").addEventListener("click", async () => {
    const payload = readCredentials();
    if (!payload) return;
    try {
      await api("/auth/register", {
        method: "POST",
        auth: false,
        body: JSON.stringify({ ...payload, role: "USER" }),
      });
      $("#password").value = "";
      toast("注册成功，可以登录了");
    } catch (error) {
      toast(error.message);
    }
  });

  $("#logoutBtn").addEventListener("click", () => {
    clearSession(true);
  });
}

function normalizeStoredSession() {
  if (state.token && state.user) return;
  state.token = "";
  state.user = null;
  localStorage.removeItem("psychAgentToken");
  localStorage.removeItem("psychAgentUser");
}

function clearSession(showToast) {
  const hadSession = Boolean(state.token || state.user);
  state.token = "";
  state.user = null;
  state.currentSessionId = null;
  localStorage.removeItem("psychAgentToken");
  localStorage.removeItem("psychAgentUser");
  $("#username").value = "";
  $("#password").value = "";
  $("#conversationList").innerHTML = "";
  $("#chatLog").innerHTML = "";
  $("#dashboardGrid").innerHTML = "";
  $("#reportsList").innerHTML = "";
  $("#userReportSummary").innerHTML = "";
  $("#metricsOverview").innerHTML = "";
  $("#recentRuns").innerHTML = "";
  $("#runTrendChart").innerHTML = "";
  $("#runtimeQuality").innerHTML = "";
  addMessage("assistant", "你好，我是 Psych Agent。登录后可以开始对话、查看报告和帮助资源。");
  renderRagTrace(null);
  setAgentStatuses({ rag: "待检索", tool: "空闲", safety: "常规", session: "未开始" });
  resetExecutionTimeline();
  $("#contextBudgetValue").textContent = "—";
  $("#memoryStatusValue").textContent = "待检查";
  resetLiveMetrics();
  syncRunState();
  syncConnectionLabel();
  syncAuthUi();
  applyRoleVisibility();
  if (showToast && hadSession) {
    toast("已退出登录");
  }
}

function readCredentials() {
  const username = $("#username").value.trim();
  const password = $("#password").value;
  if (!username || !password) {
    toast("请输入账号和密码");
    return null;
  }
  return { username, password };
}

function bindChat() {
  $("#newChatBtn").addEventListener("click", startNewConversation);
  $("#messageInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#chatForm").requestSubmit();
    }
  });
  $("#chatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.activeStream) {
      toast("上一条回复仍在生成");
      return;
    }
    const input = $("#messageInput");
    const message = input.value.trim();
    if (!message) return;
    state.activeStream = true;
    const sendButton = $(".send-button");
    sendButton.disabled = true;
    addMessage("user", message);
    input.value = "";
    resetExecutionTimeline();
    resetLiveMetrics();
    $("#contextBudgetValue").textContent = "计算中";
    $("#memoryStatusValue").textContent = "待检查";
    setAgentStatuses({
      rag: "准备检索",
      tool: "决策中",
      safety: "评估中",
    });
    syncRunState("决策中", 0);
    const pendingMessage = addMessage("meta", "正在生成回应...");

    try {
      let data;
      let responseAlreadyRendered = false;
      data = await streamAgentChat(message, pendingMessage);
      responseAlreadyRendered = true;
      if (pendingMessage.isConnected) pendingMessage.remove();
      if (data.sessionId) {
        state.currentSessionId = data.sessionId;
      }
      if (!responseAlreadyRendered) {
        addMessage("assistant", data.reply || data);
      }
      renderRagTrace(data.ragTrace || null);
      updateAgentStateFromResponse(data);
      if (!responseAlreadyRendered && data.usedTool && data.toolName) {
        addMessage("meta", `已调用工具：${data.toolName}`);
      }
      if (data.crisis || (data.resources || []).length) {
        addCrisisCard(data.resources || [], Boolean(data.crisis));
      }
      await loadConversations();
    } catch (error) {
      pendingMessage.remove();
      addMessage("meta", error.message);
      setAgentStatuses({ rag: "失败", tool: "异常", safety: "需检查" });
      syncRunState("异常", 0);
    } finally {
      state.activeStream = false;
      sendButton.disabled = false;
      input.focus();
    }
  });
}

async function streamAgentChat(message, pendingMessage) {
  const response = await fetchAgentStream(
    "/agent/chat/stream",
    { message, sessionId: state.currentSessionId },
    true,
  );
  const assistantMessage = addMessage("assistant streaming", "");
  let finalData = null;
  let streamError = null;
  let receivedToken = false;

  await consumeSse(response, (eventName, payload) => {
    switch (eventName) {
      case "meta":
        if (payload.sessionId) {
          state.currentSessionId = payload.sessionId;
          setAgentStatuses({ session: `#${payload.sessionId}` });
        }
        appendExecutionEvent(`连接 ${payload.runtime || "agent"} 运行时`);
        break;
      case "status":
        appendExecutionEvent(
          payload.label || payload.node || "执行图节点",
          "",
          payload.node,
        );
        applyNodeStatus(payload.node);
        break;
      case "plan": {
        const tools = payload.tools || [];
        const routeNames = { chat: "陪伴对话", knowledge: "知识问答", memory: "记忆查询", dashboard: "报告查询", strategy: "行动建议", clarify: "澄清需求" };
        const routing = payload.routing || {};
        $("#runRouteLabel").textContent = routeNames[payload.intent] || "处理中";
        if (routing.provider) {
          const probability = Number.isFinite(routing.probability) ? ` · ${(routing.probability * 100).toFixed(1)}%` : "";
          appendExecutionEvent(`路由模型 ${routing.model || routing.provider}${probability}${routing.fallback ? " · 已回退" : ""}`);
        }
        const label = tools.length ? tools.join(", ") : "无需工具";
        setAgentStatuses({ tool: label, rag: tools.includes("search_knowledge") ? "正在召回" : "未检索" });
        syncRunState(label, 0);
        appendExecutionEvent(`规划：${payload.intent || "chat"} · ${label}`);
        break;
      }
      case "tool": {
        const tools = payload.tools || [];
        const citations = Number(payload.citationCount || 0);
        const label = tools.join(", ") || "执行完成";
        setAgentStatuses({
          tool: label,
          rag: citations > 0 ? `${citations} 条候选` : "无有效引用",
        });
        syncRunState(label, citations);
        appendExecutionEvent(`MCP 完成：${label}`);
        renderLiveRetrieval(payload.retrieval || {});
        break;
      }
      case "context": {
        const estimated = Number(payload.estimatedTokens || 0);
        const maximum = Number(payload.maxTokens || 0);
        $("#contextBudgetValue").textContent = `${estimated} / ${maximum}`;
        appendExecutionEvent(`上下文预算 ${estimated}/${maximum}，裁剪 ${payload.droppedMessages || 0} 条`);
        break;
      }
      case "evidence":
        if (payload.limited) appendExecutionEvent("本轮证据有限，将在回复中说明", "warning");
        break;
      case "decision": {
        const supportNames = { ordinary: "普通聊天", self_help: "心理支持", professional_support: "专业资源支持", crisis: "危机支持" };
        $("#runRouteLabel").textContent = supportNames[payload.supportLevel] || "心理支持";
        appendExecutionEvent(`支持路径：${supportNames[payload.supportLevel] || "心理支持"}${payload.fallback ? " · 判断不可用，使用谨慎路径" : ""}`);
        break;
      }
      case "tool_decision":
        appendExecutionEvent(payload.action === "finish" ? "所需信息已收集" : payload.action === "clarify" ? "需要补充信息" : "正在获取支持信息");
        break;
      case "memory":
        $("#memoryStatusValue").textContent = payload.status === "pending" ? "后台更新中" : "已检查";
        appendExecutionEvent(payload.label || "记忆状态已更新");
        break;
      case "warning":
        appendExecutionEvent(payload.message || "运行时警告", "warning");
        break;
      case "node_metric":
        updateExecutionNodeMetric(payload);
        break;
      case "metrics":
        renderLiveMetrics(payload);
        break;
      case "token":
        if (pendingMessage.isConnected) pendingMessage.remove();
        receivedToken = true;
        assistantMessage.textContent += payload.text || "";
        $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
        break;
      case "done":
        finalData = payload;
        if (!assistantMessage.textContent && payload.reply) {
          assistantMessage.textContent = payload.reply;
        }
        assistantMessage.classList.remove("streaming");
        appendExecutionEvent("本轮图执行完成");
        renderLiveMetrics(payload.metrics || null);
        loadMetrics({ silent: true });
        break;
      case "error":
        streamError = new Error(payload.message || "Agent 流式执行失败");
        appendExecutionEvent(streamError.message, "warning");
        break;
      default:
        break;
    }
  });

  if (pendingMessage.isConnected) pendingMessage.remove();
  assistantMessage.classList.remove("streaming");
  if (streamError) {
    if (!receivedToken) {
      assistantMessage.remove();
    }
    throw streamError;
  }
  if (!finalData) {
    if (!receivedToken) {
      assistantMessage.remove();
    }
    throw new Error("流式响应未正常结束");
  }
  return finalData;
}

async function fetchAgentStream(path, payload, allowFallback) {
  try {
    const response = await fetch(`${state.apiBaseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: `Bearer ${state.token}`,
      },
      body: JSON.stringify(payload),
    });
    if (response.status === 401) {
      clearSession(false);
    }
    if (!response.ok) {
      const text = await response.text();
      const data = text ? parseJson(text) : null;
      throw new Error(data?.message || text || `请求失败：${response.status}`);
    }
    if (!response.body) {
      throw new Error("浏览器不支持流式响应");
    }
    state.backendOnline = true;
    syncConnectionLabel();
    return response;
  } catch (error) {
    const fallbackUrl = getLoopbackFallbackUrl(state.apiBaseUrl);
    if (error instanceof TypeError && allowFallback && fallbackUrl) {
      state.apiBaseUrl = fallbackUrl;
      localStorage.setItem("psychAgentApiBaseUrl", state.apiBaseUrl);
      hydrateSettings();
      return fetchAgentStream(path, payload, false);
    }
    if (error instanceof TypeError) {
      state.backendOnline = false;
      syncConnectionLabel();
      throw new Error(`无法连接后端：${state.apiBaseUrl}`);
    }
    throw error;
  }
}

async function consumeSse(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      dispatchSseBlock(block, onEvent);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) {
    dispatchSseBlock(buffer, onEvent);
  }
}

function dispatchSseBlock(block, onEvent) {
  let eventName = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) return;
  const raw = dataLines.join("\n");
  onEvent(eventName, parseJson(raw));
}

function applyNodeStatus(node) {
  if (node === "safety_guard") {
    setAgentStatuses({ safety: "扫描中" });
  } else if (node === "execute_tools") {
    setAgentStatuses({ tool: "MCP 执行中" });
  } else if (node === "persist_turn") {
    setAgentStatuses({ session: state.currentSessionId ? `#${state.currentSessionId} · 保存中` : "保存中" });
  } else if (node === "fallback") {
    setAgentStatuses({ tool: "Java 降级" });
  }
}

function resetExecutionTimeline() {
  const timeline = $("#executionTimeline");
  if (timeline) {
    timeline.innerHTML = `<div class="timeline-empty">发送消息后显示图节点</div>`;
  }
}

function appendExecutionEvent(label, tone = "", node = "") {
  const timeline = $("#executionTimeline");
  if (!timeline) return;
  timeline.querySelector(".timeline-empty")?.remove();
  const item = document.createElement("div");
  item.className = `timeline-item ${tone}`.trim();
  if (node) item.dataset.node = node;
  const text = document.createElement("span");
  text.textContent = label;
  item.appendChild(text);
  timeline.appendChild(item);
  while (timeline.children.length > 10) {
    timeline.firstElementChild.remove();
  }
  timeline.scrollTop = timeline.scrollHeight;
}

function updateExecutionNodeMetric(payload) {
  const timeline = $("#executionTimeline");
  const node = payload.node || "";
  const duration = formatDuration(payload.durationMs);
  const candidates = Array.from(timeline.querySelectorAll(".timeline-item"));
  const item = candidates.reverse().find((candidate) => candidate.dataset.node === node);
  if (!item) {
    appendExecutionEvent(`${node || "节点"} · ${duration}`);
    return;
  }
  let badge = item.querySelector(".timeline-duration");
  if (!badge) {
    badge = document.createElement("b");
    badge.className = "timeline-duration";
    item.appendChild(badge);
  }
  badge.textContent = duration;
}

function bindRoleCards() {
  $("#refreshRoleCardsBtn").addEventListener("click", loadRoleCards);
  $("#roleCardForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/role-cards", {
        method: "POST",
        body: JSON.stringify({
          name: $("#roleCardName").value.trim(),
          description: $("#roleCardDescription").value.trim(),
          tone: $("#roleCardTone").value,
          responseStyle: $("#roleCardResponseStyle").value,
          customInstructions: $("#roleCardInstructions").value.trim(),
          forbiddenExpressions: $("#roleCardForbidden").value.trim(),
        }),
      });
      event.target.reset();
      await loadRoleCards();
      toast("角色卡已创建");
    } catch (error) {
      toast(error.message);
    }
  });
}

async function loadRoleCards() {
  if (!state.token) return;
  try {
    const cards = await api("/role-cards");
    $("#roleCardList").innerHTML = cards.map((card) => `
      <article class="role-card-item ${card.active ? "active" : ""}">
        <h3>${escapeHtml(card.name)}</h3>
        <p>${escapeHtml(card.description)}</p>
        <p><span class="tag">${escapeHtml(card.tone)}</span><span class="tag">${escapeHtml(card.responseStyle)}</span></p>
        <button data-role-card-id="${card.id}" type="button" ${card.active ? "disabled" : ""}>${card.active ? "当前使用" : "启用"}</button>
      </article>
    `).join("");
    $$('[data-role-card-id]').forEach((button) => {
      button.addEventListener("click", () => activateRoleCard(Number(button.dataset.roleCardId)));
    });
  } catch (error) {
    $("#roleCardList").innerHTML = empty(error.message);
  }
}

async function activateRoleCard(roleCardId) {
  try {
    await api(`/role-cards/${roleCardId}/activate`, { method: "POST" });
    await loadRoleCards();
    toast("角色卡已启用");
  } catch (error) {
    toast(error.message);
  }
}

function startNewConversation() {
  state.currentSessionId = null;
  $("#chatLog").innerHTML = "";
  addMessage("assistant", "新的对话已开始。");
  renderRagTrace(null);
  setAgentStatuses({ rag: "待检索", tool: "空闲", safety: "常规", session: "新对话" });
  resetExecutionTimeline();
  $("#contextBudgetValue").textContent = "—";
  $("#memoryStatusValue").textContent = "待检查";
  resetLiveMetrics();
  syncRunState();
  renderConversationList();
}

function addMessage(role, content) {
  const item = document.createElement("div");
  item.className = `message ${role}`;
  item.textContent = content;
  $("#chatLog").appendChild(item);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  return item;
}

function addCrisisCard(resources, crisis = true) {
  const card = document.createElement("div");
  card.className = "message crisis-card";
  card.innerHTML = `
    <strong>${crisis ? "请优先确保当前安全" : "可联系的专业支持资源"}</strong>
    <div class="crisis-actions">
      ${resources.map((resource) => `<a class="tag ${resource.urgent ? "high" : ""}" href="tel:${escapeHtml(resource.contact)}">${escapeHtml(resource.name)} ${escapeHtml(resource.contact)}</a>`).join("")}
    </div>
  `;
  $("#chatLog").appendChild(card);
}

function updateAgentStateFromResponse(data) {
  const ragTrace = data.ragTrace || null;
  const citationCount = ragTrace?.citationCount || ragTrace?.citations?.length || 0;
  setAgentStatuses({
    rag: ragTrace ? `${citationCount} 条引用` : "未检索",
    tool: data.usedTool ? data.toolName || "已调用" : "未调用",
    safety: data.crisis ? "危机响应" : "常规",
    session: state.currentSessionId ? `#${state.currentSessionId}` : "临时",
  });
  $("#memoryStatusValue").textContent = $("#memoryStatusValue").textContent === "待检查"
    ? "本轮已保存"
    : $("#memoryStatusValue").textContent;
  syncRunState(data.usedTool ? data.toolName || "已调用" : "未调用", citationCount);
  renderLiveMetrics(data.metrics || null);
}

function setAgentStatuses(values = {}) {
  if (values.rag !== undefined) $("#ragStatusValue").textContent = values.rag;
  if (values.tool !== undefined) $("#toolStatusValue").textContent = values.tool;
  if (values.safety !== undefined) $("#safetyStatusValue").textContent = values.safety;
  if (values.session !== undefined) $("#sessionStatusValue").textContent = values.session;
}

function syncRunState(toolLabel = null, citationCount = null) {
  $("#runModeLabel").textContent = "Agent";
  if (toolLabel !== null) {
    $("#runToolLabel").textContent = toolLabel;
  }
  if (citationCount !== null) {
    $("#runCitationLabel").textContent = String(citationCount);
  }
}

function resetLiveMetrics() {
  $("#runRouteLabel").textContent = "待选择";
  state.currentMetrics = null;
  $("#runDurationValue").textContent = "—";
  $("#runTtftValue").textContent = "—";
  $("#runTokenValue").textContent = "—";
  $("#runCostValue").textContent = "—";
  $("#runRagLatencyValue").textContent = "—";
}

function renderLiveMetrics(metrics) {
  if (!metrics) return;
  state.currentMetrics = metrics;
  $("#runDurationValue").textContent = formatDuration(metrics.totalDurationMs);
  $("#runTtftValue").textContent = formatDuration(metrics.ttftMs);
  $("#runTokenValue").textContent = Number(metrics.totalTokens || 0).toLocaleString("zh-CN");
  $("#runCostValue").textContent = formatCost(
    metrics.actualCostUsd,
    metrics.estimatedCostUsd,
    metrics.costMode,
  );
  renderLiveRetrieval(metrics.retrieval || {});
}

function renderLiveRetrieval(retrieval) {
  if (!retrieval || retrieval.durationMs === undefined) return;
  $("#runRagLatencyValue").textContent = `${formatDuration(retrieval.durationMs)} · ${retrieval.cacheHit ? "hit" : "miss"}`;
}

function renderRagTrace(trace) {
  state.currentRagTrace = trace;
  const panel = $("#ragTracePanel");
  if (!trace) {
    panel.innerHTML = `<div class="trace-empty">本轮未调用知识检索</div>`;
    $("#runCitationLabel").textContent = "0";
    return;
  }
  const citations = trace.citations || [];
  const retrieval = trace.retrievalMetrics || {};
  $("#runCitationLabel").textContent = String(trace.citationCount ?? citations.length);
  panel.innerHTML = `
    <div class="trace-summary">
      <span class="tag">${escapeHtml(trace.tool || "rag")}</span>
      <span class="tag">${escapeHtml(trace.status || "completed")}</span>
      ${retrieval.durationMs !== undefined ? `<span class="tag">${escapeHtml(formatDuration(retrieval.durationMs))}</span>` : ""}
      ${retrieval.cacheHit !== undefined ? `<span class="tag">${retrieval.cacheHit ? "cache hit" : "cache miss"}</span>` : ""}
      ${retrieval.fusedCandidates !== undefined ? `<span class="tag">${escapeHtml(retrieval.fusedCandidates)} candidates</span>` : ""}
    </div>
    <div class="trace-query">${escapeHtml(trace.query || "未记录查询")}</div>
    ${citations.length ? citations.map(ragCitationItem).join("") : `<div class="trace-empty">没有返回可引用片段</div>`}
  `;
}

function ragCitationItem(item) {
  const score = item.relevanceScore === undefined || item.relevanceScore === null
    ? "n/a"
    : Number(item.relevanceScore).toFixed(2);
  const confidence = item.confidenceLabel || "unknown";
  return `
    <article class="trace-citation">
      <header>
        <strong>#${escapeHtml(item.rank || "")}</strong>
        <span class="tag ${escapeHtml(confidence)}">${escapeHtml(confidence)}</span>
      </header>
      <p>${escapeHtml(item.excerpt || "")}</p>
      <footer>
        <span>${escapeHtml(item.source || "unknown")}</span>
        <span>${escapeHtml(item.category || "default")}</span>
        <span>${escapeHtml(score)}</span>
      </footer>
    </article>
  `;
}

function bindProfile() {
  $("#refreshProfileBtn").addEventListener("click", refreshProfile);
  $("#profileUserSelect").addEventListener("change", () => {
    const memory = state.adminMemories.find((item) => item.username === $("#profileUserSelect").value);
    if (memory) {
      fillAdminMemory(memory);
    }
  });
  $("#profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#profileUserSelect").value;
    if (!username) {
      toast("请选择用户");
      return;
    }
    try {
      const data = await api(`/profile/${encodeURIComponent(username)}`, {
        method: "PUT",
        body: JSON.stringify(readProfileForm()),
      });
      state.profiles = state.profiles.map((profile) => profile.username === data.username ? data : profile);
      state.adminMemories = state.adminMemories.map((memory) =>
        memory.username === data.username ? { ...memory, profile: data } : memory
      );
      fillProfile(data);
      toast("画像已保存");
    } catch (error) {
      toast(error.message);
    }
  });
}

async function refreshProfile() {
  if (!isAdmin()) return;
  try {
    state.adminMemories = await api("/admin/memories");
    state.profiles = state.adminMemories.map((memory) => memory.profile);
    renderProfileUsers();
  } catch (error) {
    toast(error.message);
  }
}

function readProfileForm() {
  return {
    profileSummary: $("#profileSummary").value,
    concerns: $("#concerns").value,
    preferences: $("#preferences").value,
    copingStrategies: $("#copingStrategies").value,
    riskSignals: $("#riskSignals").value,
    supportGoals: $("#supportGoals").value,
  };
}

function fillProfile(profile) {
  if (profile.username) {
    $("#profileUserSelect").value = profile.username;
  }
  $("#profileSummary").value = profile.profileSummary || "";
  $("#concerns").value = profile.concerns || "";
  $("#preferences").value = profile.preferences || "";
  $("#copingStrategies").value = profile.copingStrategies || "";
  $("#riskSignals").value = profile.riskSignals || "";
  $("#supportGoals").value = profile.supportGoals || "";
}

function fillAdminMemory(memory) {
  fillProfile(memory.profile || {});
  $("#memorySummary").value = memory.summary || "";
  $("#longTermMemory").value = memory.longTermMemory || "";
  renderAdminSessions(memory);
  $("#adminMessageList").innerHTML = empty("选择一个会话查看消息");
}

function renderProfileUsers() {
  $("#profileUserSelect").innerHTML = state.adminMemories
    .map((memory) => `<option value="${escapeHtml(memory.username)}">${escapeHtml(memory.username)}</option>`)
    .join("");
  if (state.adminMemories.length > 0) {
    fillAdminMemory(state.adminMemories[0]);
  }
}

function renderAdminSessions(memory) {
  const sessions = memory.sessions || [];
  $("#adminSessionList").innerHTML = sessions.length
    ? sessions.map((session) => `
      <button class="conversation-item" data-admin-session-id="${session.id}" data-admin-username="${escapeHtml(memory.username)}" type="button">
        ${escapeHtml(session.title || "未命名对话")}
        <span>${escapeHtml(formatTime(session.updatedAt))}</span>
      </button>
      <div class="list-item"><p>${escapeHtml(session.summary || "暂无会话摘要")}</p></div>
    `).join("")
    : empty("暂无会话");
  $$("[data-admin-session-id]").forEach((button) => {
    button.addEventListener("click", () => loadAdminMessages(button.dataset.adminUsername, Number(button.dataset.adminSessionId)));
  });
}

async function loadAdminMessages(username, sessionId) {
  try {
    const messages = await api(`/admin/memories/${encodeURIComponent(username)}/sessions/${sessionId}/messages`);
    $("#adminMessageList").innerHTML = messages.length
      ? messages.map((message) => `
        <article class="list-item">
          <h3>${escapeHtml(message.role === "assistant" ? "助手" : "用户")}</h3>
          <p>${escapeHtml(formatTime(message.createdAt))}</p>
          <p>${escapeHtml(message.content || "")}</p>
        </article>
      `).join("")
      : empty("暂无消息");
  } catch (error) {
    toast(error.message);
  }
}

async function loadConversations() {
  if (!state.token) return;
  try {
    state.conversations = await api("/conversations");
    renderConversationList();
  } catch (error) {
    toast(error.message);
  }
}

function renderConversationList() {
  const conversations = state.conversations || [];
  $("#conversationList").innerHTML = conversations.length
    ? conversations.map((session) => `
      <button class="conversation-item ${session.id === state.currentSessionId ? "active" : ""}" data-session-id="${session.id}" type="button">
        ${escapeHtml(session.title || "未命名对话")}
        <span>${escapeHtml(formatTime(session.updatedAt))}</span>
      </button>
    `).join("")
    : `<div class="list-item"><p>暂无历史</p></div>`;
  $$(".conversation-item").forEach((button) => {
    button.addEventListener("click", () => continueConversation(Number(button.dataset.sessionId)));
  });
}

async function continueConversation(sessionId) {
  try {
    const messages = await api(`/conversations/${sessionId}/messages`);
    state.currentSessionId = sessionId;
    $("#chatLog").innerHTML = "";
    messages.forEach((message) => addMessage(message.role === "assistant" ? "assistant" : "user", message.content));
    renderRagTrace(null);
    setAgentStatuses({ rag: "历史会话", tool: "未调用", safety: "常规", session: `#${sessionId}` });
    resetExecutionTimeline();
    $("#contextBudgetValue").textContent = "历史";
    $("#memoryStatusValue").textContent = "已加载";
    syncRunState("未调用", 0);
    renderConversationList();
  } catch (error) {
    toast(error.message);
  }
}

function bindReports() {
  $("#refreshReportsBtn").addEventListener("click", refreshReports);
}

function bindHelp() {
  $("#refreshHelpBtn").addEventListener("click", loadHelpResources);
}

async function loadHelpResources() {
  try {
    const resources = await api("/help/resources", { auth: false });
    $("#helpResources").innerHTML = resources.map((resource) => `
      <article class="help-resource ${resource.urgent ? "urgent" : ""}">
        <h3>${escapeHtml(resource.name)}</h3>
        <span class="help-contact">${escapeHtml(resource.contact)}</span>
        <p>${escapeHtml(resource.description)}</p>
      </article>
    `).join("");
  } catch (error) {
    $("#helpResources").innerHTML = empty(error.message);
  }
}

async function refreshReports() {
  try {
    const dashboard = await api("/reports/dashboard?recentLimit=10&topRiskLimit=5");
    const reports = dashboard.reportSummaryResponse || [];
    if (isAdmin()) {
      $("#reportsSubtitle").textContent = "管理员查看全局风险概览、趋势和最近分析记录。";
      $("#userReportSummary").classList.add("hidden");
      const highCount = reports.filter((item) => item.risk === "high").length;
      $("#dashboardGrid").innerHTML = [
        metric("最近报告", reports.length),
        metric("高风险", highCount),
        metric("风险用户", dashboard.topRiskUserResponse?.length || 0),
      ].join("");
      renderRiskDistribution(dashboard.riskDistribution || []);
      renderEmotionTrend(dashboard.emotionTrend || []);
    } else {
      $("#reportsSubtitle").textContent = "查看自己的心理状态总结和近期记录。";
      $("#userReportSummary").classList.remove("hidden");
      renderUserReportSummary(reports);
      $("#dashboardGrid").innerHTML = [
        metric("记录数", reports.length),
        metric("主要情绪", dominantValue(reports, "emotion") || "暂无"),
        metric("最近风险", reports[0]?.risk || "暂无"),
      ].join("");
      $("#riskDistribution").innerHTML = "";
      $("#emotionTrend").innerHTML = "";
    }
    $("#reportsList").innerHTML = reports.length
      ? reports.map(reportItem).join("")
      : empty("暂无报告");
  } catch (error) {
    toast(error.message);
  }
}

function renderUserReportSummary(reports) {
  const latest = reports[0];
  if (!latest) {
    $("#userReportSummary").innerHTML = `
      <article class="summary-panel">
        <h3>暂无心理总结</h3>
        <p>开始对话后，系统会根据你的最近消息生成个人心理状态记录。</p>
      </article>
    `;
    return;
  }
  const dominantEmotion = dominantValue(reports, "emotion") || latest.emotion || "暂无";
  const highCount = reports.filter((item) => item.risk === "high").length;
  const mediumCount = reports.filter((item) => item.risk === "medium").length;
  const averageConfidence = reports.length
    ? reports.reduce((sum, item) => sum + Number(item.confidence || 0), 0) / reports.length
    : 0;
  const tone = highCount > 0
    ? "近期有较强风险信号，建议优先联系可信任的人或专业支持。"
    : mediumCount > 0
      ? "近期压力或波动比较明显，可以把目标拆小，先处理最容易落地的一步。"
      : "近期整体风险较低，可以继续保持稳定的自我观察和支持性对话。";
  $("#userReportSummary").innerHTML = `
    <article class="summary-panel">
      <div>
        <span class="panel-title">个人心理总结</span>
        <h3>${escapeHtml(dominantEmotion)}</h3>
      </div>
      <p>${escapeHtml(tone)}</p>
      <div class="summary-grid">
        <span><strong>${escapeHtml(latest.risk || "暂无")}</strong><em>最近风险</em></span>
        <span><strong>${escapeHtml(formatConfidence(averageConfidence))}</strong><em>平均置信度</em></span>
        <span><strong>${escapeHtml(formatTime(latest.createdAt))}</strong><em>最近记录</em></span>
      </div>
    </article>
  `;
}

function dominantValue(items, key) {
  const counts = new Map();
  for (const item of items) {
    const value = item?.[key];
    if (!value) continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] || "";
}

function formatConfidence(value) {
  if (!Number.isFinite(value) || value <= 0) {
    return "暂无";
  }
  return `${Math.round(value * 100)}%`;
}

function renderRiskDistribution(items) {
  const max = Math.max(1, ...items.map((item) => item.count));
  $("#riskDistribution").innerHTML = items.length
    ? items.map((item) => `
      <div class="bar-row">
        <span class="tag ${escapeHtml(item.risk)}">${escapeHtml(item.risk)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.round(item.count / max * 100)}%"></div></div>
        <strong>${item.count}</strong>
      </div>
    `).join("")
    : empty("暂无风险数据");
}

function renderEmotionTrend(items) {
  $("#emotionTrend").innerHTML = items.length
    ? items.map((item) => `
      <div class="trend-point">
        <span>${escapeHtml(item.date)}</span>
        <span>${escapeHtml(item.emotion)}</span>
        <span class="tag">${item.count} 次</span>
      </div>
    `).join("")
    : empty("暂无趋势数据");
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function reportItem(item) {
  return `
    <article class="list-item">
      <h3>${escapeHtml(item.emotion || "未知情绪")}</h3>
      <p>
        <span class="tag ${escapeHtml(item.risk || "")}">${escapeHtml(item.risk || "unknown")}</span>
        <span class="tag">${escapeHtml(formatTime(item.createdAt))}</span>
      </p>
      <p>${escapeHtml(item.message || "")}</p>
    </article>
  `;
}

function bindMetrics() {
  $("#refreshMetricsBtn").addEventListener("click", () => loadMetrics());
  $("#metricsWindow").addEventListener("change", () => loadMetrics());
  $("#runRagEvaluationBtn").addEventListener("click", runRagEvaluation);
}

async function loadMetrics({ silent = false } = {}) {
  if (!state.token) return;
  const days = Number($("#metricsWindow").value || 7);
  try {
    const [summary, recent] = await Promise.all([
      api(`/agent/metrics/summary?days=${days}`),
      api("/agent/metrics/recent?limit=30"),
    ]);
    renderMetricsOverview(summary);
    renderRunTrend(summary.trend || []);
    renderRuntimeQuality(summary);
    renderRecentRuns(recent || []);
    if (isAdmin()) {
      try {
        renderRagEvaluation(await api("/admin/agent-metrics/rag-evaluation/latest"));
      } catch (error) {
        $("#ragEvaluationMetrics").innerHTML = empty(error.message);
      }
    }
  } catch (error) {
    if (!silent) toast(error.message);
    $("#metricsOverview").innerHTML = empty(error.message);
  }
}

function renderMetricsOverview(summary) {
  $("#metricsOverview").innerHTML = [
    metricDetail("运行次数", summary.totalRuns || 0, `${summary.windowDays || 7} 天窗口`),
    metricDetail("平均总耗时", formatDuration(summary.avgDurationMs), `P50 ${formatDuration(summary.p50DurationMs)}`),
    metricDetail("P95 总耗时", formatDuration(summary.p95DurationMs), `P99 ${formatDuration(summary.p99DurationMs)}`),
    metricDetail("平均首 Token", formatDuration(summary.avgTtftMs), "从请求到首个流式 token"),
    metricDetail("累计 Token", Number(summary.totalTokens || 0).toLocaleString("zh-CN"), `${Number(summary.totalPromptTokens || 0).toLocaleString("zh-CN")} 输入 · ${Number(summary.totalCompletionTokens || 0).toLocaleString("zh-CN")} 输出`),
    metricDetail("实际成本", `$${Number(summary.actualCostUsd || 0).toFixed(6)}`, `等价估算 $${Number(summary.estimatedCostUsd || 0).toFixed(6)}`),
  ].join("");
}

function metricDetail(label, value, detail) {
  return `
    <article class="metric metric-detail">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
  `;
}

function renderRunTrend(points) {
  const panel = $("#runTrendChart");
  if (!points.length) {
    panel.innerHTML = `<div class="chart-empty">完成一次 Agent 对话后生成趋势</div>`;
    return;
  }
  const maxDuration = Math.max(1, ...points.map((point) => Number(point.avgDurationMs || 0)));
  const maxRuns = Math.max(1, ...points.map((point) => Number(point.runCount || 0)));
  panel.innerHTML = points.map((point) => `
    <div class="run-trend-column" title="${escapeHtml(point.date)} · ${escapeHtml(formatDuration(point.avgDurationMs))}">
      <div class="run-volume" style="height:${Math.max(5, Number(point.runCount || 0) / maxRuns * 34)}%"></div>
      <div class="run-latency" style="height:${Math.max(8, Number(point.avgDurationMs || 0) / maxDuration * 100)}%"></div>
      <span>${escapeHtml(String(point.date).slice(5))}</span>
      <b>${escapeHtml(point.runCount || 0)}</b>
    </div>
  `).join("");
}

function renderRuntimeQuality(summary) {
  const items = [
    ["成功率", summary.successRate || 0, "success"],
    ["无降级率", 1 - Number(summary.fallbackRate || 0), "stable"],
    ["RAG 缓存命中", summary.ragCacheHitRate || 0, "cache"],
  ];
  $("#runtimeQuality").innerHTML = items.map(([label, rawValue, tone]) => {
    const value = Math.max(0, Math.min(1, Number(rawValue || 0)));
    return `
      <div class="quality-item ${tone}">
        <div><span>${escapeHtml(label)}</span><strong>${Math.round(value * 100)}%</strong></div>
        <div class="quality-track"><i style="width:${Math.round(value * 100)}%"></i></div>
      </div>
    `;
  }).join("") + `
    <div class="quality-note">
      <span>平均 RAG 延迟</span>
      <strong>${escapeHtml(formatDuration(summary.avgRagDurationMs))}</strong>
    </div>
  `;
}

function renderRecentRuns(rows) {
  const panel = $("#recentRuns");
  if (!rows.length) {
    panel.innerHTML = `<div class="chart-empty">暂无运行记录</div>`;
    return;
  }
  panel.innerHTML = `
    <div class="run-table run-table-head">
      <span>时间 / 模型</span><span>总耗时</span><span>TTFT</span><span>Token</span><span>RAG</span><span>状态</span>
    </div>
    ${rows.map((row) => `
      <article class="run-table">
        <span><strong>${escapeHtml(formatTime(row.startedAt))}</strong><small>${escapeHtml(row.model || "unknown")}</small></span>
        <span>${escapeHtml(formatDuration(row.totalDurationMs))}</span>
        <span>${escapeHtml(formatDuration(row.ttftMs))}</span>
        <span>${Number(row.totalTokens || 0).toLocaleString("zh-CN")}<small>${escapeHtml(Number(row.tokensPerSecond || 0).toFixed(1))} tok/s</small></span>
        <span>${row.retrieval?.durationMs !== undefined ? escapeHtml(formatDuration(row.retrieval.durationMs)) : "—"}<small>${row.retrieval?.cacheHit ? "cache hit" : row.retrieval?.durationMs !== undefined ? "cache miss" : "no retrieval"}</small></span>
        <span><i class="run-status ${escapeHtml(row.status || "unknown")}"></i>${escapeHtml(row.status || "unknown")}</span>
      </article>
    `).join("")}
  `;
}

async function runRagEvaluation() {
  if (!isAdmin()) return;
  const button = $("#runRagEvaluationBtn");
  button.disabled = true;
  button.textContent = "评测中…";
  $("#ragEvaluationMetrics").innerHTML = `<div class="chart-empty">正在逐条执行 20 个标注查询</div>`;
  try {
    const result = await api("/admin/agent-metrics/rag-evaluation", {
      method: "POST",
      body: "{}",
    });
    renderRagEvaluation(result);
    toast("RAG 离线评测完成");
  } catch (error) {
    $("#ragEvaluationMetrics").innerHTML = empty(error.message);
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "运行评测";
  }
}

function renderRagEvaluation(result) {
  if (!result || !result.sampleCount) {
    $("#ragEvaluationMetrics").innerHTML = `<div class="chart-empty">尚未运行离线评测</div>`;
    $("#ragEvaluationCases").innerHTML = "";
    return;
  }
  const metrics = [
    ["Recall@1", result.recallAt1],
    ["Recall@3", result.recallAt3],
    ["Recall@5", result.recallAt5],
    ["Precision@5", result.precisionAt5],
    ["MRR", result.mrr],
    ["nDCG@5", result.nDcgAt5],
  ];
  $("#ragEvaluationMetrics").innerHTML = metrics.map(([label, value]) => `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${Math.round(Number(value || 0) * 100)}%</strong>
      <i style="--score:${Math.round(Number(value || 0) * 100)}%"></i>
    </article>
  `).join("") + `
    <article>
      <span>P95 冷检索耗时</span>
      <strong>${escapeHtml(formatDuration(result.p95LatencyMs))}</strong>
      <small>${escapeHtml(result.sampleCount)} 条 · ${escapeHtml(result.datasetVersion)}</small>
    </article>
  `;
  const failedCases = (result.cases || []).filter((item) => !item.hitAt5);
  $("#ragEvaluationCases").innerHTML = `
    <span>评测于 ${escapeHtml(formatTime(result.evaluatedAt))}</span>
    <span>${failedCases.length ? `${failedCases.length} 条未命中：${failedCases.map((item) => item.id).join(", ")}` : "全部查询在 Top-5 命中"}</span>
  `;
}

function bindKnowledge() {
  $("#refreshKnowledgeBtn").addEventListener("click", loadKnowledge);
  $("#searchKnowledgeBtn").addEventListener("click", searchKnowledge);
  $("#reindexKnowledgeBtn").addEventListener("click", reindexKnowledgeSource);
  $("#knowledgeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      category: $("#knowledgeCategory").value.trim(),
      source: $("#knowledgeSource").value.trim(),
      content: $("#knowledgeContent").value.trim(),
    };
    if (!payload.content) {
      toast("请输入知识内容");
      return;
    }
    try {
      await api("/knowledge/add", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("#knowledgeContent").value = "";
      toast("知识已添加");
      await loadKnowledge();
    } catch (error) {
      toast(error.message);
    }
  });
  $("#knowledgeImportForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      category: $("#importCategory").value.trim(),
      source: $("#importSource").value.trim(),
      content: $("#importContent").value.trim(),
      chunkSize: Number($("#importChunkSize").value || 400),
      overlap: Number($("#importOverlap").value || 80),
    };
    if (!payload.content) {
      toast("请输入长文内容");
      return;
    }
    try {
      await api("/knowledge/import", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("#importContent").value = "";
      toast("长文已导入并写入向量库");
      await loadKnowledge();
    } catch (error) {
      toast(error.message);
    }
  });
}

async function loadKnowledge() {
  if (!isAdmin()) return;
  try {
    const data = await api("/knowledge/all");
    renderKnowledge(data || []);
  } catch (error) {
    toast(error.message);
  }
}

async function searchKnowledge() {
  if (!isAdmin()) return;
  const query = $("#knowledgeQuery").value.trim();
  if (!query) {
    await loadKnowledge();
    return;
  }
  try {
    const params = new URLSearchParams({
      query,
      limit: $("#knowledgeLimit").value || "6",
    });
    const category = $("#knowledgeSearchCategory").value.trim();
    if (category) {
      params.set("category", category);
    }
    const data = await api(`/knowledge/search?${params.toString()}`);
    renderKnowledge(data || []);
  } catch (error) {
    toast(error.message);
  }
}

async function reindexKnowledgeSource() {
  if (!isAdmin()) return;
  const source = $("#knowledgeReindexSource").value.trim()
    || $("#knowledgeSource").value.trim()
    || $("#importSource").value.trim();
  if (!source) {
    toast("请输入要重建的来源");
    return;
  }
  try {
    const result = await api(`/knowledge/reindex?source=${encodeURIComponent(source)}`, {
      method: "POST",
    });
    toast(result || "来源索引已重建");
    await loadKnowledge();
  } catch (error) {
    toast(error.message);
  }
}

function renderKnowledge(items) {
  $("#knowledgeResults").innerHTML = items.length
    ? items.map(knowledgeItem).join("")
    : empty("暂无知识");
  bindKnowledgeItemActions();
}

function knowledgeItem(item) {
  const confidence = item.confidenceLabel || "unknown";
  return `
    <article class="list-item">
      <h3>${escapeHtml(item.category || "未分类")}</h3>
      <p>
        ${item.rank ? `<span class="tag">#${escapeHtml(item.rank)}</span>` : ""}
        <span class="tag">${escapeHtml(item.source || "unknown")}</span>
        ${item.relevanceScore !== undefined && item.relevanceScore !== null ? `<span class="tag">相关度 ${escapeHtml(Number(item.relevanceScore).toFixed(2))}</span>` : ""}
        ${item.confidenceLabel ? `<span class="tag ${escapeHtml(confidence)}">${escapeHtml(confidence)}</span>` : ""}
        ${item.matchReason ? `<span class="tag">${escapeHtml(item.matchReason)}</span>` : ""}
      </p>
      <p>${escapeHtml(item.content || "")}</p>
      ${item.id ? `<div class="item-actions"><button data-knowledge-delete="${escapeHtml(item.id)}" type="button">删除</button></div>` : ""}
    </article>
  `;
}

function bindKnowledgeItemActions() {
  $$("[data-knowledge-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.knowledgeDelete;
      if (!id || !window.confirm("确认删除这条知识？")) return;
      try {
        await api(`/knowledge/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("知识已删除");
        await loadKnowledge();
      } catch (error) {
        toast(error.message);
      }
    });
  });
}

function bindSettings() {
  $("#saveConfigBtn").addEventListener("click", () => {
    state.apiBaseUrl = $("#apiBaseUrl").value.trim() || config.apiBaseUrl;
    localStorage.setItem("psychAgentApiBaseUrl", state.apiBaseUrl);
    syncConnectionLabel();
    toast("API 地址已保存");
  });
}

function applyRoleVisibility() {
  const admin = isAdmin();
  $("#appShell").classList.toggle("admin-session", admin);
  $("#appShell").classList.toggle("user-session", Boolean(state.token && state.user && !admin));
  $$(".admin-only").forEach((element) => element.classList.toggle("hidden", !admin));
  syncRunState();
  if (!admin && $(".nav-tab.active")?.classList.contains("admin-only")) {
    $(".nav-tab[data-view='chatView']").click();
  }
}

function isAdmin() {
  return state.user?.role === "ADMIN";
}

function hydrateSettings() {
  $("#apiBaseUrl").value = state.apiBaseUrl;
  $("#mysqlConfig").value = config.localServices?.mysql || "";
  $("#redisConfig").value = config.localServices?.redis || "";
  $("#ollamaConfig").value = config.localServices?.ollama || "";
  $("#chromaConfig").value = config.localServices?.chroma || "";
}

function syncConnectionLabel() {
  const label = state.token && state.user
    ? `${state.user.username} · ${state.apiBaseUrl}`
    : state.apiBaseUrl;
  $("#connectionLabel").textContent = label;
  $("#appConnectionLabel").textContent = label;
  $("#connectionDot").classList.toggle("online", state.backendOnline);
  $("#appConnectionDot").classList.toggle("online", state.backendOnline);
}

function syncAuthUi() {
  const loggedIn = Boolean(state.token && state.user);
  $("#loginScreen").classList.toggle("hidden", loggedIn);
  $("#appShell").classList.toggle("hidden", !loggedIn);
  $("#sessionPanel").classList.toggle("hidden", !loggedIn);
  $("#sessionUsername").textContent = loggedIn ? state.user.username : "";
}

function activateDefaultView() {
  $$(".nav-tab").forEach((tab) => tab.classList.remove("active"));
  $$(".view").forEach((view) => view.classList.remove("active-view"));
  $(".nav-tab[data-view='chatView']").classList.add("active");
  $("#chatView").classList.add("active-view");
}

function empty(text) {
  return `<div class="list-item"><p>${escapeHtml(text)}</p></div>`;
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 10000) return `${(milliseconds / 1000).toFixed(2)} s`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

function formatCost(actual, estimated, mode) {
  const actualValue = Number(actual || 0);
  const estimatedValue = Number(estimated || 0);
  if (mode === "local") {
    return estimatedValue > 0
      ? `$${actualValue.toFixed(4)} · ≈$${estimatedValue.toFixed(4)}`
      : "$0 · local";
  }
  return `$${actualValue.toFixed(6)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("visible"), 2600);
}

async function pingBackend() {
  try {
    const response = await fetch(`${state.apiBaseUrl}/test/ping`, { cache: "no-store" });
    state.backendOnline = response.ok;
  } catch {
    state.backendOnline = false;
  }
  syncConnectionLabel();
}

let goalRecords = [];
function bindGoals() {
  $("#refreshGoalsBtn").addEventListener("click", loadGoals);
  $("#goalForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    try {
      const action = $("#goalAction").value.trim();
      await api("/goals", {method: "POST", body: JSON.stringify({
        title: $("#goalTitle").value.trim(), description: "", status: "active", confirmed: true,
        actions: action ? [{id: crypto.randomUUID(), title: action, status: "planned", feedback: []}] : []
      })});
      event.target.reset(); $("#goalNotice").textContent = "目标已保存，你可以随时调整或删除。";
      await loadGoals();
    } catch (error) { $("#goalNotice").textContent = error.message; }
    finally { button.disabled = false; }
  });
}
async function loadGoals() {
  if (!state.token) { $("#goalList").innerHTML = empty("登录后查看自己的目标"); return; }
  try {
    goalRecords = await api("/goals");
    $("#goalList").innerHTML = goalRecords.length ? goalRecords.map(goal => `
      <article class="list-item goal-card" data-goal="${goal.id}">
        <h3>${escapeHtml(goal.title)}</h3>
        <label>目标状态 <select data-goal-status><option value="active" ${goal.status === "active" ? "selected" : ""}>进行中</option><option value="paused" ${goal.status === "paused" ? "selected" : ""}>暂缓</option><option value="completed" ${goal.status === "completed" ? "selected" : ""}>已完成</option></select></label>
        ${goal.actions.map((action, index) => `<div class="goal-action" data-action-index="${index}">
          <strong>${escapeHtml(action.title)}</strong>
          <select data-action-status><option value="planned" ${action.status === "planned" ? "selected" : ""}>准备尝试</option><option value="done" ${action.status === "done" ? "selected" : ""}>已尝试</option><option value="skipped" ${action.status === "skipped" ? "selected" : ""}>暂不尝试</option></select>
          <p>${action.feedback.map(escapeHtml).join("<br>") || "还没有记录反馈"}</p>
          <input data-feedback maxlength="1000" placeholder="记录自己的实际感受，可留空" />
        </div>`).join("")}
        <input data-new-action maxlength="300" placeholder="添加一个自己愿意尝试的小行动，可留空" />
        <div class="button-row"><button data-goal-save type="button">保存我的调整与反馈</button><button data-goal-delete type="button">删除目标</button></div>
      </article>`).join("") : empty("还没有目标。可以先选择一件想尝试的小事。");
    $$("[data-goal-save]").forEach(button => button.addEventListener("click", () => updateGoal(button)));
    $$("[data-goal-delete]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api(`/goals/${button.closest("[data-goal]").dataset.goal}`, {method: "DELETE"}); await loadGoals(); }
      catch (error) { $("#goalNotice").textContent = error.message; button.disabled = false; }
    }));
  } catch (error) { $("#goalList").innerHTML = empty(error.message); }
}
async function updateGoal(button) {
  const card = button.closest("[data-goal]");
  const goal = goalRecords.find(item => String(item.id) === card.dataset.goal);
  const actions = goal.actions.map((action, index) => {
    const row = card.querySelector(`[data-action-index="${index}"]`);
    const feedback = row.querySelector("[data-feedback]").value.trim();
    return {...action, status: row.querySelector("[data-action-status]").value,
      feedback: feedback ? [...action.feedback, feedback] : action.feedback};
  });
  const added = card.querySelector("[data-new-action]").value.trim();
  if (added) actions.push({id: crypto.randomUUID(), title: added, status: "planned", feedback: []});
  button.disabled = true;
  try {
    await api(`/goals/${goal.id}`, {method: "PUT", body: JSON.stringify({title: goal.title,
      description: goal.description, status: card.querySelector("[data-goal-status]").value,
      version: goal.version, confirmed: true, actions})});
    $("#goalNotice").textContent = "调整已保存。"; await loadGoals();
  } catch (error) { $("#goalNotice").textContent = error.message; button.disabled = false; }
}

init();
