const config = window.PSYCH_AGENT_CONFIG || {};
const state = {
  apiBaseUrl: config.forceApiBaseUrl
    ? config.apiBaseUrl
    : localStorage.getItem("psychAgentApiBaseUrl") || config.apiBaseUrl || "http://localhost:8080",
  token: localStorage.getItem("psychAgentToken") || "",
  user: parseJson(localStorage.getItem("psychAgentUser") || "null"),
  currentSessionId: null,
  profiles: [],
  adminMemories: [],
  backendOnline: false,
  conversations: [],
  currentRagTrace: null,
  currentMetrics: null,
  activeStream: false,
  streamController: null,
  authRevision: 0,
  currentMemoryRequestId: null,
  currentView: "chatView",
  sidebarCollapsed: localStorage.getItem("psychAgentSidebarCollapsed") === "true",
  sidebarMobileOpen: false,
  stickToBottom: true,
  conversationLoadId: 0,
  historyLoading: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function init() {
  normalizeStoredSession();
  refreshWelcomeGreeting();
  bindShell();
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
  syncChatEmpty();
  activateDefaultView();
  pingBackend();
  window.setInterval(pingBackend, 60000);
  if (state.token) {
    loadConversations();
    loadRoleCards();
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
  const revision = state.authRevision;
  try {
    const data = await requestJson(path, { ...fetchOptions, headers }, ["GET", "HEAD"].includes((fetchOptions.method || "GET").toUpperCase()), revision);
    if (auth !== false && revision !== state.authRevision) throw new DOMException("会话已变化", "AbortError");
    return data;
  } catch (error) {
    if (auth !== false && revision !== state.authRevision) throw new DOMException("会话已变化", "AbortError");
    throw error;
  }
}

async function requestJson(path, options, allowFallback, revision) {
  try {
    const response = await fetch(`${state.apiBaseUrl}${path}`, options);
    const text = await response.text();
    const data = text ? parseJson(text) : null;
    if (options.headers?.Authorization && revision !== state.authRevision) throw new DOMException("会话已变化", "AbortError");
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
    if (error.name === "AbortError") throw error;
    const fallbackUrl = getLoopbackFallbackUrl(state.apiBaseUrl);
    if (error instanceof TypeError && allowFallback && fallbackUrl) {
      state.apiBaseUrl = fallbackUrl;
      localStorage.setItem("psychAgentApiBaseUrl", state.apiBaseUrl);
      hydrateSettings();
      syncConnectionLabel();
      return requestJson(path, options, false, revision);
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

function bindShell() {
  const media = window.matchMedia("(max-width: 900px)");
  const toggleSidebar = () => {
    if (media.matches) state.sidebarMobileOpen = !state.sidebarMobileOpen;
    else {
      state.sidebarCollapsed = !state.sidebarCollapsed;
      localStorage.setItem("psychAgentSidebarCollapsed", String(state.sidebarCollapsed));
    }
    syncSidebar();
    if (state.sidebarMobileOpen) $("#sidebarCloseBtn").focus();
    else if (state.sidebarCollapsed || media.matches) $("#sidebarToggleBtn").focus();
  };
  $("#sidebarToggleBtn").addEventListener("click", toggleSidebar);
  $("#sidebarCloseBtn").addEventListener("click", toggleSidebar);
  $("#sidebarScrim").addEventListener("click", () => closeMobileSidebar());
  $(".sidebar-brand").addEventListener("click", event => { event.preventDefault(); startNewConversation(); });
  $("#searchConversationsBtn").addEventListener("click", () => {
    const search = $("#conversationSearch");
    search.hidden = !search.hidden;
    $("#searchConversationsBtn").setAttribute("aria-expanded", String(!search.hidden));
    if (!search.hidden) search.focus();
    else { search.value = ""; renderConversationList(); }
  });
  $("#conversationSearch").addEventListener("input", renderConversationList);
  const applyTheme = dark => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    $("#themeToggleBtn span").textContent = dark ? "切换为浅色" : "切换为深色";
  };
  applyTheme(localStorage.getItem("psychAgentTheme") === "dark");
  $("#themeToggleBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    localStorage.setItem("psychAgentTheme", dark ? "dark" : "light");
    applyTheme(dark);
    $("#sessionPanel").open = false;
  });
  $("#showDetailsBtn").addEventListener("click", () => $("#insightDialog").showModal());
  $("#closeDetailsBtn").addEventListener("click", () => $("#insightDialog").close());
  $("#insightDialog").addEventListener("click", event => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.target === event.currentTarget && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) event.currentTarget.close();
  });
  $("#composerRoleBtn").addEventListener("click", () => switchView("roleCardView"));
  $$("[data-prompt]").forEach(button => button.addEventListener("click", () => {
    $("#messageInput").value = button.dataset.prompt;
    resizeComposer();
    $("#messageInput").focus();
  }));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      closeMobileSidebar();
      $("#sessionPanel").open = false;
    }
    if (state.token && (event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "o") {
      event.preventDefault(); startNewConversation();
    }
    if (event.key === "Tab" && state.sidebarMobileOpen) {
      const items = [...$("#sidebar").querySelectorAll('button:not(:disabled), a[href], input, summary')].filter(item => item.offsetParent !== null);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  media.addEventListener("change", () => { state.sidebarMobileOpen = false; syncSidebar(); resizeComposer(); });
  new ResizeObserver(() => $("#chatView").style.setProperty("--composer-height", `${$("#chatForm").offsetHeight}px`)).observe($("#chatForm"));
  resizeComposer();
}

function syncSidebar() {
  const mobile = window.matchMedia("(max-width: 900px)").matches;
  const open = mobile ? state.sidebarMobileOpen : !state.sidebarCollapsed;
  $("#appShell").classList.toggle("sidebar-collapsed", !mobile && state.sidebarCollapsed);
  $("#appShell").classList.toggle("sidebar-mobile-open", mobile && open);
  $("#sidebarScrim").hidden = !(mobile && open);
  $("#sidebar").inert = !open;
  $("#workspace").inert = mobile && open;
  $("#sidebarToggleBtn").setAttribute("aria-expanded", String(open));
  $("#sidebarToggleBtn").setAttribute("aria-label", open ? "收起侧栏" : "打开侧栏");
}

function closeMobileSidebar() {
  if (!state.sidebarMobileOpen) return;
  state.sidebarMobileOpen = false;
  syncSidebar();
  $("#sidebarToggleBtn").focus();
}

function bindNavigation() {
  $$(".nav-tab").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
}

function switchView(viewId, {load = true} = {}) {
  const tab = $$(".nav-tab").find(button => button.dataset.view === viewId);
  if (!tab || (tab.classList.contains("admin-only") && !isAdmin())) return;
  state.currentView = viewId;
  $$(".nav-tab").forEach(button => {
    button.classList.toggle("active", button === tab);
    if (button === tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$(".view").forEach(view => view.classList.toggle("active-view", view.id === viewId));
  $("#showDetailsBtn").hidden = viewId !== "chatView";
  $("#sessionPanel").open = false;
  closeMobileSidebar();
  if (!load || !state.token) return;
  const loaders = {goalsView: loadGoals, reportsView: refreshReports, roleCardView: loadRoleCards,
    helpView: loadHelpResources, metricsView: loadMetrics, profileView: refreshProfile, knowledgeView: loadKnowledge};
  loaders[viewId]?.();
}

function bindAuth() {
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("#loginBtn").disabled) return;
    const payload = readCredentials();
    if (!payload) return;
    $("#loginBtn").disabled = true;
    try {
      const data = await api("/auth/login", {
        method: "POST",
        auth: false,
        body: JSON.stringify(payload),
      });
      state.authRevision += 1;
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
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message);
    } finally {
      $("#loginBtn").disabled = false;
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
      if (error.name === "AbortError") return;
      toast(error.message);
    }
  });

  $("#logoutBtn").addEventListener("click", () => {
    clearSession(true);
  });
}

function normalizeStoredSession() {
  if (state.token && typeof state.user?.username === "string" && ["USER", "ADMIN"].includes(state.user.role)) return;
  state.token = "";
  state.user = null;
  localStorage.removeItem("psychAgentToken");
  localStorage.removeItem("psychAgentUser");
}

function clearSession(showToast) {
  const hadSession = Boolean(state.token || state.user);
  state.streamController?.abort();
  state.authRevision += 1;
  state.streamController = null;
  state.conversations = [];
  state.conversationLoadId += 1;
  state.historyLoading = false;
  state.profiles = [];
  state.adminMemories = [];
  window.PsychGoalsView.reset();
  window.PsychMetricsView.reset();
  state.currentRagTrace = null;
  state.currentMetrics = null;
  $("#insightDialog").close();
  $("#sessionPanel").open = false;
  $("#conversationSearch").value = "";
  $("#messageInput").value = "";
  $("#activeRoleLabel").textContent = "陪伴风格";
  ["#goalList", "#goalNotice", "#roleCardList", "#adminSessionList", "#adminMessageList", "#profileUserSelect", "#riskDistribution", "#emotionTrend", "#ragEvaluationMetrics", "#ragEvaluationCases", "#helpResources", "#knowledgeResults"].forEach(selector => { if ($(selector)) $(selector).replaceChildren(); });
  $("#goalForm").reset();
  $("#roleCardForm").reset();
  window.PsychRoleStudio?.reset();
  window.AttuneModelApi?.reset();
  $("#profileForm").reset();
  $("#knowledgeForm").reset();
  $("#knowledgeImportForm").reset();
  setStreaming(false);
  resizeComposer();
  activateDefaultView();
  document.title = "Attune";
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
  window.PsychReportView.reset();
  $("#metricsOverview").innerHTML = "";
  $("#recentRuns").innerHTML = "";
  $("#runTrendChart").innerHTML = "";
  $("#runtimeQuality").innerHTML = "";
  syncChatEmpty();
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
  const input = $("#messageInput");
  input.addEventListener("input", resizeComposer);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#chatForm").requestSubmit();
    }
  });
  $("#stopReplyBtn").addEventListener("click", () => state.streamController?.abort());
  $("#chatLog").addEventListener("scroll", () => {
    const log = $("#chatLog");
    state.stickToBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 90;
    $("#scrollToBottomBtn").hidden = state.stickToBottom;
  });
  $("#scrollToBottomBtn").addEventListener("click", () => scrollChatToBottom(true));
  $("#chatForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (!state.token || state.activeStream || state.historyLoading) return;
    const message = input.value.trim();
    if (!message) return;
    const revision = state.authRevision;
    state.streamController = new AbortController();
    setStreaming(true);
    state.stickToBottom = true;
    const userMessage = addMessage("user", message);
    input.value = "";
    resizeComposer();
    resetExecutionTimeline();
    resetLiveMetrics();
    renderRagTrace(null);
    $("#contextBudgetValue").textContent = "计算中";
    $("#memoryStatusValue").textContent = "待检查";
    setAgentStatuses({rag: "准备检索", tool: "决策中", safety: "评估中"});
    syncRunState("决策中", 0);
    const pendingMessage = addMessage("meta pending", "正在整理思绪…");
    try {
      const data = await streamAgentChat(message, pendingMessage);
      if (revision !== state.authRevision) return;
      if (data.sessionId) state.currentSessionId = data.sessionId;
      renderRagTrace(data.ragTrace || null);
      updateAgentStateFromResponse(data);
      if (data.crisis || (data.resources || []).length) addCrisisCard(data.resources || [], Boolean(data.crisis));
      await loadConversations();
    } catch (error) {
      if (revision !== state.authRevision) return;
      if (error.name === "AbortError") {
        addMessage("meta", "已停止接收回复。会话记录可在稍后刷新查看。");
        setAgentStatuses({tool: "已停止接收"});
      } else {
        addMessage("meta", error.message);
        if (error.code === "ADMISSION_BUSY") {
          userMessage.remove();
          if (!input.value.trim()) input.value = message;
          resizeComposer();
          setAgentStatuses({rag: "等待重试", tool: "暂时繁忙", safety: "基础检查完成"});
          $("#contextBudgetValue").textContent = "未执行";
          $("#memoryStatusValue").textContent = "未提交";
          syncRunState("暂时繁忙", 0);
        } else {
          setAgentStatuses({rag: "失败", tool: "异常", safety: "需检查"});
          syncRunState("异常", 0);
        }
      }
    } finally {
      pendingMessage.remove();
      if (revision === state.authRevision) {
        state.streamController = null;
        setStreaming(false);
        syncChatEmpty();
        if (state.currentView === "chatView") input.focus();
      }
    }
  });
}

