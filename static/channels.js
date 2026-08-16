"use strict";

// ---- 대화 화면(채널·DM) — 쓰레드 렌더링·답장·실행 트레이스 스트리밍 -------
//
// 전용 페이지(/channels/{id})와 홈 중앙 탭이 이 코드를 함께 쓴다. 그래서 전역
// id 를 찾지 않고 **넘겨받은 root 안쪽만** 본다 — 탭을 여러 개 열면 같은 id 가
// 여러 벌 생기기 때문이다. 데이터도 전역 상수가 아니라 root 안의
// <script class="channel-data"> 에서 읽는다.
//
// mountChannelView(root) 는 정리 함수(dispose)를 돌려준다. 탭을 닫을 때 열어 둔
// EventSource 와 문서 수준 리스너를 반드시 끊어야 한다 — 안 그러면 닫은 대화가
// 백그라운드에서 계속 스트리밍을 받는다.
window.mountChannelView = function mountChannelView(root) {
  if (!root || root.dataset.mounted === "1") return null;
  root.dataset.mounted = "1";

  let CHANNEL, THREADS;
  try {
    const raw = JSON.parse(root.querySelector(".channel-data").textContent);
    CHANNEL = raw.channel;
    THREADS = raw.threads || [];
  } catch (e) {
    return null;   // 데이터가 없으면 붙일 것도 없다
  }

  const $ = (sel) => root.querySelector(sel);
  const threadList = $(".thread-list");
  if (!threadList) return null;

  // 이 화면이 연 스트림·리스너 — dispose 에서 전부 끊는다.
  const streams = new Set();
  const teardown = [];

  function escapeHtml(s) {
    return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function roleName(m) {
    if (m.role === "user") return t("나");
    if (m.role === "system") return t("시스템");
    // 커스텀 에이전트가 답한 메시지는 CLI 이름이 아니라 에이전트 이름으로
    // 보여준다 — 같은 CLI 를 쓰는 에이전트가 둘일 수 있다.
    if (m.author_agent_id) return "@" + (m.author || "agent");
    return m.author || m.provider || "agent";
  }

  // messages.attachments 는 JSON 배열 문자열로 온다(API 가 행을 그대로 준다).
  function messageAttachments(m) {
    const raw = m.attachments;
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function setBadge(row, status) {
    const badge = row.querySelector(".badge");
    if (badge) { badge.className = "badge badge-" + status; badge.textContent = status; }
    row.dataset.status = status;
  }

  function buildMessageEl(m, opts) {
    opts = opts || {};
    const row = document.createElement("div");
    row.className = "chat-turn msg-row " +
      (m.role === "user" ? "user" : (m.role === "system" ? "system" : "assistant"));
    row.dataset.msgId = m.id;
    row.dataset.status = m.status;

    const roleEl = document.createElement("div");
    roleEl.className = "chat-role";
    roleEl.innerHTML = `${escapeHtml(roleName(m))} <span class="badge badge-${m.status}">${m.status}</span>`;
    row.appendChild(roleEl);

    const bubbleWrap = document.createElement("div");
    bubbleWrap.className = "chat-bubble-wrap";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble md-body";
    bubbleWrap.appendChild(bubble);

    if (opts.rootId) {
      const replyBtn = document.createElement("button");
      replyBtn.type = "button";
      replyBtn.className = "msg-thread-btn";
      replyBtn.title = t("쓰레드로 답장하기");
      replyBtn.innerHTML =
        `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h12M2 8h8M2 8v4l3-2"/></svg>` +
        `<span>${escapeHtml(t("쓰레드로 답장하기"))}</span>`;
      replyBtn.addEventListener("click", () => openThreadPanel(opts.rootId));
      bubbleWrap.appendChild(replyBtn);
    }
    row.appendChild(bubbleWrap);

    const placeholder = (m.status === "queued" || m.status === "running") && !m.body
      ? "_" + t("실행 중…") + "_" : (m.body || "");
    renderMarkdown(bubble, placeholder);

    // 첨부는 경로로 전달된다 — 화면에는 파일명만, 전체 경로는 툴팁으로.
    const files = messageAttachments(m);
    if (files.length) {
      const chips = document.createElement("div");
      chips.className = "file-chips msg-attachments";
      for (const path of files) {
        const chip = document.createElement("span");
        chip.className = "chip";
        // 저장 파일명 앞에는 충돌 방지용 타임스탬프가 붙는다 — 사람에게는
        // 자기가 올린 이름으로 보여준다(전체 경로는 툴팁).
        chip.textContent = path.split("/").pop()
          .replace(/^\d{8}-\d{6,}-/, "");
        chip.title = path;
        chips.appendChild(chip);
      }
      bubbleWrap.appendChild(chips);
    }

    if (m.error) {
      const errEl = document.createElement("div");
      errEl.className = "msg-error";
      errEl.textContent = m.error;
      row.appendChild(errEl);
    }

    if (m.status === "queued" || m.status === "running") {
      const trace = document.createElement("div");
      trace.className = "msg-trace";
      row.appendChild(trace);
      openTrace(m.id, trace);
    }
    return row;
  }

  function bindEnterSubmit(ta) {
    ta.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        ta.form?.requestSubmit();
      }
    });
  }

  // 메인 채널 뷰: 각 쓰레드는 첫 질문/응답만 접어서 보여주고,
  // 답장은 우측 슬라이드아웃 패널에서 주고받는다 (Slack 쓰레드 패턴).
  function buildThreadEl(messages) {
    const rootMsg = messages[0];
    const wrap = document.createElement("div");
    wrap.className = "channel-thread";
    wrap.dataset.rootId = rootMsg.id;
    const chat = document.createElement("div");
    chat.className = "chat-thread";
    wrap.appendChild(chat);
    const replyBar = document.createElement("button");
    replyBar.type = "button";
    replyBar.className = "thread-reply-count";
    wrap.appendChild(replyBar);
    replyBar.addEventListener("click", () => openThreadPanel(rootMsg.id));
    updateThreadCard(wrap, messages);
    return wrap;
  }

  function updateThreadCard(wrap, messages) {
    const rootMsg = messages[0];
    const preview = messages.length > 1 ? messages.slice(0, 2) : messages;
    const chat = wrap.querySelector(".chat-thread");
    chat.innerHTML = "";
    for (const m of preview) chat.appendChild(buildMessageEl(m, { rootId: rootMsg.id }));
    const replyCount = messages.length - preview.length;
    const replyBar = wrap.querySelector(".thread-reply-count");
    if (replyCount > 0) {
      const last = messages[messages.length - 1];
      replyBar.hidden = false;
      replyBar.innerHTML =
        `<span class="thread-reply-num">${replyCount}${escapeHtml(t("개 답장"))}</span>` +
        `<span class="thread-reply-last">${escapeHtml((last.body || "").slice(0, 60))}</span>`;
    } else {
      replyBar.hidden = true;
    }
  }

  async function sendMessage(body, parentId, provider, opts) {
    opts = opts || {};
    const payload = { body, provider: provider || "auto",
                      parent_id: parentId || null };
    // 안 고른 값은 보내지 않는다 — 서버가 방·에이전트 기본값을 쓰게 둔다.
    if (opts.workdir !== undefined) payload.workdir = opts.workdir;
    if (opts.attachments?.length) payload.attachments = opts.attachments;
    const res = await fetch(`/api/channels/${CHANNEL.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.json().then((d) => d.detail).catch(() => null);
      alert(detail || t("전송하지 못했습니다"));
      return null;
    }
    const data = await res.json();
    const [userMsg, agentMsg] = await Promise.all([
      fetch(`/api/messages/${data.user_message_id}`).then((r) => r.json()),
      fetch(`/api/messages/${data.message_id}`).then((r) => r.json()),
    ]);
    return { userMsg, agentMsg };
  }

  async function refreshThreadCard(rootId) {
    const wrap = threadList.querySelector(`.channel-thread[data-root-id="${rootId}"]`);
    if (!wrap) return;
    const messages = await (await fetch(`/api/messages/${rootId}/thread`)).json();
    updateThreadCard(wrap, threadMessages(messages));
  }

  // /api/messages/{id}/thread 는 {root_id, messages} 를 준다. 초기 THREADS 는
  // 배열이라 두 형태를 모두 받아 준다.
  function threadMessages(payload) {
    return Array.isArray(payload) ? payload : (payload.messages || []);
  }

  // ---- 실행 과정 타임라인(아코디언) ------------------------------------
  // 실행 중인 스텝(생각/도구 호출 등)을 한 줄 헤더로 접어서 보여주고, 클릭하면
  // 상세 로그를 펼친다. 사용자가 직접 펼치거나 접은 스텝은 새 스텝이 와도
  // 그 상태를 유지한다(userPinned).
  const TRACE_KIND_META = {
    thought: { icon: "💭", label: t("생각") },
    tool_call: { icon: "🔧", label: t("도구 호출") },
    tool_result: { icon: "📋", label: t("도구 결과") },
    output_chunk: { icon: "🖥", label: t("실행 로그") },
    error: { icon: "⚠️", label: t("오류") },
  };

  function traceKindMeta(kind) {
    return TRACE_KIND_META[kind] || { icon: "•", label: kind };
  }

  function buildTraceStepEl(step) {
    const el = document.createElement("div");
    el.className = "trace-step";
    el.dataset.stepId = step.id;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "trace-step-head";
    el.appendChild(head);

    const body = document.createElement("div");
    body.className = "trace-step-body";
    const pre = document.createElement("pre");
    pre.className = "trace-detail";
    body.appendChild(pre);
    el.appendChild(body);

    head.addEventListener("click", () => {
      el.dataset.userPinned = "1";
      el.classList.toggle("open");
    });
    return el;
  }

  function updateTraceStepEl(el, step, isLatest) {
    const meta = traceKindMeta(step.kind);
    const head = el.querySelector(".trace-step-head");
    head.innerHTML =
      `<span class="trace-chevron">▸</span>` +
      `<span class="trace-icon">${meta.icon}</span>` +
      `<span class="trace-kind">${escapeHtml(meta.label)}</span>` +
      `<span class="trace-title">${escapeHtml(step.title || "")}</span>` +
      `<span class="trace-status-dot status-${step.status}" title="${escapeHtml(step.status)}"></span>`;
    el.querySelector(".trace-detail").textContent = (step.detail || "").slice(-4000);
    el.dataset.kind = step.kind;
    el.dataset.status = step.status;

    // 사용자가 직접 펼치거나 접지 않았다면: 실행 중이거나 가장 최근 스텝만
    // 자동으로 펼쳐 보여준다.
    if (el.dataset.userPinned !== "1") {
      el.classList.toggle("open", step.status === "running" || isLatest);
    }
  }

  // 같은 메시지가 본문 카드와 쓰레드 패널에 동시에 있을 수 있다 — 둘 다 갱신한다.
  function rowsFor(messageId) {
    return root.querySelectorAll(`[data-msg-id="${messageId}"]`);
  }

  function openTrace(messageId, trace) {
    if (!trace || trace.dataset.wired) return;
    trace.dataset.wired = "1";

    const head = document.createElement("div");
    head.className = "msg-trace-head";
    head.innerHTML = `<span class="msg-trace-title">${escapeHtml(t("실행 과정"))}</span>` +
      `<button type="button" class="msg-trace-toggle-all">${escapeHtml(t("모두 펼치기"))}</button>`;
    trace.appendChild(head);
    const list = document.createElement("div");
    list.className = "msg-trace-list";
    trace.appendChild(list);

    head.querySelector(".msg-trace-toggle-all").addEventListener("click", (e) => {
      const stepEls = [...list.children];
      const shouldOpen = e.target.textContent === t("모두 펼치기");
      for (const el of stepEls) {
        el.dataset.userPinned = "1";
        el.classList.toggle("open", shouldOpen);
      }
      e.target.textContent = shouldOpen ? t("모두 접기") : t("모두 펼치기");
    });

    const steps = {};
    let latestId = null;
    const es = new EventSource(`/api/messages/${messageId}/stream`);
    streams.add(es);

    es.addEventListener("step", (e) => {
      const step = JSON.parse(e.data);
      let el = steps[step.id];
      if (!el) {
        el = buildTraceStepEl(step);
        list.appendChild(el);
        steps[step.id] = el;
      }
      latestId = step.id;
      for (const [id, stepEl] of Object.entries(steps)) {
        if (stepEl.dataset.userPinned !== "1" && Number(id) !== latestId) {
          stepEl.classList.remove("open");
        }
      }
      updateTraceStepEl(el, step, true);
      trace.scrollTop = trace.scrollHeight;
    });

    es.addEventListener("status", (e) => {
      const status = JSON.parse(e.data);
      for (const row of rowsFor(messageId)) setBadge(row, status);
    });

    es.addEventListener("done", async () => {
      es.close();
      streams.delete(es);
      try {
        const fresh = await (await fetch(`/api/messages/${messageId}`)).json();
        for (const row of rowsFor(messageId)) {
          renderMarkdown(row.querySelector(".chat-bubble"), fresh.body || "");
          setBadge(row, fresh.status);
          if (fresh.error && !row.querySelector(".msg-error")) {
            const errEl = document.createElement("div");
            errEl.className = "msg-error";
            errEl.textContent = fresh.error;
            row.appendChild(errEl);
          }
        }
      } finally {
        // 사이드바 미리보기·채팅 목록이 방금 끝난 대화를 반영하게 한다.
        document.body.dispatchEvent(new Event("refresh-channels"));
      }
    });
  }

  // ---- 쓰레드 답글 패널 ------------------------------------------------
  const threadPanel = $(".thread-panel");
  const threadPanelScrim = $(".thread-panel-scrim");
  const threadPanelBody = $(".thread-panel-body");
  const threadPanelSub = $(".thread-panel-sub");
  const threadPanelForm = $(".thread-panel-form");
  const threadPanelTa = threadPanelForm?.querySelector("textarea");
  let panelRootId = null;

  function closeThreadPanel() {
    panelRootId = null;
    if (!threadPanel) return;
    threadPanel.hidden = true;
    threadPanelScrim.hidden = true;
    threadPanel.classList.remove("open");
  }

  async function openThreadPanel(rootId) {
    if (!threadPanel) return;
    panelRootId = rootId;
    threadPanel.hidden = false;
    threadPanelScrim.hidden = false;
    requestAnimationFrame(() => threadPanel.classList.add("open"));
    threadPanelBody.innerHTML = `<p class="modal-loading">${escapeHtml(t("불러오는 중…"))}</p>`;
    const messages = threadMessages(
      await (await fetch(`/api/messages/${rootId}/thread`)).json());
    if (panelRootId !== rootId) return;
    threadPanelSub.textContent = (messages[0]?.body || "").slice(0, 48);
    threadPanelBody.innerHTML = "";
    for (const m of messages) threadPanelBody.appendChild(buildMessageEl(m));
    threadPanelBody.scrollTop = threadPanelBody.scrollHeight;
    threadPanelTa.value = "";
    threadPanelTa.focus();
  }

  $(".thread-panel-close")?.addEventListener("click", closeThreadPanel);
  threadPanelScrim?.addEventListener("click", closeThreadPanel);
  // 문서 수준 리스너는 dispose 에서 반드시 떼어낸다 — 탭을 닫아도 남으면
  // Esc 가 이미 사라진 패널을 건드린다.
  const onKeydown = (e) => {
    if (e.key === "Escape" && threadPanel && !threadPanel.hidden) closeThreadPanel();
  };
  document.addEventListener("keydown", onKeydown);
  teardown.push(() => document.removeEventListener("keydown", onKeydown));

  if (threadPanelTa) {
    bindEnterSubmit(threadPanelTa);
    threadPanelForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = threadPanelTa.value.trim();
      if (!body || !panelRootId) return;
      const rootId = panelRootId;
      const btn = threadPanelForm.querySelector("button");
      btn.disabled = true;
      try {
        const result = await sendMessage(body, rootId, "auto");
        if (result && panelRootId === rootId) {
          threadPanelBody.appendChild(buildMessageEl(result.userMsg));
          threadPanelBody.appendChild(buildMessageEl(result.agentMsg));
          threadPanelBody.scrollTop = threadPanelBody.scrollHeight;
          threadPanelTa.value = "";
        }
        if (result) {
          await refreshThreadCard(rootId);
          document.body.dispatchEvent(new Event("refresh-channels"));
        }
      } finally {
        btn.disabled = false;
      }
    });
  }

  for (const messages of THREADS) threadList.appendChild(buildThreadEl(messages));
  threadList.scrollTop = threadList.scrollHeight;

  const newThreadForm = $(".new-thread-form");
  const newThreadPrompt = $(".new-thread-prompt");
  bindEnterSubmit(newThreadPrompt);

  // ---- 컴포저(첨부·에이전트·모델·작업 위치) ------------------------------
  // app.js 의 일괄 마운트는 페이지 로드 때 한 번뿐이다 — 탭으로 나중에 열리는
  // 대화창은 여기서 직접 붙여야 첨부·드래그앤드롭·칩이 살아난다.
  const composer = window.mountComposer?.(newThreadForm) || null;
  const wsPicker = newThreadForm.querySelector(".workspace-picker");
  if (wsPicker) {
    // 작업 위치 목록은 직접 받아 온다 — 탭 패널은 innerHTML 로 붙어 htmx 가
    // 처리하지 않고, 전용 페이지에는 htmx 자체가 없다.
    fetch("/partials/workspaces")
      .then((r) => r.text())
      .then((html) => {
        wsPicker.innerHTML = html;
        composer?.initWsPicker(CHANNEL.workdir || "");
      })
      .catch(() => { /* 작업 위치를 못 받아도 대화는 된다 */ });
  }

  function pickedWorkdir() {
    return newThreadForm.querySelector(".ws-workdir")?.value || "";
  }

  // 고른 폴더는 이 방의 기본값으로 남는다 — 매번 다시 고르게 하지 않는다.
  async function rememberWorkdir(workdir) {
    if (workdir === (CHANNEL.workdir || "")) return;
    CHANNEL.workdir = workdir;
    const line = $(".channel-workdir");
    if (line) {
      line.hidden = !workdir;
      const code = line.querySelector("code");
      if (code) code.textContent = workdir;
    }
    await fetch(`/api/channels/${CHANNEL.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workdir }),
    }).catch(() => { /* 저장에 실패해도 이번 발화에는 이미 실려 갔다 */ });
  }

  // 파일은 먼저 올리고 경로만 메시지에 싣는다(POST /api/uploads).
  async function uploadAttachments() {
    const input = newThreadForm.querySelector(".composer-files");
    if (!input || !input.files.length) return [];
    const fd = new FormData();
    for (const f of input.files) fd.append("files", f);
    const res = await fetch("/api/uploads", { method: "POST", body: fd });
    if (!res.ok) {
      const detail = await res.json().then((d) => d.detail).catch(() => null);
      alert(detail || t("파일을 올리지 못했습니다"));
      return null;
    }
    return (await res.json()).map((f) => f.path);
  }

  newThreadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = newThreadPrompt.value.trim();
    if (!body) return;
    // DM 에는 에이전트 선택이 없다 — 상대가 정해져 있고 실행 CLI 는 그
    // 에이전트 설정이 정한다(서버가 채널 종류를 보고 라우팅한다).
    const provider = newThreadForm.querySelector(".composer-provider")?.value || "auto";
    const workdir = pickedWorkdir();
    const btn = newThreadForm.querySelector("button.send");
    btn.disabled = true;
    try {
      const attachments = await uploadAttachments();
      if (attachments === null) return;    // 업로드 실패 — 발화를 잃지 않게 멈춘다
      const result = await sendMessage(body, null, provider, { workdir, attachments });
      if (result) {
        const wrap = buildThreadEl([result.userMsg, result.agentMsg]);
        threadList.appendChild(wrap);
        wrap.scrollIntoView({ behavior: "smooth", block: "start" });
        document.body.dispatchEvent(new Event("refresh-channels"));
        newThreadPrompt.value = "";
        composer?.clearAttachments();
        rememberWorkdir(workdir);
      }
    } finally {
      btn.disabled = false;
    }
  });

  $(".channel-delete-btn")?.addEventListener("click", async () => {
    if (!confirm(t("이 채널과 모든 대화를 삭제할까요? 되돌릴 수 없습니다."))) return;
    await fetch(`/api/channels/${CHANNEL.id}`, { method: "DELETE" });
    // 탭 안에서 열려 있으면 탭만 닫고 홈에 머문다. 전용 페이지라면 홈으로.
    const handled = !document.body.dispatchEvent(new CustomEvent("orca-channel-deleted", {
      detail: { channelId: CHANNEL.id }, cancelable: true,
    }));
    if (!handled) window.location.href = "/";
  });

  // ---- 멤버(에이전트) 칩 · 에이전트 간 대화 토글 -------------------------
  // 칩을 누르면 입력창에 @슬러그를 넣는다(멘션 자동완성 대용).
  root.querySelectorAll(".channel-member-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const box = newThreadPrompt;
      const mention = "@" + chip.dataset.slug + " ";
      box.value = box.value ? box.value.replace(/\s*$/, " ") + mention : mention;
      box.focus();
    });
  });

  $(".member-add-select")?.addEventListener("change", async (e) => {
    const agentId = e.target.value;
    if (!agentId) return;
    await fetch(`/api/channels/${CHANNEL.id}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: Number(agentId) }),
    });
    // 칩 목록·선택지는 서버가 그린다 — 이 화면만 다시 받아 온다.
    document.body.dispatchEvent(new CustomEvent("orca-channel-reload", {
      detail: { channelId: CHANNEL.id },
    }));
    if (!root.closest(".orca-tab-panel")) window.location.reload();
  });

  $(".agent-chat-toggle")?.addEventListener("change", async (e) => {
    await fetch(`/api/channels/${CHANNEL.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_chat: e.target.checked }),
    });
  });

  return function dispose() {
    for (const es of streams) es.close();
    streams.clear();
    for (const off of teardown) off();
  };
};

// 전용 페이지(/channels/{id})는 DOM 이 준비되면 바로 붙인다.
// 중앙 탭은 home.js 의 로더가 조각을 받아 온 뒤 직접 호출한다.
document.addEventListener("DOMContentLoaded", () => {
  const page = document.querySelector(".channel-page .channel-view");
  if (page) window.mountChannelView(page);
});
