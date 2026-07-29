"use strict";

// ---- 사이드바: 새 채널 만들기 모달 (partial이 htmx로 자주 갈아끼워지므로
// 버튼은 위임 리스너로 잡는다) -------------------------------------------
const chModal = document.getElementById("channel-modal");

function openChannelModal() {
  if (!chModal) return;
  chModal.hidden = false;
  const titleEl = document.getElementById("channel-title");
  const topicEl = document.getElementById("channel-topic");
  const errEl = document.getElementById("channel-error");
  if (titleEl) titleEl.value = "";
  if (topicEl) topicEl.value = "";
  if (errEl) errEl.hidden = true;
  titleEl?.focus();
}
function closeChannelModal() { if (chModal) chModal.hidden = true; }

document.addEventListener("click", (e) => {
  if (e.target.closest("#channel-add-btn")) openChannelModal();
  if (e.target.closest("#channel-modal-close")) closeChannelModal();
  if (e.target === chModal) closeChannelModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && chModal && !chModal.hidden) closeChannelModal();
});

document.getElementById("channel-create-btn")?.addEventListener("click", async () => {
  const title = document.getElementById("channel-title").value.trim();
  const topic = document.getElementById("channel-topic").value.trim();
  const errEl = document.getElementById("channel-error");
  if (!title) {
    errEl.textContent = t("채널 이름을 입력하세요");
    errEl.hidden = false;
    return;
  }
  const btn = document.getElementById("channel-create-btn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, topic }),
    });
    if (!res.ok) {
      errEl.textContent = t("채널을 만들지 못했습니다");
      errEl.hidden = false;
      return;
    }
    const ch = await res.json();
    window.location.href = "/channels/" + ch.id;
  } finally {
    btn.disabled = false;
  }
});