async function streamAgentChat(message, pendingMessage) {
  const response = await fetchAgentStream(
    "/agent/chat/stream",
    { message, sessionId: state.currentSessionId },
    state.streamController.signal,
  );
  const assistantMessage = addMessage("assistant streaming", "");
  const activity = window.PsychToolActivity.attach(assistantMessage);
  const authRevision = state.authRevision;
  let requestId = null;
  const memory = window.PsychMemoryStatus.attach(assistantMessage, {
    fetchStatus: (id, signal) => api(`/agent/memory-updates/${encodeURIComponent(id)}`, {signal}),
    isCurrent: () => authRevision === state.authRevision,
    onChange: value => {
      if (requestId && requestId === state.currentMemoryRequestId) {
        $("#memoryStatusValue").textContent = value.label;
      }
    },
  });
  let finalData = null;
  let streamError = null;
  let receivedToken = false;

  try {
    await consumeSse(response, (eventName, payload) => {
      switch (eventName) {
        case "meta":
          requestId = payload.requestId || null;
          state.currentMemoryRequestId = requestId;
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
          appendExecutionEvent(`工具处理结束：${label}`);
          renderLiveRetrieval(payload.retrieval || {});
          break;
        }
        case "context": {
          if (payload.mode === "goal_planning") {
            $("#contextBudgetValue").textContent = "由目标工具规划";
            appendExecutionEvent("目标内容由创建工具生成，保存结果会单独显示");
            break;
          }
          const estimated = Number(payload.estimatedTokens || 0);
          const maximum = Number(payload.maxTokens || 0);
          $("#contextBudgetValue").textContent = `${estimated} / ${maximum}`;
          appendExecutionEvent(`上下文预算 ${estimated}/${maximum}，裁剪 ${payload.droppedMessages || 0} 条`);
          if(payload.roleVoice?.enabled)appendExecutionEvent('已应用人设与对话示例');
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
          activity.observeLoop(payload);
          appendExecutionEvent(payload.action === "finish" ? "所需信息已收集" : payload.action === "clarify" ? "需要补充信息" : "正在获取支持信息");
          break;
        case "loop_observation":
          activity.observeLoop(payload);
          break;
        case "tool_activity":
          activity.update(payload);
          if (activity.hasCalls() && pendingMessage.isConnected) pendingMessage.remove();
          scrollChatToBottom();
          break;
        case "knowledge_progress":
          activity.observeKnowledge(payload);
          if (pendingMessage.isConnected) pendingMessage.remove();
          scrollChatToBottom();
          break;
        case "goal_created":
          window.PsychGoalsView.showCreated(payload.goals || [],assistantMessage);
          break;
        case "goal_progress":
          window.PsychGoalsView.showSynced(payload.updates || [],assistantMessage);
          break;
        case "memory":
          if (payload.status === "pending") memory.pending();
          appendExecutionEvent(payload.label || "记忆状态已更新");
          break;
        case "warning":
          appendExecutionEvent(payload.message || "运行时警告", "warning");
          break;
        case "node_metric":
          updateExecutionNodeMetric(payload);
          break;
        case "metrics":
          activity.reconcile(payload);
          renderLiveMetrics(payload);
          break;
        case "reply_reset":
          updateMessage(assistantMessage, "");
          receivedToken = false;
          break;
        case "token":
          if (pendingMessage.isConnected) pendingMessage.remove();
          receivedToken = true;
          updateMessage(assistantMessage, assistantMessage.rawText + (payload.text || ""));
          break;
        case "done":
          finalData = payload;
          requestId = payload.metrics?.requestId || requestId;
          state.currentMemoryRequestId = requestId;
          memory.complete(requestId, payload.metrics?.persistenceStatus === "saved");
          activity.reconcile(payload.metrics);
          activity.finish("done");
          window.PsychGoalsView.showSynced(payload.goalUpdates || [],assistantMessage);
          window.PsychGoalsView.showCreated(payload.createdGoals || [],assistantMessage);
          if (payload.reply) updateMessage(assistantMessage, payload.reply, true);
          assistantMessage.classList.remove("streaming");
          appendExecutionEvent("本轮图执行完成");
          renderLiveMetrics(payload.metrics || null);
          break;
        case "error":
          streamError = new Error(payload.message || "Agent 流式执行失败");
          streamError.code = payload.code;
          appendExecutionEvent(streamError.message, "warning");
          break;
        default:
          break;
      }
    });
  } finally {
    if (!finalData) memory.interrupt();
    activity.finish(state.streamController?.signal.aborted ? "stopped" : finalData ? "done" : "interrupted");
    updateMessage(assistantMessage, assistantMessage.rawText, true);
    assistantMessage.classList.remove("streaming");
    if (!assistantMessage.rawText && !activity.hasCalls()) assistantMessage.remove();
  }

  if (pendingMessage.isConnected) pendingMessage.remove();
  if (streamError) {
    if (!receivedToken && !activity.hasCalls()) {
      assistantMessage.remove();
    }
    throw streamError;
  }
  if (!finalData) {
    if (!receivedToken && !activity.hasCalls()) {
      assistantMessage.remove();
    }
    throw new Error("流式响应未正常结束");
  }
  return finalData;
}

