"use strict";

// 좌측 chat-rail 컴포넌트 — 에이전트 채팅 탭 + 도구 실행 로그 탭.
// 호스트 페이지가 partials/chat_rail.html을 include하고 window.ORCA_CHAT_RAIL
// (channelId, tasks)을 심어준 뒤 이 스크립트를 로드한다.
(function () {
  const rail = document.getElementById("orca-chat-rail");
  if (!rail) return;
  const CFG = window.ORCA_CHAT_RAIL || { channelId: null, tasks: [] };

  function tt(s) { return (typeof t === "function") ? t(s) : s; }
  function escapeHtml(s) {
    return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }
  function paintMarkdown(el, body) {
    if (typeof renderMarkdown === "function") renderMarkdown(el, body);
    else el.textContent = body;
  }

  // ── 접기/펼치기 (localStorage 유지) ─────────────────────────────────
  const collapseBtn = document.getElementById("orca-rail-collapse");
  const COLLAPSE_KEY = "orca-rail-collapsed";
  function isNarrow() { return window.innerWidth < 1024; }
  function applyCollapsed(collapsed) {
    rail.classList.toggle("is-collapsed", collapsed);
  }
  applyCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
  collapseBtn?.addEventListener("click", () => {
    if (isNarrow()) { setRailOpen(false); return; }
    const next = !rail.classList.contains("is-collapsed");
    applyCollapsed(next);
    localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
  });

  // ── 태블릿/모바일(<1024px) 오프캔버스 열기/닫기 ─────────────────────
  const mobileToggle = document.getElementById("orca-rail-mobile-toggle");
  const scrim = document.getElementById("orca-rail-scrim");
  function setRailOpen(open) {
    rail.classList.toggle("is-open", open);
    document.body.classList.toggle("orca-rail-open", open);
    if (scrim) scrim.hidden = !open;
  }
  mobileToggle?.addEventListener("click", () => setRailOpen(true));
  scrim?.addEventListener("click", () => setRailOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && rail.classList.contains("is-open")) setRailOpen(false);
  });

  // ── 리사이즈 (드래그로 280–480px) ────────────────────────────────────
  const resizeHandle = document.getElementById("orca-rail-resize");
  const WIDTH_KEY = "orca-rail-width";
  const savedWidth = parseInt(localStorage.getItem(WIDTH_KEY) || "", 10);
  if (savedWidth >= 280 && savedWidth <= 480) rail.style.width = savedWidth + "px";

  const DEFAULT_WIDTH = 360;
  let resizing = false;
  resizeHandle?.addEventListener("mousedown", (e) => {
    if (rail.classList.contains("is-collapsed")) return;
    e.preventDefault();
    resizing = true;
    rail.classList.add("is-resizing");
    resizeHandle.classList.add("active");
    document.body.classList.add("orca-resizing");
  });
  document.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const railRect = rail.getBoundingClientRect();
    const w = Math.min(480, Math.max(280, e.clientX - railRect.left));
    rail.style.width = w + "px";
  });
  document.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    rail.classList.remove("is-resizing");
    resizeHandle.classList.remove("active");
    document.body.classList.remove("orca-resizing");
    localStorage.setItem(WIDTH_KEY, parseInt(rail.style.width, 10) || DEFAULT_WIDTH);
  });
  resizeHandle?.addEventListener("dblclick", () => {
    rail.style.width = DEFAULT_WIDTH + "px";
    localStorage.setItem(WIDTH_KEY, DEFAULT_WIDTH);
  });
  resizeHandle?.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
    if (!step) return;
    e.preventDefault();
    const current = rail.getBoundingClientRect().width;
    const w = Math.min(480, Math.max(280, current + step));
    rail.style.width = w + "px";
    localStorage.setItem(WIDTH_KEY, w);
  });

  // ── 탭 전환 (localStorage 유지) ─────────────────────────────────────
  const tabs = [...rail.querySelectorAll("[data-rail-tab]")];
  const panels = [...rail.querySelectorAll("[data-rail-panel]")];
  const TAB_KEY = "orca-rail-tab";
  function activateTab(name) {
    const tabBtn = tabs.find((b) => b.dataset.railTab === name) || tabs[0];
    if (!tabBtn) return;
    tabs.forEach((b) => b.classList.toggle("active", b === tabBtn));
    panels.forEach((p) => { p.hidden = p.dataset.railPanel !== tabBtn.dataset.railTab; });
  }
  tabs.forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      activateTab(tabBtn.dataset.railTab);
      localStorage.setItem(TAB_KEY, tabBtn.dataset.railTab);
    });
  });
  activateTab(localStorage.getItem(TAB_KEY) || "chat");

  // ═══ 채팅 탭 ══════════════════════════════════════════════════════════
  const chatScroll = document.getElementById("orca-chat-scroll");
  const chatForm = document.getElementById("orca-chat-form");
  const chatTa = chatForm?.querySelector("textarea");
  const chatProviderSel = document.getElementById("orca-chat-provider");

  function roleLabel(m) {
    return m.role === "user" ? tt("나") : (m.author || m.provider || "agent");
  }

  function buildTraceStepEl(step) {
    const el = document.createElement("div");
    el.className = "orca-trace-step";
    el.dataset.stepId = step.id;
    const head = document.createElement("button");
    head.type = "button";
    head.className = "orca-trace-step-head";
    el.appendChild(head);
    const body = document.createElement("div");
    body.className = "orca-trace-step-body";
    const pre = document.createElement("pre");
    pre.className = "orca-trace-detail";
    body.appendChild(pre);
    el.appendChild(body);
    head.addEventListener("click", () => {
      el.dataset.userPinned = "1";
      el.classList.toggle("open");
    });
    return el;
  }

  const TRACE_KIND_META = {
    thought: { icon: "💭", label: tt("생각") },
    tool_call: { icon: "🔧", label: tt("도구 호출") },
    tool_result: { icon: "📋", label: tt("도구 결과") },
    output_chunk: { icon: "🖥", label: tt("실행 로그") },
    error: { icon: "⚠️", label: tt("오류") },
  };

  function updateTraceStepEl(el, step, isLatest) {
    const meta = TRACE_KIND_META[step.kind] || { icon: "•", label: step.kind };
    const head = el.querySelector(".orca-trace-step-head");
    head.innerHTML =
      `<span class="orca-trace-chevron">▸</span>` +
      `<span class="orca-trace-kind">${meta.icon}</span>` +
      `<span class="orca-trace-title">${escapeHtml(step.title || meta.label)}</span>` +
      `<span class="orca-trace-status-dot status-${step.status}" title="${escapeHtml(step.status)}"></span>`;
    el.querySelector(".orca-trace-detail").textContent = (step.detail || "").slice(-4000);
    if (el.dataset.userPinned !== "1") {
      el.classList.toggle("open", step.status === "running" || isLatest);
    }
  }

  function isMockId(id) { return typeof id === "string" && id.startsWith("mock-"); }

  function openTrace(messageId, traceEl) {
    if (traceEl.dataset.wired) return;
    traceEl.dataset.wired = "1";
    const steps = {};
    let latestId = null;
    const es = isMockId(messageId)
      ? window.OrcaMock.fakeStream(messageId)
      : new EventSource(`/api/messages/${messageId}/stream`);

    es.addEventListener("step", (e) => {
      const step = JSON.parse(e.data);
      let el = steps[step.id];
      if (!el) {
        el = buildTraceStepEl(step);
        traceEl.appendChild(el);
        steps[step.id] = el;
      }
      latestId = step.id;
      for (const [id, stepEl] of Object.entries(steps)) {
        if (stepEl.dataset.userPinned !== "1" && Number(id) !== latestId) {
          stepEl.classList.remove("open");
        }
      }
      updateTraceStepEl(el, step, true);
      chatScroll.scrollTop = chatScroll.scrollHeight;
    });

    es.addEventListener("status", (e) => {
      const status = JSON.parse(e.data);
      for (const row of chatScroll.querySelectorAll(`[data-msg-id="${messageId}"]`)) {
        row.dataset.status = status;
      }
    });

    es.addEventListener("done", async () => {
      es.close();
      const fresh = isMockId(messageId)
        ? window.OrcaMock.getMessage(messageId)
        : await (await fetch(`/api/messages/${messageId}`)).json();
      for (const row of chatScroll.querySelectorAll(`[data-msg-id="${messageId}"]`)) {
        paintMarkdown(row.querySelector(".orca-msg-bubble"), fresh.body || "");
        row.dataset.status = fresh.status;
        if (fresh.error && !row.querySelector(".orca-msg-error")) {
          const errEl = document.createElement("div");
          errEl.className = "orca-msg-error";
          errEl.textContent = fresh.error;
          row.appendChild(errEl);
        }
      }
      saveChatCache();
      // 이 메시지가 프로젝트 태스크와 같은 job을 썼을 수 있다 — 2초 폴링을
      // 기다리지 않고 중앙 비전보드를 바로 새로 그리게 한다.
      document.body.dispatchEvent(new Event("orca-refresh-board"));
    });
  }

  function buildMessageEl(m) {
    const row = document.createElement("div");
    row.className = "orca-msg " + (m.role === "user" ? "user" : "assistant");
    row.dataset.msgId = m.id;
    row.dataset.status = m.status;
    if (m.job_id) row.dataset.jobId = m.job_id;

    const roleEl = document.createElement("div");
    roleEl.className = "orca-msg-role";
    roleEl.textContent = roleLabel(m);
    row.appendChild(roleEl);

    const bubble = document.createElement("div");
    bubble.className = "orca-msg-bubble";
    const placeholder = (m.status === "queued" || m.status === "running") && !m.body
      ? "_" + tt("실행 중…") + "_" : (m.body || "");
    paintMarkdown(bubble, placeholder);
    row.appendChild(bubble);

    if (m.error) {
      const errEl = document.createElement("div");
      errEl.className = "orca-msg-error";
      errEl.textContent = m.error;
      row.appendChild(errEl);
    }

    if (m.status === "queued" || m.status === "running") {
      const trace = document.createElement("div");
      trace.className = "orca-trace";
      row.appendChild(trace);
      openTrace(m.id, trace);
    }
    return row;
  }

  // ── localStorage 캐시 (새로고침 시 즉시 복원) + 오프라인 Mock 폴백 ────
  const CACHE_KEY = "orca-chat-cache-" + (CFG.channelId ?? "none");
  const DRAFT_KEY = "orca-chat-draft-" + (CFG.channelId ?? "none");

  function saveChatCache() {
    if (!CFG.channelId) return;
    const rows = [...chatScroll.querySelectorAll("[data-msg-id]")].map((row) => ({
      id: row.dataset.msgId,
      role: row.classList.contains("user") ? "user" : "assistant",
      status: row.dataset.status,
      job_id: row.dataset.jobId || null,
      author: row.querySelector(".orca-msg-role")?.textContent || "",
      body: row.querySelector(".orca-msg-bubble")?.textContent || "",
      error: row.querySelector(".orca-msg-error")?.textContent || null,
    }));
    localStorage.setItem(CACHE_KEY, JSON.stringify(rows));
  }

  function loadChatCache() {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || "[]"); }
    catch (e) { return []; }
  }

  function renderOfflineNotice(on) {
    let el = document.getElementById("orca-rail-offline");
    if (!on) { el?.remove(); return; }
    if (el) return;
    el = document.createElement("p");
    el.id = "orca-rail-offline";
    el.className = "orca-rail-empty";
    el.textContent = tt("서버에 연결할 수 없어 저장된 대화를 보여드리고 있어요 (오프라인 모드).");
    chatScroll.before(el);
  }

  async function loadChatHistory() {
    if (!CFG.channelId) {
      chatScroll.innerHTML = `<p class="orca-rail-empty">${escapeHtml(tt("이 프로젝트에 연결된 채널이 없습니다."))}</p>`;
      return;
    }
    try {
      const roots = await (await fetch(`/api/channels/${CFG.channelId}/messages`)).json();
      chatScroll.innerHTML = "";
      for (const root of roots) {
        const messages = await (await fetch(`/api/messages/${root.id}/thread`)).json();
        for (const m of messages) chatScroll.appendChild(buildMessageEl(m));
      }
      renderOfflineNotice(false);
      chatScroll.scrollTop = chatScroll.scrollHeight;
      saveChatCache();
    } catch (err) {
      // 백엔드에 닿지 못했다 — 마지막으로 캐싱해둔 대화를 그대로 보여준다.
      chatScroll.innerHTML = "";
      renderOfflineNotice(true);
      for (const m of loadChatCache()) chatScroll.appendChild(buildMessageEl(m));
      chatScroll.scrollTop = chatScroll.scrollHeight;
    }
  }

  // ── 입력 초안 보존 ────────────────────────────────────────────────────
  if (chatTa) {
    const savedDraft = localStorage.getItem(DRAFT_KEY);
    if (savedDraft) chatTa.value = savedDraft;
    chatTa.addEventListener("input", () => {
      if (chatTa.value) localStorage.setItem(DRAFT_KEY, chatTa.value);
      else localStorage.removeItem(DRAFT_KEY);
    });
  }

  chatTa?.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!CFG.channelId) return;
    const body = chatTa.value.trim();
    if (!body) return;
    const provider = chatProviderSel.value;
    const btn = chatForm.querySelector(".orca-rail-send");
    btn.disabled = true;
    try {
      let data;
      try {
        const res = await fetch(`/api/channels/${CFG.channelId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body, provider }),
        });
        if (!res.ok) { alert(tt("전송하지 못했습니다")); return; }
        data = await res.json();
      } catch (netErr) {
        // 백엔드에 닿지 못했다 — 목 API가 대신 응답을 흉내 낸다.
        renderOfflineNotice(true);
        data = await window.OrcaMock.sendMessage(CFG.channelId, body, provider);
      }
      const [userMsg, agentMsg] = data._mock
        ? [window.OrcaMock.getMessage(data.user_message_id), window.OrcaMock.getMessage(data.message_id)]
        : await Promise.all([
            fetch(`/api/messages/${data.user_message_id}`).then((r) => r.json()),
            fetch(`/api/messages/${data.message_id}`).then((r) => r.json()),
          ]);
      chatScroll.appendChild(buildMessageEl(userMsg));
      chatScroll.appendChild(buildMessageEl(agentMsg));
      chatScroll.scrollTop = chatScroll.scrollHeight;
      chatTa.value = "";
      localStorage.removeItem(DRAFT_KEY);
      saveChatCache();
    } finally {
      btn.disabled = false;
    }
  });

  loadChatHistory();

  // ═══ 실행 로그 탭 ═════════════════════════════════════════════════════
  const logSelect = document.getElementById("orca-log-task-select");
  const logStream = document.getElementById("orca-log-stream");
  const logBadge = document.getElementById("orca-log-badge");
  let logEs = null;

  function runningTasks() {
    return (CFG.tasks || []).filter((tsk) => tsk.status === "running" && tsk.job_id);
  }

  function renderLogPicker() {
    const running = runningTasks();
    if (logBadge) {
      logBadge.hidden = running.length === 0;
      logBadge.textContent = String(running.length);
    }
    if (running.length === 0) {
      logSelect.innerHTML = "";
      logSelect.hidden = true;
      logStream.textContent = tt("현재 실행 중인 태스크가 없습니다.");
      if (logEs) { logEs.close(); logEs = null; }
      return;
    }
    logSelect.hidden = false;
    const prevValue = logSelect.value;
    logSelect.innerHTML = running.map((tsk) =>
      `<option value="${tsk.job_id}">[${tsk.seq}] ${escapeHtml(tsk.title || "")}</option>`
    ).join("");
    const stillThere = running.some((tsk) => String(tsk.job_id) === prevValue);
    logSelect.value = stillThere ? prevValue : String(running[0].job_id);
    streamJobLog(logSelect.value);
  }

  function streamJobLog(jobId) {
    if (logEs) { logEs.close(); logEs = null; }
    if (!jobId) return;
    logStream.textContent = "";
    logEs = new EventSource(`/jobs/${jobId}/stream`);
    logEs.onmessage = (e) => {
      logStream.textContent += JSON.parse(e.data);
      logStream.scrollTop = logStream.scrollHeight;
    };
    logEs.addEventListener("status", () => {
      logEs.close();
      logEs = null;
    });
  }

  logSelect?.addEventListener("change", () => streamJobLog(logSelect.value));
  renderLogPicker();

  // 호스트 페이지가 태스크 상태 변화를 알려줄 때(DAG 폴링 등) 갱신할 수 있도록
  // 이벤트 훅을 노출한다.
  document.body.addEventListener("orca-tasks-updated", (e) => {
    CFG.tasks = e.detail || [];
    renderLogPicker();
  });

  // 캔버스에서 노드를 선택하면(board-editor.js selectTask) 그 태스크가 실행
  // 중이면 실행 로그 탭으로 따라가고, 완료된 태스크라면 채팅 쪽에서 같은 job의
  // 메시지를 찾아 짧게 반짝여 "이게 그 대화였다"를 보여준다.
  document.body.addEventListener("orca-task-selected", (e) => {
    const { jobId, status } = e.detail || {};
    if (!jobId) return;
    if (status === "running" || status === "queued") {
      tabs.find((b) => b.dataset.railTab === "log")?.click();
      if (logSelect && [...logSelect.options].some((o) => o.value === String(jobId))) {
        logSelect.value = String(jobId);
        streamJobLog(jobId);
      }
    } else {
      const row = chatScroll?.querySelector(`[data-job-id="${jobId}"]`);
      if (row) {
        tabs.find((b) => b.dataset.railTab === "chat")?.click();
        row.scrollIntoView({ block: "center", behavior: "smooth" });
        row.classList.add("orca-live-flash");
        setTimeout(() => row.classList.remove("orca-live-flash"), 1200);
      }
    }
  });
})();
