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
  // tabs: [{ tabId, kind, refId, title, status, dispose }]
  //   kind="job"     → /partials/job/{id} + job-view.js (SSE)
  //   kind="project" → /partials/board/{id} 비전보드 워크플로우
  // dispose는 그 탭이 연 SSE·폴링을 끊는다.
  const LS_TABS = "aos-home-tabs";
  let tabs = [];
  let activeTabId = null;

  function tabKey(kind, refId) { return `${kind}-${refId}`; }
  function panelId(tabId) { return `home-tab-panel-${tabId}`; }
  function tabButtonId(tabId) { return `home-tabbtn-${tabId}`; }
  function findTab(tabId) { return tabs.find((tb) => tb.tabId === tabId); }
  // 채팅 말풍선·상태 점은 잡 탭에만 해당한다.
  function activeJobId() {
    const tb = findTab(activeTabId);
    return tb && tb.kind === "job" ? tb.refId : null;
  }

  function saveTabs() {
    try {
      localStorage.setItem(LS_TABS, JSON.stringify({
        v: 2,
        tabs: tabs.map((tb) => ({ kind: tb.kind, refId: tb.refId })),
        activeTabId,
      }));
    } catch (e) { /* 저장 실패는 무시 — 탭은 메모리에서 계속 동작한다 */ }
  }

  function renderTabbar() {
    scrollTrack.innerHTML = "";
    tabs.forEach((tb) => {
      const active = tb.tabId === activeTabId;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "orca-tab" + (active ? " active" : "");
      btn.id = tabButtonId(tb.tabId);
      btn.dataset.tabId = tb.tabId;
      btn.dataset.tabKind = tb.kind;
      if (tb.kind === "job") btn.dataset.jobId = tb.refId;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.setAttribute("aria-controls", panelId(tb.tabId));
      btn.tabIndex = active ? 0 : -1;
      btn.title = tb.title;
      btn.innerHTML = `
        <span class="orca-tab-icon" aria-hidden="true">${LOADERS[tb.kind].icon}</span>
        <span class="orca-tab-title">${escapeHtml(tb.title)}</span>
        ${tb.status ? `<span class="orca-tab-status-dot status-${STATUS_MAP[tb.status] || "pending"}"></span>` : ""}
        <button type="button" class="orca-tab-close" data-tab-close="${tb.tabId}" aria-label="${tt("닫기")}">✕</button>
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
      p.classList.toggle("active", p.dataset.tabId === activeTabId);
    });
  }

  // ── 탭 종류별 로더 ────────────────────────────────────────────────────
  // mount()는 정리 함수(dispose)를 돌려준다 — 탭을 닫거나 다시 그릴 때 호출된다.
  const LOADERS = {
    job: {
      icon: "📋",
      defaultTitle: (id) => `${tt("작업")} #${id}`,
      probe: (id) => fetch(`/partials/job/${id}`, { method: "HEAD" }),
      async mount(panel, tb) {
        const res = await fetch(`/partials/job/${tb.refId}`);
        if (!res.ok) throw new Error(res.status === 404
          ? tt("작업이 삭제되었거나 서버가 아직 이 화면을 모릅니다(재시작 필요).")
          : `HTTP ${res.status}`);
        panel.innerHTML = await res.text();
        // 작업이 끝나면 조각을 다시 받아 최종 상태(배지·오류 배너)를 반영하고,
        // 우측 인스펙터도 새로 받는다(끝나야 후속 지시 컴포저가 생긴다).
        const dispose = window.mountJobView?.(panel.querySelector(".job-view"), () => {
          loadPanel(tb);
          if (tb.tabId === activeTabId) loadJobInspector(tb.refId);
        });
        // 대화 뷰이므로 최신 턴이 보이는 맨 아래에서 시작한다.
        panel.scrollTop = panel.scrollHeight;
        return dispose;
      },
    },
    project: {
      icon: "🗂",
      defaultTitle: (id) => `${tt("비전 보드")} #${id}`,
      probe: (id) => fetch(`/partials/board/${id}`, { method: "HEAD" }),
      async mount(panel, tb) {
        panel.innerHTML = `<div class="orca-board-host" id="board-${tb.refId}"
                                data-project-id="${tb.refId}"></div>`;
        const host = panel.firstElementChild;
        const url = () => `/partials/board/${tb.refId}?o=${window.boardOrientation
          ? window.boardOrientation() : (window.innerWidth < 768 ? "tb" : "lr")}`;
        async function reload() {
          const res = await fetch(url());
          if (res.ok) host.innerHTML = await res.text();
        }
        const first = await fetch(url());
        if (!first.ok) throw new Error(first.status === 404
          ? tt("프로젝트가 삭제되었습니다.") : `HTTP ${first.status}`);
        host.innerHTML = await first.text();
        // 다이어그램 조작(팬·줌·노드 선택·편집)은 board-editor.js가 맡는다.
        const unmount = window.mountBoardEditor?.(host);
        // 비활성 탭은 폴링하지 않는다 — 보드 탭 여러 개가 동시에 서버를 때리지 않게.
        const timer = setInterval(async () => {
          if (tb.tabId !== activeTabId) return;
          await reload().catch(() => {});
          window.mountBoardEditor?.(host);   // 조각이 갈렸으니 카메라·선택을 다시 맞춘다
        }, 2000);
        return () => { clearInterval(timer); unmount?.(); };
      },
    },
  };

  async function loadPanel(tb) {
    const panel = document.getElementById(panelId(tb.tabId));
    if (!panel) return;
    panel.innerHTML = `<p class="orca-muted">${tt("불러오는 중…")}</p>`;
    tb.dispose?.();
    tb.dispose = null;
    try {
      tb.dispose = await LOADERS[tb.kind].mount(panel, tb);
    } catch (e) {
      panel.innerHTML = `<div class="orca-tab-error">
        <p class="orca-muted">${escapeHtml(tt("불러올 수 없습니다.") + " " + e.message)}</p>
        <button type="button" class="btn-ghost" data-tab-retry="${tb.tabId}">${tt("다시 시도")}</button>
      </div>`;
    }
  }

  function openTab(spec) {
    const kind = spec.kind || "job";
    const refId = +spec.refId;
    const tabId = tabKey(kind, refId);
    let tb = findTab(tabId);
    if (!tb) {
      tb = { tabId, kind, refId, title: spec.title || LOADERS[kind].defaultTitle(refId),
             status: null, dispose: null };
      tabs.push(tb);
      const panel = document.createElement("section");
      panel.className = "orca-tab-panel";
      panel.id = panelId(tabId);
      panel.dataset.tabId = tabId;
      panel.dataset.tabKind = kind;
      if (kind === "job") panel.dataset.jobId = String(refId);
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tabButtonId(tabId));
      panelsRoot.appendChild(panel);
      loadPanel(tb);
    }
    activeTabId = tabId;
    renderTabbar();
    renderPanels();
    saveTabs();
    syncFromJobsTable();
    closeRailOverlay();
    // board-editor.js/task-inspector 등이 "지금 어떤 탭인지" 알 수 있게 알린다
    // (프로젝트 페이지의 board-workspace.js와 같은 이벤트 이름·detail 형태).
    document.body.dispatchEvent(new CustomEvent("orca-tab-activated", {
      detail: { tabId, kind, refId },
    }));
    return document.getElementById(panelId(tabId));
  }

  function closeTab(tabId) {
    const tb = findTab(tabId);
    if (!tb) return;
    const panel = document.getElementById(panelId(tabId));
    document.body.dispatchEvent(new CustomEvent("orca-tab-closing", {
      detail: { tabId, kind: tb.kind, refId: tb.refId, panel },
    }));
    tb.dispose?.();
    tabs = tabs.filter((x) => x.tabId !== tabId);
    panel?.remove();
    if (activeTabId === tabId) {
      activeTabId = tabs.length ? tabs[tabs.length - 1].tabId : null;
      const next = findTab(activeTabId);
      if (next) {
        document.body.dispatchEvent(new CustomEvent("orca-tab-activated", {
          detail: { tabId: next.tabId, kind: next.kind, refId: next.refId },
        }));
      }
    }
    renderTabbar();
    renderPanels();
    saveTabs();
    document.body.dispatchEvent(new CustomEvent("orca-tab-closed", {
      detail: { tabId, kind: tb.kind, refId: tb.refId },
    }));
  }

  panelsRoot.addEventListener("click", (e) => {
    const retry = e.target.closest("[data-tab-retry]");
    if (retry) {
      const tb = findTab(retry.dataset.tabRetry);
      if (tb) loadPanel(tb);
    }
  });

  // job-view.js의 "이어서 작업" 컴포저가 후속 작업을 만들면 그 자리에서 새 탭으로
  // 잇는다(이전 탭은 닫아 대화가 한 탭에서 계속되는 것처럼 보이게 한다).
  window.openJobTab = function (jobId, replaceJobId) {
    openTab({ kind: "job", refId: jobId });
    if (replaceJobId && +replaceJobId !== +jobId) closeTab(tabKey("job", +replaceJobId));
    document.body.dispatchEvent(new Event("refresh-jobs"));
  };

  // 비전보드 채팅·프로젝트 카드가 중앙에 보드 탭을 열 때 쓴다.
  window.openHomeTab = function (spec) { return openTab(spec); };

  scrollTrack.addEventListener("click", (e) => {
    const closeBtn = e.target.closest("[data-tab-close]");
    if (closeBtn) { closeTab(closeBtn.dataset.tabClose); return; }
    const tabBtn = e.target.closest(".orca-tab");
    if (tabBtn) openTab({ kind: tabBtn.dataset.tabKind, refId: tabBtn.dataset.tabId.split("-").pop() });
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
        // 표에 열이 없는 값은 조각이 데이터 속성으로 실어 보낸다(partials/jobs.html)
        title: tr.dataset.title || "",
        workdir: tr.dataset.workdir || "",
        created: tr.dataset.created || "",
      };
    }).filter((j) => j.id);
  }

  // 사용자가 이름을 붙였으면 그 이름이, 아니면 프롬프트가 세션 제목이다.
  function chatLabel(j) { return j.title || j.prompt; }
  function projectName(workdir) {
    return workdir ? workdir.split("/").filter(Boolean).pop() : tt("작업 위치 없음");
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

    // 열린 잡 탭의 상태 점·제목 갱신 (보드 탭은 #jobs와 무관하다)
    let dirty = false;
    tabs.filter((tb) => tb.kind === "job").forEach((tb) => {
      const j = byId.get(tb.refId);
      if (!j) return;
      const label = chatLabel(j);
      const title = label ? `#${j.id} ${label}` : `${tt("작업")} #${j.id}`;
      if (tb.status !== j.status || tb.title !== title) {
        tb.status = j.status;
        tb.title = title;
        dirty = true;
      }
    });
    if (dirty) renderTabbar();

    renderChatBubbles(jobs);
  }

  // ── 채팅 목록 — 검색 / 정렬(시간순·프로젝트별) / 이름 변경 ────────────
  const chatSearch = document.getElementById("home-chat-search");
  const LS_CHAT_SORT = "aos-home-chat-sort";
  const LS_CHAT_CLOSED = "aos-home-chat-closed-groups";
  let chatQuery = "";
  let chatSort = "time";
  // 이름을 고치는 동안에는 3초 폴링이 입력칸을 지우면 안 된다.
  let renamingId = null;

  function closedGroups() {
    try { return new Set(JSON.parse(localStorage.getItem(LS_CHAT_CLOSED)) || []); }
    catch (e) { return new Set(); }
  }
  function saveClosedGroups(set) {
    try { localStorage.setItem(LS_CHAT_CLOSED, JSON.stringify([...set])); }
    catch (e) { /* ignore */ }
  }

  function bubbleHtml(j, activeJob, withProject) {
    const project = withProject && j.workdir
      ? ` · 📁 ${escapeHtml(projectName(j.workdir))}` : "";
    return `<div class="orca-chat-row" data-chat-row="${j.id}">
      <button type="button" class="orca-chat-bubble${j.id === activeJob ? " active" : ""}" data-job-id="${j.id}">
        <span class="orca-chat-bubble-text">${escapeHtml(chatLabel(j))}</span>
        <span class="orca-chat-bubble-meta">
          <span class="orca-tab-status-dot status-${STATUS_MAP[j.status] || "pending"}"></span>
          ${escapeHtml(j.provider)}${project}
        </span>
      </button>
      <button type="button" class="orca-chat-rename" data-rename-job="${j.id}"
              title="${tt("이름 변경")}" aria-label="${tt("이름 변경")}">✎</button>
    </div>`;
  }

  // 최근 요청을 채팅 말풍선으로 되비춘다 — 클릭하면 중앙 탭으로 열린다.
  function renderChatBubbles(jobs) {
    if (!chatScroll || renamingId !== null) return;
    if (!jobs.length) {
      chatScroll.innerHTML = `<p class="orca-muted orca-chat-hint">${tt("아직 작업이 없습니다. 아래에서 첫 작업을 보내 보세요.")}</p>`;
      return;
    }
    const q = chatQuery.trim().toLowerCase();
    const hits = q
      ? jobs.filter((j) => [chatLabel(j), j.provider, j.workdir]
          .join(" ").toLowerCase().includes(q))
      : jobs;
    if (!hits.length) {
      chatScroll.innerHTML = `<p class="orca-muted orca-chat-hint">${tt("검색 결과가 없습니다")}</p>`;
      return;
    }
    const atBottom = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight < 40;
    const activeJob = activeJobId();
    // 서버는 최신순으로 준다 — 대화처럼 위→아래 작업시간순(오래된 것이 위)으로 세운다.
    const byTime = [...hits].sort((a, b) =>
      (a.created < b.created ? -1 : a.created > b.created ? 1 : a.id - b.id));

    if (chatSort === "time") {
      chatScroll.innerHTML = byTime.map((j) => bubbleHtml(j, activeJob, true)).join("");
      if (atBottom) chatScroll.scrollTop = chatScroll.scrollHeight;
      return;
    }

    // 프로젝트(작업 위치)별 묶음 — 최근에 움직인 프로젝트가 위로 온다.
    const groups = new Map();
    byTime.forEach((j) => {
      const key = j.workdir || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(j);
    });
    const closed = closedGroups();
    // 각 묶음은 이미 시간순이므로 마지막 항목이 그 프로젝트의 최근 작업이다.
    const last = (items) => items[items.length - 1];
    const ordered = [...groups.entries()].sort(
      (a, b) => (last(a[1]).created < last(b[1]).created ? 1 : -1));
    chatScroll.innerHTML = ordered.map(([key, items]) => `
      <div class="orca-chat-group${closed.has(key) ? "" : " open"}" data-chat-group="${escapeHtml(key)}">
        <button type="button" class="orca-chat-group-toggle">
          <svg class="chev" viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>
          <span class="orca-chat-group-name">${escapeHtml(projectName(key))}</span>
          <span class="orca-chat-group-count">${items.length}</span>
        </button>
        <div class="orca-chat-group-body">
          <div class="orca-chat-group-items">
            ${items.map((j) => bubbleHtml(j, activeJob, false)).join("")}
          </div>
        </div>
      </div>`).join("");
  }

  function setChatSort(mode) {
    chatSort = mode;
    document.querySelectorAll("[data-chat-sort]").forEach(
      (b) => b.classList.toggle("active", b.dataset.chatSort === mode));
    try { localStorage.setItem(LS_CHAT_SORT, mode); } catch (e) { /* ignore */ }
    renderChatBubbles(readJobs());
  }
  document.querySelectorAll("[data-chat-sort]").forEach((btn) => {
    btn.addEventListener("click", () => setChatSort(btn.dataset.chatSort));
  });
  try {
    const saved = localStorage.getItem(LS_CHAT_SORT);
    if (saved === "project" || saved === "time") setChatSort(saved);
  } catch (e) { /* ignore */ }

  chatSearch?.addEventListener("input", () => {
    chatQuery = chatSearch.value;
    renderChatBubbles(readJobs());
  });

  // 말풍선을 이름 입력칸으로 바꿔 그 자리에서 고친다.
  async function startRename(jobId) {
    const row = chatScroll?.querySelector(`[data-chat-row="${jobId}"]`);
    if (!row) return;
    const current = row.querySelector(".orca-chat-bubble-text")?.textContent.trim() || "";
    renamingId = jobId;
    row.innerHTML = `<input type="text" class="orca-chat-rename-input" maxlength="120">`;
    const input = row.firstElementChild;
    input.value = current;
    input.focus();
    input.select();
    let settled = false;
    const finish = async (save) => {
      if (settled) return;
      settled = true;
      const value = input.value.trim();
      renamingId = null;
      if (save && value !== current) {
        try {
          await fetch(`/jobs/${jobId}/rename`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({ title: value }),
          });
          // 다음 폴링(3초)을 기다리지 않도록 큐 조각의 값도 지금 맞춰 둔다.
          const tr = [...(jobsPanel?.querySelectorAll("tbody tr") || [])].find(
            (row) => +(row.querySelector(".job-id")?.textContent.trim() || 0) === jobId);
          if (tr) tr.dataset.title = value;
        } catch (e) { /* 실패하면 다음 폴링에서 원래 이름으로 돌아온다 */ }
      }
      syncFromJobsTable();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }

  chatScroll?.addEventListener("click", (e) => {
    const rename = e.target.closest("[data-rename-job]");
    if (rename) { startRename(+rename.dataset.renameJob); return; }
    const toggle = e.target.closest(".orca-chat-group-toggle");
    if (toggle) {
      const group = toggle.closest(".orca-chat-group");
      const opened = group.classList.toggle("open");
      const closed = closedGroups();
      opened ? closed.delete(group.dataset.chatGroup) : closed.add(group.dataset.chatGroup);
      saveClosedGroups(closed);
      return;
    }
    const bubble = e.target.closest(".orca-chat-bubble");
    if (bubble) openTab({ kind: "job", refId: bubble.dataset.jobId });
  });

  // 큐의 작업 링크는 페이지 이동 대신 중앙 탭으로 연다(딥링크 자체는 그대로 유효).
  jobsPanel?.addEventListener("click", (e) => {
    const link = e.target.closest('.job-prompt a[href^="/jobs/"]');
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    openTab({
      kind: "job", refId: link.getAttribute("href").split("/").pop(),
      title: `#${link.closest("tr").querySelector(".job-id").textContent.trim()} ${link.textContent.trim()}`,
    });
  });

  // 좌측 프로젝트 카드도 페이지 이동 대신 중앙 보드 탭으로 연다.
  const projectsPanel = document.getElementById("projects");
  projectsPanel?.addEventListener("click", (e) => {
    const link = e.target.closest('.project-card[href^="/projects/"]');
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    openTab({
      kind: "project", refId: link.getAttribute("href").split("/").pop(),
      title: link.querySelector(".project-title")?.textContent.trim(),
    });
  });

  // ── 작업 인스펙터(우측 레일) — 중앙 작업 탭의 후속 지시는 여기서 보낸다 ──
  // 중앙 탭은 결과·로그 전용이고(embed_followup=False), 편집은 레일이 맡는다.
  const jobInspector = document.getElementById("home-job-inspector");

  // 레일 헤더 문구 — '작업'은 탭 버튼이 없는 프로그램 전용 패널이라 여기서만 티가 난다.
  const RAIL_TITLES = { job: "작업 편집", task: "태스크", projects: "프로젝트" };

  function showRailPanel(name) {
    if (!rail) return;
    rail.querySelectorAll("[data-rail-tab]").forEach(
      (b) => b.classList.toggle("active", b.dataset.railTab === name));
    rail.querySelectorAll("[data-rail-panel]").forEach(
      (p) => { p.hidden = p.dataset.railPanel !== name; });
    const title = rail.querySelector(".orca-rail-title");
    if (title) title.textContent = tt(RAIL_TITLES[name] || "에이전트");
  }

  async function loadJobInspector(jobId) {
    if (!jobInspector) return;
    if (jobId == null) {
      jobInspector.innerHTML = "";
      if (!rail?.querySelector('[data-rail-panel="job"]')?.hidden) showRailPanel("chat");
      return;
    }
    try {
      const res = await fetch(`/partials/job/${jobId}/followup`);
      const html = res.ok ? await res.text() : "";
      jobInspector.innerHTML = html.trim()
        ? html
        : `<p class="orca-muted">${tt("아직 진행 중입니다 — 끝나면 여기서 이어서 지시할 수 있어요.")}</p>`;
      if (html.trim()) window.wireJobFollowUp?.(jobInspector, jobId);
      showRailPanel("job");
    } catch (e) { jobInspector.innerHTML = ""; }
  }

  document.body.addEventListener("orca-tab-activated", (e) => {
    const d = e.detail || {};
    if (d.kind === "job") loadJobInspector(d.refId);
    else loadJobInspector(null);
  });
  document.body.addEventListener("orca-tab-closed", () => {
    if (!tabs.length) loadJobInspector(null);
  });

  // ── 태스크 인스펙터 — 중앙 워크플로우의 노드를 클릭하면 우측 레일에 전용
  //    '태스크' 탭이 생기고 그 탭이 레일을 통째로 쓴다(프로젝트 목록·컴포저와 자리를
  //    나눠 쓰지 않는다). 탭 버튼은 태스크가 선택돼 있는 동안만 보인다.
  const inspectorBox = document.getElementById("home-task-inspector");
  const inspectorBack = document.getElementById("home-inspector-back");
  const taskTabBtn = document.getElementById("home-rail-task-tab");
  const taskInspector = inspectorBox && window.mountTaskInspector?.(inspectorBox, {
    onOpen: () => {
      if (taskTabBtn) taskTabBtn.hidden = false;
      showRailPanel("task");
      // 좁은 화면에서 레일은 오프캔버스다 — 열어 주지 않으면 편집창이 화면 밖에 뜬다.
      if (isNarrow()) openRailOverlay();
    },
    onClose: () => {
      if (taskTabBtn) taskTabBtn.hidden = true;
      if (!inspectorBox.hidden) showRailPanel("projects");
    },
    onBoardChanged: (projectId) => document.body.dispatchEvent(
      new CustomEvent("orca-refresh-board", { detail: { projectId } })),
  });
  inspectorBack?.addEventListener("click", () => taskInspector?.close());
  document.body.addEventListener("orca-task-selected", (e) => {
    const { id, projectId } = e.detail || {};
    if (id != null) taskInspector?.show(id, { projectId });
  });
  document.body.addEventListener("orca-task-context-close", () => taskInspector?.close());
  // 보드 탭을 닫거나 다른 종류의 탭으로 넘어가면 편집창도 접는다.
  document.body.addEventListener("orca-tab-activated", (e) => {
    if (e.detail?.kind !== "project") taskInspector?.close();
  });

  // ── 비전보드 채팅 — 목표를 보내면 중앙에 보드 탭이 열린다(페이지 이동 없음) ──
  const visionForm = document.getElementById("vision-composer");
  visionForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ta = visionForm.querySelector("textarea");
    const goal = ta.value.trim();
    if (!goal) return;
    const btn = visionForm.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await fetch("/projects", {
        method: "POST", body: new FormData(visionForm),
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { project_id } = await res.json();
      ta.value = "";
      // 폼이 그대로 남으므로 첨부 파일 칩을 비운다 (작업 위치·에이전트 선택은
      // 다음 보드에도 그대로 쓰는 게 자연스러워 유지한다)
      visionForm.__composer?.clearAttachments();
      openTab({ kind: "project", refId: project_id, title: goal.slice(0, 40) });
      document.body.dispatchEvent(new Event("refresh-projects"));
    } catch (err) {
      alert(tt("비전 보드를 만들지 못했습니다.") + " " + err.message);
    } finally { btn.disabled = false; }
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
    btn.addEventListener("click", () => showRailPanel(btn.dataset.railTab));
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
    // v1({jobIds, activeJobId}) → v2({tabs:[{kind,refId}], activeTabId}) 이관.
    if (saved && !saved.v && Array.isArray(saved.jobIds)) {
      saved = {
        v: 2,
        tabs: saved.jobIds.map((id) => ({ kind: "job", refId: id })),
        activeTabId: saved.activeJobId != null ? tabKey("job", saved.activeJobId) : null,
      };
    }
    if (!saved?.tabs?.length) { renderTabbar(); return; }
    const alive = [];
    for (const spec of saved.tabs) {
      if (!LOADERS[spec.kind]) continue;
      try {
        const res = await LOADERS[spec.kind].probe(spec.refId);
        if (res.ok) alive.push(spec);
      } catch (e) { /* 네트워크 실패 시엔 복원하지 않는다 */ }
    }
    alive.forEach((spec) => openTab(spec));
    const active = alive.find((s) => tabKey(s.kind, s.refId) === saved.activeTabId);
    if (active) openTab(active);
    if (!alive.length) { renderTabbar(); saveTabs(); }
  })();
})();