async function fetchAgentStream(path, payload, signal) {
  const revision = state.authRevision;
  try {
    const response = await fetch(`${state.apiBaseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: `Bearer ${state.token}`,
      },
      body: JSON.stringify(payload),
      signal,
    });
    if (revision !== state.authRevision) throw new DOMException("会话已变化", "AbortError");
    if (response.status === 401) {
      clearSession(false);
    }
    if (!response.ok) {
      const text = await response.text();
      const data = text ? parseJson(text) : null;
      const error = new Error(data?.message || text || `请求失败：${response.status}`);
      error.code = data?.code;
      throw error;
    }
    if (!response.body) {
      throw new Error("浏览器不支持流式响应");
    }
    state.backendOnline = true;
    syncConnectionLabel();
    return response;
  } catch (error) {
    if (error.name === "AbortError") throw error;
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
  try {
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
  } finally {
    reader.releaseLock();
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
  window.PsychRoleStudio?.bind({api,toast,state,reload:loadRoleCards});
  $("#refreshRoleCardsBtn").addEventListener("click", loadRoleCards);
  $("#roleCardForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/role-cards", {
        method: "POST",
        body: JSON.stringify({
          name: $("#roleCardName").value.trim(),
          description: $("#roleCardInstructions").value.trim().slice(0,100),
          tone: "自然",
          responseStyle: "遵循角色的交流习惯",
          customInstructions: [$("#roleCardInstructions").value.trim(),
            $("#roleCardExamples").value.trim() ? `对话示例（仅示范语气）：\n${$("#roleCardExamples").value.trim()}` : ""].filter(Boolean).join("\n\n"),
          forbiddenExpressions: "",
        }),
      });
      event.target.reset();
      await loadRoleCards();
      toast("角色卡已创建");
    } catch (error) {
      if (error.name === "AbortError") return;
      toast(error.message);
    }
  });
}

async function loadRoleCards() {
  if (!state.token) return;
  try {
    const token=state.token;
    const [cards,feature] = await Promise.all([api("/role-cards"),api("/role-cards/studio/features").catch(()=>({enabled:false}))]);
    if(token!==state.token)return;
    window.PsychRoleStudio?.render(cards,feature.enabled===true);
    $("#activeRoleLabel").textContent = cards.find(card => card.active)?.name || "陪伴风格";
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
    if (error.name === "AbortError") return;
    $("#roleCardList").innerHTML = empty(error.message);
  }
}

async function activateRoleCard(roleCardId) {
  try {
    await api(`/role-cards/${roleCardId}/activate`, { method: "POST" });
    await loadRoleCards();
    toast("角色卡已启用");
  } catch (error) {
    if (error.name === "AbortError") return;
    toast(error.message);
  }
}

function startNewConversation() {
  if (state.activeStream) { toast("请先停止接收当前回复，再开始新对话"); return; }
  refreshWelcomeGreeting();
  state.conversationLoadId += 1;
  state.historyLoading = false;
  switchView("chatView");
  state.stickToBottom = true;
  state.currentSessionId = null;
  $("#chatLog").innerHTML = "";
  syncChatEmpty();
  $("#messageInput").value = "";
  resizeComposer();
  $("#messageInput").focus();
  document.title = "Attune";
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
  const item = document.createElement("article");
  item.className = `message ${role}`;
  item.setAttribute("aria-label", role.includes("assistant") ? "Attune 回复" : role === "user" ? "你的消息" : "对话状态");
  const body = document.createElement("div");
  body.className = "message-content";
  item.appendChild(body);
  if (role.includes("assistant")) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.title = "复制回复";
    copy.setAttribute("aria-label", "复制回复");
    copy.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#icon-copy"/></svg>';
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(item.rawText); toast("已复制回复"); }
      catch { toast("暂时无法复制，请选中文字复制"); }
    });
    actions.appendChild(copy);
    item.appendChild(actions);
  }
  $("#chatLog").appendChild(item);
  updateMessage(item, String(content ?? ""), true);
  syncChatEmpty();
  return item;
}

function updateMessage(item, text, immediate = false) {
  item.rawText = item.classList.contains("assistant") ? window.PsychChatRenderer.visibleText(text) : text;
  const render = () => {
    item.renderFrame = null;
    const body = item.querySelector(".message-content");
    if (item.classList.contains("assistant")) window.PsychChatRenderer.render(body, item.rawText);
    else body.textContent = item.rawText;
    scrollChatToBottom();
  };
  if (immediate) {
    if (item.renderFrame) cancelAnimationFrame(item.renderFrame);
    render();
  } else if (!item.renderFrame) item.renderFrame = requestAnimationFrame(render);
}

function refreshWelcomeGreeting() {
  const heading = $("#chatWelcome h1");
  const greetings = ["很高兴见到你。", "你好，今天过得怎么样？", "有什么想聊的吗？", "最近有什么新鲜事？", "从你想说的开始。", "今天，我们从哪里聊起？", "嗨，见到你真好。", "有什么想一起想想的？"];
  const choices = greetings.filter(text => text !== heading.textContent);
  heading.textContent = choices[Math.floor(Math.random() * choices.length)];
}

function syncChatEmpty() {
  const emptyChat = !$("#chatLog").children.length;
  $("#chatView").classList.toggle("is-empty", emptyChat);
  $("#chatWelcome").hidden = !emptyChat;
  $("#starterPrompts").hidden = !emptyChat;
  $("#showDetailsBtn").disabled = emptyChat;
  if (emptyChat) $("#scrollToBottomBtn").hidden = true;
}

function scrollChatToBottom(force = false) {
  if (force) state.stickToBottom = true;
  if (state.stickToBottom) {
    const log = $("#chatLog");
    log.scrollTop = log.scrollHeight;
    $("#scrollToBottomBtn").hidden = true;
  }
}

function resizeComposer() {
  const input = $("#messageInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(200, Math.max(42, input.scrollHeight))}px`;
  $("#chatForm button[type='submit']").disabled = state.activeStream || state.historyLoading || !input.value.trim();
}

function setStreaming(active) {
  state.activeStream = active;
  $("#chatLog").setAttribute("aria-busy", String(active));
  $("#stopReplyBtn").hidden = !active;
  $("#chatForm button[type='submit']").hidden = active;
  resizeComposer();
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
  scrollChatToBottom();
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
  if(state.currentView==="metricsView")loadMetrics({silent:true});
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
      if (error.name === "AbortError") return;
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
    if (error.name === "AbortError") return;
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
    if (error.name === "AbortError") return;
    toast(error.message);
  }
}

async function loadConversations() {
  if (!state.token) return;
  try {
    state.conversations = await api("/conversations");
    renderConversationList();
  } catch (error) {
    if (error.name === "AbortError") return;
    toast(error.message);
  }
}

function renderConversationList() {
  const query = $("#conversationSearch").value.trim().toLocaleLowerCase();
  const conversations = (state.conversations || []).filter(session => (session.title || "未命名对话").toLocaleLowerCase().includes(query));
  $("#conversationList").innerHTML = conversations.length
    ? conversations.map(session => `
      <button class="conversation-item ${session.id === state.currentSessionId ? "active" : ""}" data-session-id="${session.id}" type="button" ${session.id === state.currentSessionId ? 'aria-current="true"' : ''} title="${escapeHtml(session.title || "未命名对话")}">
        <span class="conversation-title">${escapeHtml(session.title || "未命名对话")}</span>
      </button>`).join("")
    : `<div class="history-empty">${query ? "没有匹配的对话" : "对话会保存在这里"}</div>`;
  $$(".conversation-item").forEach(button => button.addEventListener("click", () => continueConversation(Number(button.dataset.sessionId))));
}

async function continueConversation(sessionId) {
  if (state.activeStream) { toast("请先停止接收当前回复，再切换对话"); return; }
  const loadId = ++state.conversationLoadId;
  const viewAtStart = state.currentView;
  state.historyLoading = true;
  resizeComposer();
  try {
    const messages = await api(`/conversations/${sessionId}/messages`);
    if (loadId !== state.conversationLoadId || state.currentView !== viewAtStart) return;
    switchView("chatView");
    state.stickToBottom = true;
    state.currentSessionId = sessionId;
    document.title = `${state.conversations.find(session => session.id === sessionId)?.title || "对话"} · Attune`;
    $("#chatLog").innerHTML = "";
    messages.filter(message => ["assistant", "user"].includes(message.role)).forEach(message => addMessage(message.role, message.content));
    syncChatEmpty();
    scrollChatToBottom(true);
    renderRagTrace(null);
    setAgentStatuses({ rag: "历史会话", tool: "未调用", safety: "常规", session: `#${sessionId}` });
    resetExecutionTimeline();
    $("#contextBudgetValue").textContent = "历史";
    $("#memoryStatusValue").textContent = "已加载";
    syncRunState("未调用", 0);
    renderConversationList();
  } catch (error) {
    if (error.name === "AbortError") return;
    if (loadId === state.conversationLoadId) toast(error.message);
  } finally {
    if (loadId === state.conversationLoadId) { state.historyLoading = false; resizeComposer(); }
  }
}

function bindReports() {
  window.PsychReportView.bind({api, state, switchView, startNewConversation, continueConversation,
    empty, formatTime});
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
    if (error.name === "AbortError") return;
    $("#helpResources").innerHTML = empty(error.message);
  }
}