// ---- 채널 상세 페이지: 쓰레드 렌더링·답장·실행 트레이스 스트리밍 ---------
const threadList = document.getElementById("thread-list");
if (threadList && typeof CHANNEL !== "undefined") {

  function escapeHtml(s) {
    return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function roleName(m) {
    return m.role === "user" ? t("나") : (m.author || m.provider || "agent");
  }

  function setBadge(row, status) {
    const badge = row.querySelector(".badge");
    if (badge) { badge.className = "badge badge-" + status; badge.textContent = status; }
    row.dataset.status = status;
  }

  function buildMessageEl(m, opts) {
    opts = opts || {};
    const row = document.createElement("div");
    row.className = "chat-turn msg-row " + (m.role === "user" ? "user" : "assistant");
    row.id = (opts.idPrefix || "msg-") + m.id;
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

    if (m.error) {
      const errEl = document.createElement("div");
      errEl.className = "msg-error";
      errEl.textContent = m.error;
      row.appendChild(errEl);
    }

    if (m.status === "queued" || m.status === "running") {
      const trace = document.createElement("div");
      trace.className = "msg-trace";
      trace.id = (opts.idPrefix ? "panel-trace-" : "trace-") + m.id;
      row.appendChild(trace);
      openTrace(m.id, trace.id);
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

  function threadProvider(messages) {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].provider) return messages[i].provider;
    }
    return "auto";
  }

  // 메인 채널 뷰: 각 쓰레드는 첫 질문/응답만 접어서 보여주고,
  // 답장은 우측 슬라이드아웃 패널에서 주고받는다 (Slack 쓰레드 패턴).
  function buildThreadEl(messages) {
    const root = messages[0];
    const wrap = document.createElement("div");
    wrap.className = "channel-thread";
    wrap.id = "thread-" + root.id;
    wrap.dataset.rootId = root.id;
    const chat = document.createElement("div");
    chat.className = "chat-thread";
    wrap.appendChild(chat);
    const replyBar = document.createElement("button");
    replyBar.type = "button";
    replyBar.className = "thread-reply-count";
    wrap.appendChild(replyBar);
    replyBar.addEventListener("click", () => openThreadPanel(root.id));
    updateThreadCard(wrap, messages);
    return wrap;
  }

  function updateThreadCard(wrap, messages) {
    const root = messages[0];
    const preview = messages.length > 1 ? messages.slice(0, 2) : messages;
    const chat = wrap.querySelector(".chat-thread");
    chat.innerHTML = "";
    for (const m of preview) chat.appendChild(buildMessageEl(m, { rootId: root.id }));
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

  async function sendMessage(body, parentId, provider) {
    const res = await fetch(`/api/channels/${CHANNEL.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body, provider: provider || "auto", parent_id: parentId || null }),
    });
    if (!res.ok) {
      alert(t("전송하지 못했습니다"));
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
    const wrap = document.getElementById("thread-" + rootId);
    if (!wrap) return;
    const messages = await (await fetch(`/api/messages/${rootId}/thread`)).json();
    updateThreadCard(wrap, messages);
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

  function openTrace(messageId, traceElId) {
    const trace = document.getElementById(traceElId || "trace-" + messageId);
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
      for (const row of document.querySelectorAll(`[data-msg-id="${messageId}"]`)) setBadge(row, status);
    });

    es.addEventListener("done", async () => {
      es.close();
      try {
        const fresh = await (await fetch(`/api/messages/${messageId}`)).json();
        for (const row of document.querySelectorAll(`[data-msg-id="${messageId}"]`)) {
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
        document.body.dispatchEvent(new Event("refresh-channels"));
      }
    });
  }

  // ---- 우측 쓰레드 답글 패널 ------------------------------------------
  const threadPanel = document.getElementById("thread-panel");
  const threadPanelScrim = document.getElementById("thread-panel-scrim");
  const threadPanelBody = document.getElementById("thread-panel-body");
  const threadPanelSub = document.getElementById("thread-panel-sub");
  const threadPanelForm = document.getElementById("thread-panel-form");
  const threadPanelTa = threadPanelForm.querySelector("textarea");
  let panelRootId = null;

  function closeThreadPanel() {
    panelRootId = null;
    threadPanel.hidden = true;
    threadPanelScrim.hidden = true;
    threadPanel.classList.remove("open");
  }

  async function openThreadPanel(rootId) {
    panelRootId = rootId;
    threadPanel.hidden = false;
    threadPanelScrim.hidden = false;
    requestAnimationFrame(() => threadPanel.classList.add("open"));
    threadPanelBody.innerHTML = `<p class="modal-loading">${escapeHtml(t("불러오는 중…"))}</p>`;
    const messages = await (await fetch(`/api/messages/${rootId}/thread`)).json();
    if (panelRootId !== rootId) return;
    threadPanelSub.textContent = (messages[0]?.body || "").slice(0, 48);
    threadPanelBody.innerHTML = "";
    for (const m of messages) threadPanelBody.appendChild(buildMessageEl(m, { idPrefix: "panel-msg-" }));
    threadPanelBody.scrollTop = threadPanelBody.scrollHeight;
    threadPanelTa.value = "";
    threadPanelTa.focus();
  }

  document.getElementById("thread-panel-close")?.addEventListener("click", closeThreadPanel);
  threadPanelScrim?.addEventListener("click", closeThreadPanel);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !threadPanel.hidden) closeThreadPanel();
  });

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
        threadPanelBody.appendChild(buildMessageEl(result.userMsg, { idPrefix: "panel-msg-" }));
        threadPanelBody.appendChild(buildMessageEl(result.agentMsg, { idPrefix: "panel-msg-" }));
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

  for (const messages of THREADS) threadList.appendChild(buildThreadEl(messages));
  threadList.scrollTop = threadList.scrollHeight;

  const newThreadForm = document.getElementById("new-thread-form");
  const newThreadPrompt = document.getElementById("new-thread-prompt");
  bindEnterSubmit(newThreadPrompt);
  newThreadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = newThreadPrompt.value.trim();
    if (!body) return;
    const provider = document.getElementById("new-thread-provider").value;
    const btn = newThreadForm.querySelector("button.send");
    btn.disabled = true;
    try {
      const result = await sendMessage(body, null, provider);
      if (result) {
        const wrap = buildThreadEl([result.userMsg, result.agentMsg]);
        threadList.appendChild(wrap);
        wrap.scrollIntoView({ behavior: "smooth", block: "start" });
        document.body.dispatchEvent(new Event("refresh-channels"));
      }
      newThreadPrompt.value = "";
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("channel-delete-btn")?.addEventListener("click", async () => {
    if (!confirm(t("이 채널과 모든 대화를 삭제할까요? 되돌릴 수 없습니다."))) return;
    await fetch(`/api/channels/${CHANNEL.id}`, { method: "DELETE" });
    window.location.href = "/";
  });
}
