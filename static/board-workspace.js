"use strict";

// 메인 워크스페이스 탭바 (Orca ADE식) — 모든 폭(데스크톱/태블릿/모바일)에서
// 같은 탭 상태·DOM을 공유하는 단일 스토어 모듈. 리사이즈로 브레이크포인트를
// 넘나들어도(예: 창을 좁혀 데스크톱→모바일) 이 모듈은 다시 초기화되지 않으므로
// 열린 탭·활성 탭·순서가 그대로 유지된다 — CSS만 폭별로 같은 DOM의 모양을
// 바꾼다(≥1024 가로 탭바 / 768~1023 전체폭 탭바 / <768 하단 세그먼트 컨트롤
// 뒤 칩 스트립). isDesktop()/isMobile()은 스와이프 제스처·단축키처럼 폭에
// 따라 상호작용 방식만 바꾸는 곳에서 쓰인다.
//
// 탭 데이터 접근은 전부 이 파일의 `api` 객체를 거친다: 서버에 실제로 있는
// /api/boards/{id}/tabs REST를 낙관적 갱신 + 백그라운드 동기화로 쓴다.
// 오프라인/네트워크 장애로 호출이 실패하면 콘솔에 남기고 로컬 상태(localStorage)
// 로만 계속 동작한다 — "항상 실패해서 조용히 폴백"이 아니라 정상 경로가 REST이고
// 장애 시에만 로컬로 내려가는 구조다. 이후 API가 바뀌어도 고칠 곳은 이 `api`
// 객체 하나뿐이다.
(function () {
  const workspace = document.getElementById("orca-workspace");
  const tabbar = document.getElementById("orca-tabbar");
  const scrollTrack = document.getElementById("orca-tabbar-scroll");
  const navPrev = document.querySelector('[data-tabbar-nav="prev"]');
  const navNext = document.querySelector('[data-tabbar-nav="next"]');
  const panelsRoot = document.getElementById("orca-tab-panels");
  const emptyState = document.getElementById("orca-tab-empty");
  if (!workspace || !tabbar || !scrollTrack || !panelsRoot) return;

  const boardId = workspace.dataset.boardId ? +workspace.dataset.boardId : null;
  const LS_KEY = boardId ? `orca-tabs-${boardId}` : null;

  const ICONS = { workflow: "🗺️", task: "📋", chat: "💬", flow: "🧭", artifact: "📦", diff: "±", preview: "👁" };
  const STATUS_MAP = {
    pending: "pending", queued: "pending", running: "running",
    done: "done", failed: "error", error: "error",
  };
  function tt(s) { return (typeof t === "function") ? t(s) : s; }

  // CSS 미디어쿼리와 같은 레이아웃 뷰포트로 잰다 — window.innerWidth는 iOS
  // Safari에서 핀치 줌에 따라 변해, 화면을 벌린 사이에만 티어가 뒤집힌다.
  const vpWidth = () => document.documentElement.clientWidth || window.innerWidth;
  function isDesktop() { return vpWidth() >= 1024; }
  function isMobile() { return vpWidth() < 768; }

  // tabs: [{ tabId: "canvas"|"task-42", kind, refId, title, status, closable, apiId }]
  let tabs = [];
  let activeTabId = null;

  // ── 서버 접근(단일 창구) ────────────────────────────────────────────────
  const api = {
    async list() {
      if (!boardId) throw new Error("no board id");
      const res = await fetch(`/api/boards/${boardId}/tabs`);
      if (!res.ok) throw new Error("list failed");
      return (await res.json()).tabs || [];
    },
    async create(title, kind, refId, status) {
      if (!boardId) throw new Error("no board id");
      const res = await fetch(`/api/boards/${boardId}/tabs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, kind, ref_id: refId, status }),
      });
      if (!res.ok) throw new Error("create failed");
      return (await res.json()).tab;
    },
    activate(apiId) {
      if (!boardId || !apiId) return;
      fetch(`/api/boards/${boardId}/tabs/${apiId}/activate`, { method: "POST" })
        .catch((err) => console.warn("orca tab activate sync failed", err));
    },
    close(apiId) {
      if (!boardId || !apiId) return;
      fetch(`/api/boards/${boardId}/tabs/${apiId}`, { method: "DELETE" })
        .catch((err) => console.warn("orca tab close sync failed", err));
    },
    reorder(tabIds) {
      if (!boardId) return;
      fetch(`/api/boards/${boardId}/tabs/order`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tab_ids: tabIds }),
      }).catch((err) => console.warn("orca tab reorder sync failed", err));
    },
  };

  // 복원 시 태스크가 실제로 아직 존재하는지 확인한다 — board_tabs 레코드가
  // 살아있어도(예: 다른 경로로 태스크가 삭제된 경우) 참조가 끊긴 탭은 조용히 버린다.
  async function taskStillExists(refId) {
    try {
      const res = await fetch(`/partials/task/${refId}`, { method: "HEAD" });
      return res.ok;
    } catch (e) {
      return false;
    }
  }

  function saveLocal() {
    if (!LS_KEY) return;
    const snapshot = tabs.map((tb) => ({ ...tb }));
    try { localStorage.setItem(LS_KEY, JSON.stringify({ tabs: snapshot, activeTabId })); } catch (e) { /* ignore */ }
  }
  function loadLocal() {
    if (!LS_KEY) return null;
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; }
  }

  // ── 탭/패널 DOM ────────────────────────────────────────────────────────

  function tabButtonId(tabId) { return `orca-tabbtn-${tabId}`; }
  function panelId(tabId) { return `orca-tab-panel-${tabId}`; }

  function renderTabbar() {
    scrollTrack.innerHTML = "";
    tabs.forEach((tb, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "orca-tab" + (tb.tabId === activeTabId ? " active" : "");
      btn.id = tabButtonId(tb.tabId);
      btn.dataset.tabId = tb.tabId;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", tb.tabId === activeTabId ? "true" : "false");
      btn.setAttribute("aria-controls", panelId(tb.tabId));
      btn.tabIndex = tb.tabId === activeTabId ? 0 : -1;
      btn.title = tb.title;
      btn.draggable = true;
      btn.innerHTML = `
        <span class="orca-tab-icon" aria-hidden="true">${ICONS[tb.kind] || "📄"}</span>
        <span class="orca-tab-title">${escapeHtml(tb.title)}</span>
        ${tb.status ? `<span class="orca-tab-status-dot status-${STATUS_MAP[tb.status] || tb.status}"></span>` : ""}
        ${tb.unread ? `<span class="orca-tab-unread-dot" title="${tt("새 로그")}"></span>` : ""}
        ${tb.closable !== false ? `<button type="button" class="orca-tab-close" data-tab-close="${tb.tabId}" aria-label="${tt("닫기")}">✕</button>` : ""}
      `;
      scrollTrack.appendChild(btn);
    });
    updateNavButtons();
    updateMobileSegCtl();
  }

  function updateNavButtons() {
    if (!navPrev || !navNext) return;
    const overflow = scrollTrack.scrollWidth > scrollTrack.clientWidth + 2;
    navPrev.hidden = !overflow || scrollTrack.scrollLeft <= 0;
    navNext.hidden = !overflow || scrollTrack.scrollLeft + scrollTrack.clientWidth >= scrollTrack.scrollWidth - 2;
  }
  scrollTrack.addEventListener("scroll", updateNavButtons);
  window.addEventListener("resize", updateNavButtons);
  navPrev?.addEventListener("click", () => scrollTrack.scrollBy({ left: -160, behavior: "smooth" }));
  navNext?.addEventListener("click", () => scrollTrack.scrollBy({ left: 160, behavior: "smooth" }));

  function escapeHtml(s) {
    return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function getOrCreatePanel(tabId, kind, refId) {
    let panel = document.getElementById(panelId(tabId));
    if (panel) return panel;
    panel = document.createElement("section");
    panel.className = "orca-tab-panel";
    panel.id = panelId(tabId);
    panel.dataset.tabId = tabId;
    panel.dataset.tabKind = kind;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tabButtonId(tabId));
    panelsRoot.appendChild(panel);
    // task 탭은 board-editor.js의 selectTask()가, workflow 탭은 정적 HTML(#board)이
    // 채운다. artifact/diff/preview는 별도 데이터 소스가 없으므로 태스크 상세
    // 조각(/partials/task/{id})에서 필요한 부분만 재사용해 채운다.
    if (["artifact", "diff", "preview"].includes(kind) && refId != null) {
      renderRefTabContent(panel, kind, refId);
    }
    return panel;
  }

  async function renderRefTabContent(panel, kind, refId) {
    panel.innerHTML = `<p class="orca-muted">${tt("불러오는 중…")}</p>`;
    let html;
    try {
      const res = await fetch(`/partials/task/${refId}`);
      if (!res.ok) throw new Error("fetch failed");
      html = await res.text();
    } catch (e) {
      panel.innerHTML = `<p class="orca-muted">${tt("불러올 수 없습니다.")}</p>`;
      return;
    }
    const scratch = document.createElement("div");
    scratch.innerHTML = html;
    if (kind === "artifact") {
      const artifact = scratch.querySelector(".task-artifact");
      const src = scratch.querySelector(".task-src");
      panel.innerHTML = "";
      if (artifact) {
        panel.appendChild(artifact);
      } else if (src) {
        const out = document.createElement("div");
        out.className = "md-body task-out";
        panel.appendChild(out);
        window.renderMarkdown?.(out, src.textContent);
      } else {
        panel.innerHTML = `<p class="orca-muted">${tt("아직 산출물이 없습니다.")}</p>`;
      }
    } else if (kind === "preview") {
      const src = scratch.querySelector(".task-src");
      panel.innerHTML = "";
      const out = document.createElement("div");
      out.className = "md-body task-out";
      panel.appendChild(out);
      window.renderMarkdown?.(out, src ? src.textContent : "");
    } else if (kind === "diff") {
      const src = scratch.querySelector(".task-src");
      const text = src ? src.textContent : "";
      panel.innerHTML = "";
      const pre = document.createElement("pre");
      pre.className = "orca-diff-view";
      pre.innerHTML = text.split("\n").map((line) => {
        const cls = line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-del" : "";
        return `<span class="${cls}">${escapeHtml(line)}</span>`;
      }).join("\n");
      panel.appendChild(pre);
    }
    if (window.htmx) window.htmx.process(panel);
  }

  function renderPanels() {
    panelsRoot.querySelectorAll(".orca-tab-panel").forEach((p) => {
      p.classList.toggle("active", p.dataset.tabId === activeTabId);
    });
    if (emptyState) emptyState.classList.toggle("active", tabs.length === 0);
    if (emptyState) emptyState.hidden = tabs.length !== 0;
  }

  // ── 탭 상태 조작 ──────────────────────────────────────────────────────

  function findTab(tabId) { return tabs.find((tb) => tb.tabId === tabId); }

  function setActive(tabId, { notify = false } = {}) {
    activeTabId = tabId;
    const activeTb = findTab(tabId);
    if (activeTb && activeTb.unread) activeTb.unread = false;
    renderTabbar();
    renderPanels();
    saveLocal();
    const tb = findTab(tabId);
    if (tb && tb.apiId) api.activate(tb.apiId);
    const activeBtn = document.getElementById(tabButtonId(tabId));
    if (activeBtn) activeBtn.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (notify && tb) {
      document.body.dispatchEvent(new CustomEvent("orca-tab-activated", {
        detail: { tabId: tb.tabId, kind: tb.kind, refId: tb.refId },
      }));
    }
  }

  function ensureTab({ tabId, kind, refId, title, status, closable = true }) {
    let tb = findTab(tabId);
    if (tb) {
      tb.title = title || tb.title;
      if (status) tb.status = status;
      renderTabbar();
      saveLocal();
      return tb;
    }
    tb = { tabId, kind, refId: refId ?? null, title: title || tabId, status: status || null, closable, apiId: null };
    tabs.push(tb);
    getOrCreatePanel(tabId, kind, refId ?? null);
    renderTabbar();
    renderPanels();
    saveLocal();
    api.create(tb.title, kind, refId ?? null, STATUS_MAP[status] || "pending")
      .then((rec) => { tb.apiId = rec.id; })
      .catch((err) => console.warn("orca tab create sync failed — staying local-only", err));
    return tb;
  }

  function closeTab(tabId) {
    const idx = tabs.findIndex((tb) => tb.tabId === tabId);
    if (idx === -1) return;
    const tb = tabs[idx];
    if (tb.apiId) api.close(tb.apiId);
    const panel = document.getElementById(panelId(tabId));
    if (panel) {
      // board-editor.js가 이 탭의 진행 로그 EventSource를 정리할 수 있도록
      // DOM에서 떼어내기 전에 패널 참조를 그대로 넘긴다.
      document.body.dispatchEvent(new CustomEvent("orca-tab-closing", { detail: { tabId, panel } }));
      panel.remove();
    }
    tabs.splice(idx, 1);
    document.body.dispatchEvent(new CustomEvent("orca-tab-closed", {
      detail: { tabId: tb.tabId, kind: tb.kind, refId: tb.refId },
    }));
    if (activeTabId === tabId) {
      const next = tabs[Math.max(0, idx - 1)] || tabs[0] || null;
      if (next) setActive(next.tabId, { notify: true });
      else { activeTabId = null; renderTabbar(); renderPanels(); saveLocal(); }
    } else {
      renderTabbar();
      renderPanels();
      saveLocal();
    }
  }

  function reorderTabs(fromTabId, toTabId, placeAfter) {
    const fromIdx = tabs.findIndex((tb) => tb.tabId === fromTabId);
    let toIdx = tabs.findIndex((tb) => tb.tabId === toTabId);
    if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return;
    const [moved] = tabs.splice(fromIdx, 1);
    toIdx = tabs.findIndex((tb) => tb.tabId === toTabId);
    tabs.splice(placeAfter ? toIdx + 1 : toIdx, 0, moved);
    renderTabbar();
    saveLocal();
    api.reorder(tabs.map((tb) => tb.apiId).filter((id) => id != null));
  }

  function closeOtherTabs(keepTabId) {
    const toClose = tabs.filter((tb) => tb.tabId !== keepTabId && tb.closable !== false).map((tb) => tb.tabId);
    toClose.forEach((tabId) => closeTab(tabId));
  }

  function closeTabsToRight(fromTabId) {
    const idx = tabs.findIndex((tb) => tb.tabId === fromTabId);
    if (idx === -1) return;
    const toClose = tabs.slice(idx + 1).filter((tb) => tb.closable !== false).map((tb) => tb.tabId);
    toClose.forEach((tabId) => closeTab(tabId));
  }

  // ── 드래그 앤 드롭 탭 순서 변경 ───────────────────────────────────────────
  let dragTabId = null;

  scrollTrack.addEventListener("dragstart", (e) => {
    const tabBtn = e.target.closest(".orca-tab");
    if (!tabBtn) { e.preventDefault(); return; }
    dragTabId = tabBtn.dataset.tabId;
    tabBtn.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", dragTabId); } catch (err) { /* ignore */ }
  });

  scrollTrack.addEventListener("dragover", (e) => {
    if (!dragTabId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const tabBtn = e.target.closest(".orca-tab");
    scrollTrack.querySelectorAll(".orca-tab.drag-over-before, .orca-tab.drag-over-after")
      .forEach((el) => el.classList.remove("drag-over-before", "drag-over-after"));
    if (!tabBtn || tabBtn.dataset.tabId === dragTabId) return;
    const rect = tabBtn.getBoundingClientRect();
    const before = e.clientX < rect.left + rect.width / 2;
    tabBtn.classList.add(before ? "drag-over-before" : "drag-over-after");
  });

  scrollTrack.addEventListener("drop", (e) => {
    if (!dragTabId) return;
    e.preventDefault();
    const tabBtn = e.target.closest(".orca-tab");
    scrollTrack.querySelectorAll(".orca-tab.drag-over-before, .orca-tab.drag-over-after")
      .forEach((el) => el.classList.remove("drag-over-before", "drag-over-after"));
    if (tabBtn && tabBtn.dataset.tabId !== dragTabId) {
      const rect = tabBtn.getBoundingClientRect();
      const before = e.clientX < rect.left + rect.width / 2;
      reorderTabs(dragTabId, tabBtn.dataset.tabId, !before);
    }
    dragTabId = null;
  });

  scrollTrack.addEventListener("dragend", () => {
    dragTabId = null;
    scrollTrack.querySelectorAll(".dragging, .drag-over-before, .drag-over-after")
      .forEach((el) => el.classList.remove("dragging", "drag-over-before", "drag-over-after"));
  });

  // ── 우클릭/롱탭 컨텍스트 메뉴 ────────────────────────────────────────────
  let ctxMenu = null;

  function closeContextMenu() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
    document.removeEventListener("click", closeContextMenu);
    document.removeEventListener("keydown", onCtxMenuKeydown);
  }
  function onCtxMenuKeydown(e) { if (e.key === "Escape") closeContextMenu(); }

  function openContextMenu(tabId, x, y) {
    closeContextMenu();
    const tb = findTab(tabId);
    if (!tb) return;
    ctxMenu = document.createElement("div");
    ctxMenu.className = "orca-tab-ctxmenu";
    ctxMenu.innerHTML = `
      <button type="button" data-action="close-others">${tt("다른 탭 닫기")}</button>
      <button type="button" data-action="close-right">${tt("오른쪽 탭 모두 닫기")}</button>
    `;
    document.body.appendChild(ctxMenu);
    const menuRect = ctxMenu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - menuRect.width - 8);
    const top = Math.min(y, window.innerHeight - menuRect.height - 8);
    ctxMenu.style.left = `${Math.max(8, left)}px`;
    ctxMenu.style.top = `${Math.max(8, top)}px`;
    ctxMenu.addEventListener("click", (e) => {
      e.stopPropagation();
      const action = e.target.closest("[data-action]")?.dataset.action;
      if (action === "close-others") closeOtherTabs(tabId);
      else if (action === "close-right") closeTabsToRight(tabId);
      closeContextMenu();
    });
    setTimeout(() => {
      document.addEventListener("click", closeContextMenu);
      document.addEventListener("keydown", onCtxMenuKeydown);
    }, 0);
  }

  scrollTrack.addEventListener("contextmenu", (e) => {
    const tabBtn = e.target.closest(".orca-tab");
    if (!tabBtn) return;
    e.preventDefault();
    openContextMenu(tabBtn.dataset.tabId, e.clientX, e.clientY);
  });

  // 롱탭(모바일): 스크롤/드래그 스와이프와 구분하기 위해 이동량이 작을 때만 발동.
  let longPressTimer = null, longPressStart = null, longPressTabId = null;
  let longPressFired = false;
  scrollTrack.addEventListener("touchstart", (e) => {
    const tabBtn = e.target.closest(".orca-tab");
    if (!tabBtn || e.touches.length !== 1) return;
    longPressTabId = tabBtn.dataset.tabId;
    longPressStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    longPressFired = false;
    longPressTimer = setTimeout(() => {
      longPressFired = true;
      openContextMenu(longPressTabId, longPressStart.x, longPressStart.y);
      longPressTimer = null;
    }, 550);
  }, { passive: true });
  scrollTrack.addEventListener("touchmove", (e) => {
    if (!longPressTimer || !longPressStart) return;
    const dx = e.touches[0].clientX - longPressStart.x;
    const dy = e.touches[0].clientY - longPressStart.y;
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  }, { passive: true });
  scrollTrack.addEventListener("touchend", () => {
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
  });

  // board-editor.js가 진행 로그에 새 줄이 도착할 때마다 쏘는 신호 — 지금 보고
  // 있는 탭이 아니면 뱃지만 켠다(로그 자체는 이미 백그라운드에서 계속 쌓이는 중).
  document.body.addEventListener("orca-task-log-activity", (e) => {
    const { taskId } = e.detail || {};
    if (taskId == null) return;
    const tb = findTab(`task-${taskId}`);
    if (tb && tb.tabId !== activeTabId && !tb.unread) {
      tb.unread = true;
      renderTabbar();
    }
  });

  function syncStatuses(entries) {
    // entries: [{ id, status }] — task id 기준. 배경 탭도 상태 점만 갱신한다.
    let changed = false;
    for (const e of entries) {
      const tb = findTab(`task-${e.id}`);
      if (tb && tb.status !== e.status) { tb.status = e.status; changed = true; }
    }
    if (changed) { renderTabbar(); saveLocal(); }
  }

  // ── 모바일(<768px) 세그먼트 컨트롤: 채팅 ↔ 워크스페이스 전체 화면 전환 ────
  const segCtl = document.getElementById("orca-mobile-segctl");
  const segBtns = segCtl ? [...segCtl.querySelectorAll("[data-seg]")] : [];
  const segDot = segCtl?.querySelector(".orca-mobile-segctl-dot");
  const segBadge = segCtl?.querySelector(".orca-mobile-segctl-badge");
  const segCount = segCtl?.querySelector(".orca-mobile-segctl-count");
  // 뷰만 바꾼다 — 히스토리는 건드리지 않는다(popstate 핸들러와 goTo*()가 담당).
  function applyMobileView(view) {
    document.body.dataset.orcaMobileView = view;
    segBtns.forEach((b) => {
      const active = b.dataset.seg === view;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  // 채팅 → 워크스페이스: 히스토리 엔트리 1개 추가(뒤로가기로 채팅 복귀 가능하도록).
  function goToWorkspace() {
    if (isMobile() && document.body.dataset.orcaMobileView !== "workspace") {
      history.pushState({ orcaMobileView: "workspace" }, "", location.href);
    }
    applyMobileView("workspace");
  }
  // 워크스페이스 → 채팅: 우리가 push한 엔트리가 있으면 back()으로 소비해서
  // 채팅 뷰 진입 시 히스토리 엔트리가 계속 쌓이지 않게 한다.
  function goToChat() {
    if (isMobile() && history.state && history.state.orcaMobileView === "workspace") {
      history.back();
      return;
    }
    applyMobileView("chat");
  }

  segBtns.forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.seg === "workspace") goToWorkspace();
    else goToChat();
  }));
  applyMobileView("chat");

  // 프로젝트 개요(목표·작업 위치·삭제)는 모바일에서 접어 둔다 — 제목 한 줄만
  // 남겨 워크스페이스가 화면을 최대한 쓰게 한다. 데스크톱은 펼친 채(open) 둔다.
  const brief = document.getElementById("project-brief");
  if (brief && isMobile()) brief.open = false;
  // 폭이 모바일 구간으로 들어올 때만 한 번 접는다 — 키보드 등으로 resize가
  // 연달아 떠도 사용자가 방금 펼친 개요를 다시 닫아 버리지 않게.
  let briefWasMobile = isMobile();
  window.addEventListener("resize", () => {
    const m = isMobile();
    if (brief && m && !briefWasMobile) brief.open = false;
    briefWasMobile = m;
  });

  window.addEventListener("popstate", (e) => {
    if (!isMobile()) return;
    applyMobileView(e.state && e.state.orcaMobileView === "workspace" ? "workspace" : "chat");
  });

  // 워크스페이스 뷰에서 아래로 스와이프 → 채팅 복귀. 활성 탭 패널이 이미 스크롤
  // 최상단일 때만 발동시켜 캔버스 팬/탭 내부 스크롤과 충돌하지 않게 한다.
  // 별도 애니메이션을 추가하지 않는 순간 전환이라 prefers-reduced-motion은
  // 항상 만족된다.
  let wsSwipeStartX = null, wsSwipeStartY = null;
  workspace.addEventListener("touchstart", (e) => {
    if (!isMobile() || document.body.dataset.orcaMobileView !== "workspace" || e.touches.length !== 1) {
      wsSwipeStartY = null;
      return;
    }
    const tb = findTab(activeTabId);
    const panel = tb ? document.getElementById(panelId(tb.tabId)) : null;
    if (panel && panel.scrollTop > 0) { wsSwipeStartY = null; return; }
    wsSwipeStartX = e.touches[0].clientX;
    wsSwipeStartY = e.touches[0].clientY;
  }, { passive: true });
  workspace.addEventListener("touchend", (e) => {
    if (wsSwipeStartY == null) return;
    const dy = e.changedTouches[0].clientY - wsSwipeStartY;
    const dx = e.changedTouches[0].clientX - wsSwipeStartX;
    wsSwipeStartX = wsSwipeStartY = null;
    if (dy > 70 && dy > Math.abs(dx) * 1.5) goToChat();
  }, { passive: true });

  function updateMobileSegCtl() {
    if (!segCtl) return;
    const running = tabs.filter((tb) => tb.status === "running").length;
    const unread = tabs.filter((tb) => tb.unread).length;
    if (segCount) segCount.textContent = tabs.length ? ` (${tabs.length})` : "";
    if (segDot) segDot.hidden = running === 0;
    if (segBadge) {
      segBadge.hidden = unread === 0;
      segBadge.textContent = String(unread);
    }
  }

  // 워크플로/캔버스 탭은 자체 포인터 제스처(핀치줌·팬)를 쓰므로 스와이프-탭전환은
  // 그 외 탭(태스크/산출물 등)의 콘텐츠 영역에서만 켠다 — 캔버스와 충돌 방지.
  let touchStartX = null, touchStartY = null;
  panelsRoot.addEventListener("touchstart", (e) => {
    if (!isMobile() || e.touches.length !== 1) { touchStartX = null; return; }
    const activeTb = findTab(activeTabId);
    if (!activeTb || activeTb.kind === "workflow") { touchStartX = null; return; }
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, { passive: true });
  panelsRoot.addEventListener("touchend", (e) => {
    if (touchStartX == null) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    touchStartX = touchStartY = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    const idx = tabs.findIndex((tb) => tb.tabId === activeTabId);
    if (idx === -1) return;
    const next = dx < 0 ? tabs[idx + 1] : tabs[idx - 1];
    if (next) setActive(next.tabId, { notify: true });
  }, { passive: true });

  // ── 클릭/키보드 ──────────────────────────────────────────────────────

  scrollTrack.addEventListener("click", (e) => {
    if (longPressFired) { longPressFired = false; e.preventDefault(); e.stopPropagation(); return; }
    const closeBtn = e.target.closest("[data-tab-close]");
    if (closeBtn) { e.stopPropagation(); closeTab(closeBtn.dataset.tabClose); return; }
    const tabBtn = e.target.closest(".orca-tab");
    if (tabBtn) setActive(tabBtn.dataset.tabId, { notify: true });
  });

  scrollTrack.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const idx = tabs.findIndex((tb) => tb.tabId === activeTabId);
    if (idx === -1) return;
    e.preventDefault();
    let next;
    if (e.key === "ArrowLeft") next = tabs[(idx - 1 + tabs.length) % tabs.length];
    else if (e.key === "ArrowRight") next = tabs[(idx + 1) % tabs.length];
    else if (e.key === "Home") next = tabs[0];
    else next = tabs[tabs.length - 1];
    if (next) {
      setActive(next.tabId, { notify: true });
      document.getElementById(tabButtonId(next.tabId))?.focus();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (!isDesktop() || !tabs.length) return;
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return;
    if (e.key >= "1" && e.key <= "9") {
      const idx = +e.key - 1;
      if (tabs[idx]) { e.preventDefault(); setActive(tabs[idx].tabId, { notify: true }); }
    } else if (e.key.toLowerCase() === "w") {
      if (activeTabId) { e.preventDefault(); closeTab(activeTabId); }
    }
  });

  // chat-rail.js가 메시지 응답에서 created_task_id를 발견하면 쏘는 신호(§4
  // 채팅→탭 자동 오픈). ensureTab이 tabId({kind}-{refId}) 기준으로 이미 열린
  // 탭이면 재사용하므로 같은 태스크에 대해 중복 탭이 생기지 않는다.
  document.body.addEventListener("orca-tab-opened", (e) => {
    const { kind, refId } = e.detail || {};
    if (kind !== "task" || refId == null) return;
    const tabId = `task-${refId}`;
    ensureTab({ tabId, kind: "task", refId, title: `#${refId}`, status: null, closable: true });
    setActive(tabId);
  });

  // ── 초기화 ────────────────────────────────────────────────────────────

  async function init() {
    // 캔버스 탭은 항상 첫 탭으로 존재한다 — DOM에 이미 렌더된 #board를 그대로 품는다.
    const canvasPanel = document.getElementById("orca-tab-panel-canvas");
    tabs = [{ tabId: "canvas", kind: "workflow", refId: null, title: tt("워크플로"), status: null, closable: true, apiId: null }];
    activeTabId = "canvas";
    if (canvasPanel) canvasPanel.dataset.tabId = "canvas";

    try {
      const serverTabs = await api.list();
      const canvasRec = serverTabs.find((r) => r.kind === "workflow");
      if (canvasRec) tabs[0].apiId = canvasRec.id;
      else {
        const created = await api.create(tt("워크플로"), "workflow", null, "pending");
        tabs[0].apiId = created.id;
      }
      // 이전에 열려 있던 태스크/진행흐름 탭은 로컬 스냅샷에서 복원한다(서버 쪽
      // 콘텐츠는 여기서 다시 fetch하지 않는다 — board-editor.js가 노드 클릭 시,
      // vision-flow.js가 탭 활성화 시 채운다). 태스크 탭은 참조된 태스크가 아직
      // 존재할 때만 복원하고, 이미 삭제된 태스크를 가리키는 탭은 조용히 버린다.
      const local = loadLocal();
      if (local && Array.isArray(local.tabs)) {
        for (const saved of local.tabs) {
          if (saved.tabId === "canvas" || findTab(saved.tabId)) continue;
          if (saved.kind === "task") {
            const rec = serverTabs.find((r) => r.id === saved.apiId);
            if (!rec || !(await taskStillExists(saved.refId))) continue;
            tabs.push({ ...saved, apiId: rec.id });
            getOrCreatePanel(saved.tabId, "task", saved.refId);
          } else if (saved.kind === "flow") {
            const rec = serverTabs.find((r) => r.id === saved.apiId);
            tabs.push({ ...saved, apiId: rec ? rec.id : null });
            getOrCreatePanel(saved.tabId, "flow", null);
          }
        }
        if (local.activeTabId && findTab(local.activeTabId)) activeTabId = local.activeTabId;
      }
    } catch (err) {
      // API 없음/실패 — 로컬 스냅샷만으로 복원(태스크 탭은 여전히 존재 확인한다)
      const local = loadLocal();
      if (local && Array.isArray(local.tabs)) {
        for (const saved of local.tabs) {
          if (saved.tabId === "canvas" || findTab(saved.tabId)) continue;
          if (saved.kind === "task" && !(await taskStillExists(saved.refId))) continue;
          tabs.push({ ...saved, apiId: null });
          getOrCreatePanel(saved.tabId, saved.kind, saved.refId ?? null);
        }
        if (local.activeTabId && findTab(local.activeTabId)) activeTabId = local.activeTabId;
      }
    }

    renderTabbar();
    renderPanels();
    saveLocal();
  }

  init();

  window.OrcaWorkspace = {
    isDesktop,
    openFlowTab() {
      const tabId = "flow";
      ensureTab({ tabId, kind: "flow", refId: null, title: tt("진행 흐름"), status: null, closable: true });
      setActive(tabId, { notify: true });
      return document.getElementById(panelId(tabId));
    },
    openTaskTab(taskId, meta) {
      const tabId = `task-${taskId}`;
      ensureTab({
        tabId, kind: "task", refId: taskId,
        title: (meta && meta.title) || `#${taskId}`,
        status: (meta && meta.status) || null,
        closable: true,
      });
      setActive(tabId);
      return document.getElementById(panelId(tabId));
    },
    getTaskPanelId: (taskId) => panelId(`task-${taskId}`),
    activeTabId: () => activeTabId,
    closeTab,
    syncStatuses,
  };
})();