function refreshReports() {
  return window.PsychReportView.load();
}

function bindMetrics() {
  window.PsychMetricsView.bind({api,state,toast,formatDuration,formatTime,isAdmin,empty});
}
function loadMetrics(options={}) {return window.PsychMetricsView.load(options);}

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
      if (error.name === "AbortError") return;
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
      if (error.name === "AbortError") return;
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
    if (error.name === "AbortError") return;
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
    if (error.name === "AbortError") return;
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
    if (error.name === "AbortError") return;
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
        if (error.name === "AbortError") return;
        toast(error.message);
      }
    });
  });
}

function bindSettings() {
  window.AttuneModelApi.bind({api,state,toast,closeMenu:()=>{$("#sessionPanel").open=false;closeMobileSidebar();}});
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
  const label = state.backendOnline ? "服务已连接" : "连接暂不可用";
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
  $("#accountAvatar").textContent = loggedIn ? [...state.user.username][0]?.toUpperCase() || "P" : "P";
  syncSidebar();
}

function activateDefaultView() {
  switchView("chatView", {load: false});
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
  if (mode === "external_unpriced") return "由接口服务计费";
  const actualValue = Number(actual || 0);
  const estimatedValue = Number(estimated || 0);
  if (mode === "local") {
    return estimatedValue > 0
      ? `$${actualValue.toFixed(4)} · ≈$${estimatedValue.toFixed(4)}`
      : "$0 · local";
  }
  return `$${actualValue.toFixed(6)}`;
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

function bindGoals() {
  window.PsychGoalsView.bind({api,state,startNewConversation,switchView,toast});
}
function loadGoals() {
  return window.PsychGoalsView.load();
}

init();
