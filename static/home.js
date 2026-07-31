"use strict";

// 홈 Orca 셸 — 중앙 멀티 탭 워크스페이스, 우측 채팅/세션 레일, 최하단 작업 큐 상태바.
//
// 데이터 소스는 단 하나다: 이미 3초마다 폴링되는 `#jobs`(/partials/jobs) 테이블.
// 상태바 요약 카운트, 채팅 탭의 최근 요청 말풍선, 열린 탭의 상태 점이 모두
// 그 스왑 한 번에서 파생되므로 새 폴링·새 엔드포인트를 추가하지 않는다.
(function () {
  const workspace = document.getElementById("home-workspace");
  const tabbar = document.getElementById("home-tabbar");
  const scrollTrack = document.getElementById("home-tabbar-scroll");
  const panelsRoot = document.getElementById("home-tab-panels");
  const emptyState = document.getElementById("home-tab-empty");
  if (!workspace || !scrollTrack || !panelsRoot) return;

  const navPrev = document.querySelector('#home-tabbar [data-tabbar-nav="prev"]');
  const navNext = document.querySelector('#home-tabbar [data-tabbar-nav="next"]');
  const jobsPanel = document.getElementById("jobs");
  const chatScroll = document.getElementById("home-chat-scroll");

  const STATUS_MAP = {
    queued: "pending", running: "running", rate_limited: "running",
    done: "done", failed: "error",
  };
  function tt(s) { return (typeof t === "function") ? t(s) : s; }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ── 중앙 탭 상태 ──────────────────────────────────────────────────────
  // tabs: [{ jobId, title, status, dispose }]  — dispose는 열린 SSE를 끊는다.
  const LS_TABS = "aos-home-tabs";
  let tabs = [];
  let activeJobId = null;

  function panelId(jobId) { return `home-tab-panel-${jobId}`; }
  function tabButtonId(jobId) { return `home-tabbtn-${jobId}`; }
  function findTab(jobId) { return tabs.find((tb) => tb.jobId === jobId); }

  function saveTabs() {
    try {
      localStorage.setItem(LS_TABS, JSON.stringify({
        jobIds: tabs.map((tb) => tb.jobId), activeJobId,
      }));
    } catch (e) { /* 저장 실패는 무시 — 탭은 메모리에서 계속 동작한다 */ }
  }

  function renderTabbar() {
    scrollTrack.innerHTML = "";
    tabs.forEach((tb) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "orca-tab" + (tb.jobId === activeJobId ? " active" : "");
      btn.id = tabButtonId(tb.jobId);
      btn.dataset.jobId = tb.jobId;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", tb.jobId === activeJobId ? "true" : "false");
      btn.setAttribute("aria-controls", panelId(tb.jobId));
      btn.tabIndex = tb.jobId === activeJobId ? 0 : -1;
      btn.title = tb.title;
      btn.innerHTML = `
        <span class="orca-tab-icon" aria-hidden="true">📋</span>
        <span class="orca-tab-title">${escapeHtml(tb.title)}</span>
        ${tb.status ? `<span class="orca-tab-status-dot status-${STATUS_MAP[tb.status] || "pending"}"></span>` : ""}
        <button type="button" class="orca-tab-close" data-tab-close="${tb.jobId}" aria-label="${tt("닫기")}">✕</button>
      `;
      scrollTrack.appendChild(btn);
    });
    if (tabbar) tabbar.hidden = tabs.length === 0;
    if (emptyState) emptyState.hidden = tabs.length !== 0;
    updateNavButtons();
  }

  function updateNavButtons() {
    if (!navPrev || !navNext) return;
    const overflow = scrollTrack.scrollWidth > scrollTrack.clientWidth + 2;
    navPrev.hidden = !overflow || scrollTrack.scrollLeft <= 0;
    navNext.hidden = !overflow
      || scrollTrack.scrollLeft + scrollTrack.clientWidth >= scrollTrack.scrollWidth - 2;
  }
  scrollTrack.addEventListener("scroll", updateNavButtons);
  window.addEventListener("resize", updateNavButtons);
  navPrev?.addEventListener("click", () => scrollTrack.scrollBy({ left: -160, behavior: "smooth" }));
  navNext?.addEventListener("click", () => scrollTrack.scrollBy({ left: 160, behavior: "smooth" }));

  function renderPanels() {
    panelsRoot.querySelectorAll(".orca-tab-panel").forEach((p) => {
      p.classList.toggle("active", +p.dataset.jobId === activeJobId);
    });
  }

  // 탭 내용 = /partials/job/{id} 조각 + job-view.js 마운트(마크다운 + SSE).
  async function loadPanel(tb) {
    const panel = document.getElementById(panelId(tb.jobId));
    if (!panel) return;
    panel.innerHTML = `<p class="orca-muted">${tt("불러오는 중…")}</p>`;
    let html;
    try {
      const res = await fetch(`/partials/job/${tb.jobId}`);
      if (!res.ok) throw new Error(res.status === 404
        ? tt("작업이 삭제되었거나 서버가 아직 이 화면을 모릅니다(재시작 필요).")
        : `HTTP ${res.status}`);
      html = await res.text();
    } catch (e) {
      panel.innerHTML = `<div class="orca-tab-error">
        <p class="orca-muted">${escapeHtml(tt("불러올 수 없습니다.") + " " + e.message)}</p>
        <button type="button" class="btn-ghost" data-tab-retry="${tb.jobId}">${tt("다시 시도")}</button>
      </div>`;
      return;
    }
    tb.dispose?.();
    panel.innerHTML = html;
    // 작업이 끝나면 조각을 다시 받아 최종 상태(배지·오류 배너)를 반영한다.
    tb.dispose = window.mountJobView?.(panel.querySelector(".job-view"), () => loadPanel(tb));
    // 대화 뷰이므로 최신 턴과 "이어서 작업" 컴포저가 보이는 맨 아래에서 시작한다.
    panel.scrollTop = panel.scrollHeight;
  }

  function openTab(jobId, title) {
    jobId = +jobId;
    let tb = findTab(jobId);
    if (!tb) {
      tb = { jobId, title: title || `${tt("작업")} #${jobId}`, status: null, dispose: null };
      tabs.push(tb);
      const panel = document.createElement("section");
      panel.className = "orca-tab-panel";
      panel.id = panelId(jobId);
      panel.dataset.jobId = String(jobId);
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tabButtonId(jobId));
      panelsRoot.appendChild(panel);
      loadPanel(tb);
    }
    activeJobId = jobId;
    renderTabbar();
    renderPanels();
    saveTabs();
    syncFromJobsTable();
    closeRailOverlay();
  }

  function closeTab(jobId) {
    jobId = +jobId;
    const tb = findTab(jobId);
    tb?.dispose?.();
    tabs = tabs.filter((x) => x.jobId !== jobId);
    document.getElementById(panelId(jobId))?.remove();
    if (activeJobId === jobId) activeJobId = tabs.length ? tabs[tabs.length - 1].jobId : null;
    renderTabbar();
    renderPanels();
    saveTabs();
  }

  panelsRoot.addEventListener("click", (e) => {
    const retry = e.target.closest("[data-tab-retry]");
    if (retry) {
      const tb = findTab(+retry.dataset.tabRetry);
      if (tb) loadPanel(tb);
    }
  });

  // job-view.js의 "이어서 작업" 컴포저가 후속 작업을 만들면 그 자리에서 새 탭으로
  // 잇는다(이전 탭은 닫아 대화가 한 탭에서 계속되는 것처럼 보이게 한다).
  window.openJobTab = function (jobId, replaceJobId) {
    openTab(jobId);
    if (replaceJobId && +replaceJobId !== +jobId) closeTab(replaceJobId);
    document.body.dispatchEvent(new Event("refresh-jobs"));
  };

  scrollTrack.addEventListener("click", (e) => {
    const closeBtn = e.target.closest("[data-tab-close]");
    if (closeBtn) { closeTab(closeBtn.dataset.tabClose); return; }
    const tabBtn = e.target.closest(".orca-tab");
    if (tabBtn) openTab(tabBtn.dataset.jobId);
  });

  // ── #jobs 테이블 → 상태바 카운트 / 채팅 말풍선 / 탭 상태 점 ──────────
  function readJobs() {
    if (!jobsPanel) return [];
    return [...jobsPanel.querySelectorAll("tbody tr")].map((tr) => {
      const link = tr.querySelector(".job-prompt a");
      const badge = tr.querySelector(".job-status .badge");
      return {
        id: +(tr.querySelector(".job-id")?.textContent.trim() || 0),
        prompt: link?.textContent.trim() || "",
        status: badge?.textContent.trim() || "",
        provider: tr.querySelector(".provider-name")?.textContent.trim() || "",
      };
    }).filter((j) => j.id);
  }

  const countsEl = document.getElementById("statusbar-counts");

  function syncFromJobsTable() {
    const jobs = readJobs();
    const byId = new Map(jobs.map((j) => [j.id, j]));

    // 상태바 요약 — 접혀 있어도 진행 상황이 한 줄로 보인다.
    if (countsEl) {
      const n = (s) => jobs.filter((j) => j.status === s).length;
      const running = n("running") + n("rate_limited");
      const parts = [];
      if (running) parts.push(`<span class="orca-sb-count is-running">${tt("실행")} ${running}</span>`);
      if (n("queued")) parts.push(`<span class="orca-sb-count is-queued">${tt("대기")} ${n("queued")}</span>`);
      if (n("failed")) parts.push(`<span class="orca-sb-count is-failed">${tt("실패")} ${n("failed")}</span>`);
      if (n("done")) parts.push(`<span class="orca-sb-count is-done">${tt("완료")} ${n("done")}</span>`);
      countsEl.innerHTML = parts.length
        ? parts.join("") : `<span class="orca-sb-count">${tt("작업 없음")}</span>`;
    }

    // 열린 탭의 상태 점·제목 갱신
    let dirty = false;
    tabs.forEach((tb) => {
      const j = byId.get(tb.jobId);
      if (!j) return;
      const title = j.prompt ? `#${j.id} ${j.prompt}` : `${tt("작업")} #${j.id}`;
      if (tb.status !== j.status || tb.title !== title) {
        tb.status = j.status;
        tb.title = title;
        dirty = true;
      }
    });
    if (dirty) renderTabbar();

    renderChatBubbles(jobs);
  }

  // 최근 요청을 채팅 말풍선으로 되비춘다 — 클릭하면 중앙 탭으로 열린다.
  function renderChatBubbles(jobs) {
    if (!chatScroll) return;
    if (!jobs.length) {
      chatScroll.innerHTML = `<p class="orca-muted orca-chat-hint">${tt("아직 작업이 없습니다. 아래에서 첫 작업을 보내 보세요.")}</p>`;
      return;
    }
    const atBottom = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight < 40;
    // 서버는 최신순(id DESC)으로 준다 — 대화처럼 위→아래 시간순으로 뒤집는다.
    chatScroll.innerHTML = [...jobs].reverse().map((j) => `
      <button type="button" class="orca-chat-bubble${j.id === activeJobId ? " active" : ""}" data-job-id="${j.id}">
        <span class="orca-chat-bubble-text">${escapeHtml(j.prompt)}</span>
        <span class="orca-chat-bubble-meta">
          <span class="orca-tab-status-dot status-${STATUS_MAP[j.status] || "pending"}"></span>
          ${escapeHtml(j.provider)}
        </span>
      </button>`).join("");
    if (atBottom) chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  chatScroll?.addEventListener("click", (e) => {
    const bubble = e.target.closest(".orca-chat-bubble");
    if (bubble) openTab(bubble.dataset.jobId);
  });

  // 큐의 작업 링크는 페이지 이동 대신 중앙 탭으로 연다(딥링크 자체는 그대로 유효).
  jobsPanel?.addEventListener("click", (e) => {
    const link = e.target.closest('.job-prompt a[href^="/jobs/"]');
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    openTab(link.getAttribute("href").split("/").pop(), `#${link.closest("tr").querySelector(".job-id").textContent.trim()} ${link.textContent.trim()}`);
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "jobs") syncFromJobsTable();
  });

  // ── 최하단 작업 큐 상태바 (접기/펼치기) ──────────────────────────────
  const statusbar = document.getElementById("home-statusbar");
  const sbToggle = document.getElementById("statusbar-toggle");
  const sbBody = document.getElementById("statusbar-body");
  const LS_SB = "aos-statusbar-open";

  function setStatusbar(open) {
    statusbar?.classList.toggle("is-open", open);
    if (sbBody) sbBody.hidden = !open;
    sbToggle?.setAttribute("aria-expanded", open ? "true" : "false");
    try { localStorage.setItem(LS_SB, open ? "1" : "0"); } catch (e) { /* ignore */ }
  }
  sbToggle?.addEventListener("click", () => setStatusbar(!statusbar.classList.contains("is-open")));
  try { setStatusbar(localStorage.getItem(LS_SB) === "1"); } catch (e) { setStatusbar(false); }

  // ── 우측 레일 — 탭 전환 / 접기 / 폭 조절 / 오프캔버스 ────────────────
  const rail = document.getElementById("home-rail");
  const railScrim = document.getElementById("home-rail-scrim");
  const LS_RAIL_W = "aos-home-rail-width";
  const LS_RAIL_COLLAPSED = "aos-home-rail-collapsed";

  function isNarrow() { return window.innerWidth < 1024; }

  rail?.querySelectorAll("[data-rail-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.railTab;
      rail.querySelectorAll("[data-rail-tab]").forEach((b) => b.classList.toggle("active", b === btn));
      rail.querySelectorAll("[data-rail-panel]").forEach((p) => {
        p.hidden = p.dataset.railPanel !== name;
      });
    });
  });

  const railToggle = document.getElementById("home-rail-toggle");

  function openRailOverlay() {
    // 좁은 화면에서 두 서랍이 동시에 열려 겹치지 않게 좌측 사이드바를 닫는다.
    document.body.classList.remove("nav-open");
    document.body.classList.add("home-rail-open");
    if (railScrim) railScrim.hidden = false;
    railToggle?.setAttribute("aria-expanded", "true");
  }
  function closeRailOverlay() {
    if (!isNarrow()) return;
    document.body.classList.remove("home-rail-open");
    if (railScrim) railScrim.hidden = true;
    railToggle?.setAttribute("aria-expanded", "false");
  }
  // 같은 버튼으로 열고 닫는다(모바일에서 손가락이 한 자리에 머문다).
  railToggle?.addEventListener("click", () => {
    if (document.body.classList.contains("home-rail-open")) closeRailOverlay();
    else openRailOverlay();
  });
  railScrim?.addEventListener("click", closeRailOverlay);
  document.getElementById("home-rail-close")?.addEventListener("click", closeRailOverlay);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeRailOverlay();
  });
  // 좌측 사이드바를 열 때도 우측 레일을 닫는다(app.js가 nav-open을 토글한다).
  document.getElementById("nav-toggle")?.addEventListener("click", () => {
    if (document.body.classList.contains("nav-open")) closeRailOverlay();
  });
  // 폭이 넓어지면 오프캔버스 상태를 남겨 두지 않는다.
  window.addEventListener("resize", () => {
    if (!isNarrow() && document.body.classList.contains("home-rail-open")) {
      document.body.classList.remove("home-rail-open");
      if (railScrim) railScrim.hidden = true;
      railToggle?.setAttribute("aria-expanded", "false");
    }
  });

  // 좁은 화면에서는 접기 버튼이 오프캔버스 닫기로 동작한다.
  document.getElementById("home-rail-collapse")?.addEventListener("click", () => {
    if (isNarrow()) { closeRailOverlay(); return; }
    const collapsed = !rail.classList.contains("is-collapsed");
    rail.classList.toggle("is-collapsed", collapsed);
    try { localStorage.setItem(LS_RAIL_COLLAPSED, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
  });
  try {
    if (localStorage.getItem(LS_RAIL_COLLAPSED) === "1") rail?.classList.add("is-collapsed");
    const w = +localStorage.getItem(LS_RAIL_W);
    if (w >= 280 && w <= 560) rail.style.width = `${w}px`;
  } catch (e) { /* ignore */ }

  // 우측 레일이므로 핸들을 왼쪽으로 끌 때 넓어진다.
  const handle = document.getElementById("home-rail-resize");
  handle?.addEventListener("pointerdown", (e) => {
    if (isNarrow() || rail.classList.contains("is-collapsed")) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = rail.getBoundingClientRect().width;
    rail.classList.add("is-resizing");
    document.body.classList.add("orca-resizing");
    const onMove = (ev) => {
      const next = Math.min(560, Math.max(280, startW - (ev.clientX - startX)));
      rail.style.width = `${next}px`;
    };
    const onUp = () => {
      rail.classList.remove("is-resizing");
      document.body.classList.remove("orca-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      try { localStorage.setItem(LS_RAIL_W, String(Math.round(rail.getBoundingClientRect().width))); } catch (err) { /* ignore */ }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  // ── 초기 복원 ────────────────────────────────────────────────────────
  // 새로고침해도 열려 있던 탭을 되살린다. 삭제된 작업의 탭은 조용히 버린다.
  (async function restoreTabs() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(LS_TABS) || "null"); } catch (e) { /* ignore */ }
    if (!saved?.jobIds?.length) { renderTabbar(); return; }
    const alive = [];
    for (const id of saved.jobIds) {
      try {
        const res = await fetch(`/partials/job/${id}`, { method: "HEAD" });
        if (res.ok) alive.push(id);
      } catch (e) { /* 네트워크 실패 시엔 복원하지 않는다 */ }
    }
    alive.forEach((id) => openTab(id));
    if (alive.includes(saved.activeJobId)) openTab(saved.activeJobId);
    if (!alive.length) { renderTabbar(); saveTabs(); }
  })();
})();
