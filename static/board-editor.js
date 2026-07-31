// 비전 보드 워크플로 캔버스 — n8n식 다이어그램 편집기.
//
// 서버가 SVG 전체를 렌더하고 모든 편집 액션이 같은 보드 조각을 되돌려 준다.
// 그래서 이 파일은 그래프 사본을 들고 있지 않는다 — "제스처 → POST → 새 조각"
// 루프와, 조각이 갈려도 살아남아야 하는 것들(뷰박스·선택)만 관리한다.
//
// 좌표계: SVG viewBox가 곧 카메라다. 팬/줌은 viewBox만 움직이고 노드 좌표는
// 건드리지 않으므로, 서버가 주는 좌표와 화면 좌표가 끝까지 일치한다.

(function () {
  "use strict";

  const NARROW = 768;        // 이 폭 미만이면 세로 흐름(tb)으로 재정렬한다
  const DRAG_SLOP = 4;       // 이보다 덜 움직이면 클릭(=선택)으로 본다

  // 폭 판정은 CSS 미디어쿼리와 같은 레이아웃 뷰포트에서 재야 한다.
  // window.innerWidth는 iOS Safari에서 핀치 줌을 따라 커진다 — 폰에서 다이어그램을
  // 읽으려고 화면을 벌리는 순간 경계를 넘겨, CSS는 모바일인데 그래프만 데스크톱
  // 가로 배치(lr)로 다시 그려지는 상태가 됐다. 경계값도 CSS 모바일 티어와 맞춘다.
  const vpWidth = () => document.documentElement.clientWidth || window.innerWidth;

  let view = null;           // {x, y, w, h} — null이면 다음 렌더에서 화면 맞춤
  let lastOrientation = null;
  let selTask = null, selStatus = null;
  let selEdge = null;        // 선택된 화살표 {from, to, el} — Delete/버튼으로 지운다
  let linkMode = null;       // {seq} — 터치 대안: '연결' 버튼을 누른 뒤 다음 노드 탭을 기다린다
  let busy = false;          // 제스처 진행 중 — 폴링을 막는다
  let paletteOpen = false;
  let drag = null, pinch = null;
  const pointers = new Map();
  let prevNodeStatus = new Map(); // task id → 직전 렌더에서의 status, 변화 감지용
  let restoredSelection = false; // 새로고침 직후 localStorage에서 선택 복원을 1회만 시도
  let orientationRetry = false;  // 방향 불일치 재요청 중 — 재귀 재요청을 막는다

  // --- 조회 헬퍼 ------------------------------------------------------------

  const board = () => document.getElementById("board");
  const canvas = () => document.querySelector("#board .graph-canvas");
  const svg = () => document.querySelector("#board .graph-svg");
  const editable = () => { const c = canvas(); return !!c && c.dataset.editable === "1"; };
  const orientation = () => { const c = canvas(); return c ? c.dataset.orientation : "lr"; };
  const tr = (s) => (window.t ? window.t(s) : s);

  // 서버에 어떤 방향으로 그려 달라고 할지 — htmx의 hx-vals가 이 함수를 부른다.
  function boardOrientation() {
    return vpWidth() < NARROW ? "tb" : "lr";
  }
  window.boardOrientation = boardOrientation;

  // --- 카메라 --------------------------------------------------------------

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function fitView() {
    const s = svg(), c = canvas();
    const w = +s.dataset.w, h = +s.dataset.h;
    const box = c.getBoundingClientRect();
    const ar = box.height > 0 ? box.width / box.height : w / h;
    // 그래프 전체가 들어가도록 빠듯한 쪽에 맞추고 약간의 여백을 준다
    let vw = w, vh = h;
    if (vw / vh > ar) vh = vw / ar; else vw = vh * ar;
    vw *= 1.04; vh *= 1.04;
    // 좁은 화면에서는 "전체가 다 들어오게"를 포기한다 — 세로로 긴 흐름을 다
    // 넣으려면 0.4배쯤 축소돼 노드 글자가 5px 수준으로 짜부라진다. 대신 모바일
    // 레이아웃(한 줄 세로 스택)은 캔버스 폭 자체가 폰 화면에 맞춰 나오므로,
    // 폭에 딱 맞추면 노드 글자가 1:1보다 크게 보여 확대 없이 읽힌다. 세로는
    // 팬으로 따라가고, 전체 조망은 축소 버튼/핀치로 사용자가 고르게 한다.
    if (box.width > 0 && vpWidth() < NARROW) {
      vw = w;
      vh = vw / ar;
    } else if (box.width > 0 && vw < box.width) {
      // 1:1보다 크게는 확대하지 않는다 — 노드 두어 개짜리 그래프가 우스꽝스럽게
      // 부풀지 않도록. (축소는 그대로 허용 — 큰 그래프는 다 들어와야 한다)
      vh *= box.width / vw;
      vw = box.width;
    }
    // 가로는 가운데, 세로는 거의 위쪽 정렬 — 남는 높이를 위아래로 반씩 나누면
    // 좁은 화면에서 그래프가 캔버스 한가운데에 떠 보이고 아래는 시트에 가린다.
    return { x: (w - vw) / 2, y: Math.min(0, (h - vh) * 0.15), w: vw, h: vh };
  }

  function applyView() {
    const s = svg();
    if (!s) return;
    if (!view) view = fitView();
    s.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  }

  function toSvgXY(clientX, clientY) {
    const s = svg();
    const pt = s.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    return pt.matrixTransform(s.getScreenCTM().inverse());
  }

  const toSvg = (e) => toSvgXY(e.clientX, e.clientY);

  // at(SVG 좌표)을 화면에 고정한 채 확대/축소한다.
  function zoomAt(factor, at) {
    if (!view) return;
    const nw = clamp(view.w * factor, 200, 12000);
    const f = nw / view.w;
    view.x = at.x - (at.x - view.x) * f;
    view.y = at.y - (at.y - view.y) * f;
    view.w = nw;
    view.h *= f;
    applyView();
  }

  const viewCenter = () => ({ x: view.x + view.w / 2, y: view.y + view.h / 2 });

  // 제스처 시작 시점(d.startX/vx)을 기준으로 카메라를 끌고 다닌다.
  function panBy(d, e) {
    if (!view) return;
    const box = canvas().getBoundingClientRect();
    const scale = box.width > 0 ? view.w / box.width : 1;
    view.x = d.vx - (e.clientX - d.startX) * scale;
    view.y = d.vy - (e.clientY - d.startY) * scale;
    applyView();
  }

  // --- 노드·엣지 기하 (서버의 layout_graph와 같은 공식) ----------------------

  function nodeSize(g) {
    const box = g.querySelector(".node-box");
    return { w: +box.getAttribute("width"), h: +box.getAttribute("height") };
  }

  function portPoint(g, kind, x, y) {
    const { w, h } = nodeSize(g);
    if (orientation() === "tb") {
      return kind === "out" ? { x: x + w / 2, y: y + h } : { x: x + w / 2, y: y };
    }
    return kind === "out" ? { x: x + w, y: y + h / 2 } : { x: x, y: y + h / 2 };
  }

  function anchor(seq, kind, override) {
    const g = svg().querySelector(`.graph-node[data-seq="${seq}"]`);
    if (!g) return { x: 0, y: 0 };
    return portPoint(g, kind,
      override ? override.x : +g.dataset.x,
      override ? override.y : +g.dataset.y);
  }

  function edgePath(a, b) {
    if (orientation() === "tb") {
      const my = (a.y + b.y) / 2;
      return `M ${a.x} ${a.y} C ${a.x} ${my}, ${b.x} ${my}, ${b.x} ${b.y}`;
    }
    const mx = (a.x + b.x) / 2;
    return `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
  }

  // 노드를 끄는 동안 연결선이 따라오게 한다 (서버 왕복 없이 로컬로).
  function trackEdges(seq, x, y) {
    svg().querySelectorAll(".graph-edge-g").forEach((eg) => {
      const from = +eg.dataset.from, to = +eg.dataset.to;
      if (from !== seq && to !== seq) return;
      const d = edgePath(anchor(from, "out", from === seq ? { x, y } : null),
                         anchor(to, "in", to === seq ? { x, y } : null));
      eg.querySelectorAll("path").forEach((p) => p.setAttribute("d", d));
    });
  }

  const depsOf = (seq) =>
    [...svg().querySelectorAll(`.graph-edge-g[data-to="${seq}"]`)]
      .map((eg) => +eg.dataset.from);

  // fromSeq에서 기존 화살표(from→to 방향)만 따라가서 toSeq에 닿을 수 있는지 —
  // 닿는다면 toSeq→fromSeq 새 화살표는 순환을 만든다.
  function reachable(fromSeq, toSeq) {
    const seen = new Set();
    const stack = [fromSeq];
    while (stack.length) {
      const cur = stack.pop();
      if (cur === toSeq) return true;
      if (seen.has(cur)) continue;
      seen.add(cur);
      svg().querySelectorAll(`.graph-edge-g[data-from="${cur}"]`)
        .forEach((eg) => stack.push(+eg.dataset.to));
    }
    return false;
  }

  // --- 서버 왕복 -----------------------------------------------------------

  function flash(msg, kind) {
    const bar = document.querySelector("#board .graph-toolbar");
    if (!bar) return;
    let el = bar.querySelector(".graph-flash");
    if (!el) {
      el = document.createElement("span");
      el.className = "graph-flash";
      bar.prepend(el);
    }
    el.classList.toggle("ok", kind === "ok");
    // 서버 검증 메시지는 한국어 원문으로 온다 — 같은 카탈로그로 번역해 띄운다
    el.textContent = tr(msg);
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.remove(), 5000);
  }

  async function post(url, data) {
    const fd = new FormData();
    fd.append("o", boardOrientation());
    for (const [k, v] of Object.entries(data || {})) {
      if (Array.isArray(v)) v.forEach((x) => fd.append(k, x));
      else fd.append(k, v);
    }
    let res;
    try {
      res = await fetch(url, { method: "POST", body: fd });
    } catch (err) {
      flash(tr("서버에 연결하지 못했습니다"));
      return false;
    }
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (err) { /* 본문 없음 */ }
      // 되돌리기가 먼저다 — 실패한 낙관적 이동을 서버 상태로 되감고 나서
      // 배너를 띄운다(순서가 바뀌면 새 조각이 배너를 지워 버린다).
      await reloadBoard();
      flash(msg);
      return false;
    }
    board().innerHTML = await res.text();
    afterBoardRender();
    refreshPanel();
    return true;
  }

  async function reloadBoard() {
    const b = board();
    if (!b) return;
    const res = await fetch(`${b.getAttribute("hx-get")}?o=${boardOrientation()}`);
    if (!res.ok) return;
    b.innerHTML = await res.text();
    afterBoardRender();
  }

  // --- 태스크 사이드바 컨텍스트 --------------------------------------------
  //
  // window.OrcaWorkspace(board-workspace.js)가 있으면 폭에 상관없이 태스크
  // 상세를 메인 탭바의 탭으로 연다 — 데스크톱은 사이드바 옆 탭바로, 태블릿은
  // 전체 폭 탭바로, 모바일은 하단 세그먼트 컨트롤의 "작업" 화면 안 칩 스트립
  // 탭바로(board-workspace.js가 같은 탭 상태를 물고 폭별로 모양만 바꾼다).
  // OrcaWorkspace 스크립트가 로드되지 못한 경우에만 예전 #task-detail
  // 인라인/바텀시트로 폴백한다.

  const taskPanel = () => document.getElementById("task-detail");
  const taskBackdrop = () => document.getElementById("task-detail-backdrop");
  const isTabWorkspace = () => !!window.OrcaWorkspace;

  // 선택한 태스크를 프로젝트별로 localStorage에 남겨, 새로고침 후에도 인스펙터가
  // 같은 노드를 다시 열어둔다.
  function selKey() {
    const pid = canvas()?.dataset.project;
    return pid ? `orca-board-sel-${pid}` : null;
  }

  function setPanelOpen(open) {
    const bd = taskBackdrop();
    if (bd) bd.hidden = !open;
    document.body.classList.toggle("task-panel-open", open);
  }

  // 현재 편집/조회 대상 태스크 상세의 루트 엘리먼트 — 데스크톱에서는 활성 탭
  // 패널(태스크마다 독립된 콘텐츠를 들고 있음), 그 아래 폭에서는 #task-detail.
  function currentTaskRoot() {
    if (isTabWorkspace()) {
      return document.querySelector('.orca-tab-panel[data-tab-kind="task"].active') || taskPanel();
    }
    return taskPanel();
  }

  // 클릭 이벤트가 어느 태스크 상세 루트 안에서 일어났는지(데스크톱은 탭마다
  // 패널이 따로 있으므로 "활성 탭"이 아니라 "이벤트가 실제로 발생한 탭"을 써야 한다).
  function taskRootOf(el) {
    return (el.closest && el.closest('.orca-tab-panel[data-tab-kind="task"]')) || taskPanel();
  }

  function taskContainerFor(id, node) {
    if (isTabWorkspace()) {
      return window.OrcaWorkspace.openTaskTab(id, {
        title: node ? node.dataset.title : `#${id}`,
        status: node ? node.dataset.status : null,
      });
    }
    return taskPanel();
  }

  // --- 진행 로그 스트리밍 ----------------------------------------------------
  //
  // task_detail.html이 실행 중/대기 중 태스크에 <div class="task-log"
  // data-job-id> 블록을 심어 두면, 여기서 기존 /jobs/{id}/stream SSE를 그대로
  // 구독해 라인 단위로 그린다(새 전송 방식 도입 없음 — chat-rail.js의 실행
  // 로그 탭과 같은 엔드포인트). 패널(탭이든 모바일 시트든) 노드 자체는 상태가
  // 바뀔 때만 갈아끼워지므로, 탭이 비활성이어도 이 EventSource는 계속 살아
  // 있어 로그가 백그라운드에서 누적된다.
  const MAX_LOG_LINES = 2000;
  const LOG_SCROLL_EDGE = 32; // 이 픽셀 안쪽이면 "바닥 근처"로 본다
  const taskLogStreams = new Map(); // panel 엘리먼트 → 스트림 상태

  function stopTaskLog(panel) {
    if (!panel) return;
    const state = taskLogStreams.get(panel);
    if (!state) return;
    state.es.close();
    taskLogStreams.delete(panel);
  }

  function initTaskLog(panel) {
    const box = panel.querySelector(".task-log[data-job-id]");
    if (!box) return;
    const jobId = box.dataset.jobId;
    const stream = box.querySelector(".task-log-stream");
    const jumpBtn = box.querySelector(".task-log-jump");
    const offlineEl = box.querySelector(".task-log-offline");
    if (!stream || !jobId) return;

    const state = { lines: [], tailEl: null, tailText: "", autoStick: true, es: null };
    taskLogStreams.set(panel, state);

    function scrollToBottom() {
      stream.scrollTop = stream.scrollHeight;
      if (jumpBtn) jumpBtn.hidden = true;
    }

    function trimLines() {
      while (state.lines.length > MAX_LOG_LINES) {
        state.lines.shift().remove();
      }
    }

    function pushLine(text) {
      state.tailEl = null; // 방금 완결된 줄은 더 이상 이어 쓰지 않는다
      const div = document.createElement("div");
      div.className = "task-log-line";
      div.textContent = text;
      stream.appendChild(div);
      state.lines.push(div);
    }

    function updateTail() {
      if (!state.tailEl) {
        state.tailEl = document.createElement("div");
        state.tailEl.className = "task-log-line";
        stream.appendChild(state.tailEl);
        state.lines.push(state.tailEl);
      }
      state.tailEl.textContent = state.tailText;
    }

    function notifyActivity() {
      if (!isTabWorkspace() || panel.dataset.tabId === window.OrcaWorkspace.activeTabId()) return;
      const taskId = panel.querySelector(".task-panel")?.dataset.task;
      if (taskId == null) return;
      document.body.dispatchEvent(new CustomEvent("orca-task-log-activity", { detail: { taskId } }));
    }

    function appendChunk(text) {
      state.tailText += text;
      const parts = state.tailText.split("\n");
      state.tailText = parts.pop();
      for (const line of parts) pushLine(line);
      if (state.tailText) updateTail();
      trimLines();
      if (state.autoStick) scrollToBottom();
      else if (jumpBtn) jumpBtn.hidden = false;
      notifyActivity();
    }

    stream.addEventListener("scroll", () => {
      const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < LOG_SCROLL_EDGE;
      state.autoStick = nearBottom;
      if (jumpBtn) jumpBtn.hidden = nearBottom;
    });
    jumpBtn?.addEventListener("click", () => { state.autoStick = true; scrollToBottom(); });

    const es = new EventSource(`/jobs/${jobId}/stream`);
    state.es = es;
    es.onmessage = (e) => {
      if (offlineEl) offlineEl.hidden = true;
      appendChunk(JSON.parse(e.data));
    };
    es.addEventListener("status", () => {
      if (offlineEl) offlineEl.hidden = true;
      es.close();
    });
    // 백엔드 장애/오프라인으로 연결이 끊기면 브라우저가 자동 재연결을
    // 시도한다 — 사용자에게는 그 사이 "멈춘 게 아니다"만 알려준다.
    es.addEventListener("error", () => { if (offlineEl) offlineEl.hidden = false; });
  }

  // 데스크톱 탭바에서 탭이 실제로 DOM에서 떨어져 나가기 직전에 정리한다
  // (board-workspace.js의 closeTab이 panel.remove() 하기 전에 쏘는 신호).
  document.body.addEventListener("orca-tab-closing", (e) => {
    stopTaskLog(e.detail && e.detail.panel);
  });

  // 탭을 다시 활성화했을 때 — 상태가 안 바뀌어 selectTask를 다시 타지 않는
  // 경우, 비활성 상태에서 쌓인 로그로 스크롤이 바닥에 붙어 있어야 한다.
  document.body.addEventListener("orca-tab-activated", (e) => {
    const panel = document.getElementById(window.OrcaWorkspace.getTaskPanelId(e.detail?.refId));
    const state = panel && taskLogStreams.get(panel);
    if (state && state.autoStick) {
      const stream = panel.querySelector(".task-log-stream");
      if (stream) requestAnimationFrame(() => { stream.scrollTop = stream.scrollHeight; });
    }
  });

  async function selectTask(id) {
    selTask = id;
    const node = document.querySelector(`.graph-node[data-task="${id}"]`);
    selStatus = node ? node.dataset.status : null;
    highlightSel();
    const key = selKey();
    if (key) localStorage.setItem(key, String(id));
    // 좌측 레일이 태스크 상세·편집·전용 대화로 전환한다. 중앙은 계속 캔버스다.
    document.body.dispatchEvent(new CustomEvent("orca-task-selected", {
      detail: {
        id, status: selStatus,
        jobId: node && node.dataset.job ? +node.dataset.job : null,
      },
    }));
  }
  window.selectTask = selectTask;

  function closePanel() {
    selTask = null;
    selStatus = null;
    highlightSel();
    const key = selKey();
    if (key) localStorage.removeItem(key);
    document.body.dispatchEvent(new CustomEvent("orca-task-context-close"));
  }
  window.closeTaskPanel = closePanel;

  // 워크스페이스 탭바에서 직접 탭을 닫거나 전환했을 때(board-editor.js의 selectTask를
  // 거치지 않은 경로) 선택 상태를 맞춘다.
  document.body.addEventListener("orca-tab-closed", (e) => {
    const { kind, refId } = e.detail || {};
    if (kind !== "task" || refId !== selTask) return;
    selTask = null;
    selStatus = null;
    highlightSel();
    const key = selKey();
    if (key) localStorage.removeItem(key);
  });

  document.body.addEventListener("orca-tab-activated", (e) => {
    const { kind, refId } = e.detail || {};
    if (kind !== "task" || refId === selTask) return;
    const node = document.querySelector(`.graph-node[data-task="${refId}"]`);
    const liveStatus = node ? node.dataset.status : null;
    const panel = document.getElementById(window.OrcaWorkspace.getTaskPanelId(refId));
    selTask = refId;
    selStatus = liveStatus;
    if (panel && panel.dataset.contentStatus !== liveStatus) {
      selectTask(refId);
      return;
    }
    highlightSel();
    const key = selKey();
    if (key) localStorage.setItem(key, String(refId));
    document.body.dispatchEvent(new CustomEvent("orca-task-selected", {
      detail: { id: refId, status: liveStatus, jobId: node && node.dataset.job ? +node.dataset.job : null },
    }));
  });

  function highlightSel() {
    document.querySelectorAll(".graph-node").forEach((n) =>
      n.classList.toggle("selected", n.dataset.task == selTask));
  }

  // 선택한 태스크가 아직 있으면 최신 값으로 다시 그리고, 지워졌으면 패널을 비운다.
  function refreshPanel() {
    if (selTask === null) return;
    if (document.querySelector(`.graph-node[data-task="${selTask}"]`)) {
      const node = document.querySelector(`.graph-node[data-task="${selTask}"]`);
      document.body.dispatchEvent(new CustomEvent("orca-task-selected", {
        detail: { id: selTask, status: node?.dataset.status || null,
          jobId: node?.dataset.job ? +node.dataset.job : null },
      }));
    } else {
      closePanel();
    }
  }

  // --- 오류 조치 패널 (모델 교체) -------------------------------------------
  // 모델 목록은 에이전트마다 다르다 — 서버가 패널에 실어 보낸 JSON에서 지금 고른
  // 에이전트의 목록만 뽑아 <select>를 다시 채운다.

  function fillModelSelect(panel, agentSel, modelSel) {
    const box = panel && panel.querySelector("[data-provider-models]");
    if (!box || !agentSel || !modelSel) return;
    let byProvider = {};
    try { byProvider = JSON.parse(box.textContent) || {}; } catch (err) { return; }
    // 첫 렌더에서는 서버가 저장해 둔 모델을, 이후에는 사용자가 고른 값을 유지한다
    const keep = modelSel.dataset.current || modelSel.value;
    modelSel.textContent = "";
    const base = document.createElement("option");
    base.value = "";
    base.textContent = tr("기본값");
    modelSel.appendChild(base);
    for (const entry of byProvider[agentSel.value] || []) {
      if (!entry.model) continue;   // '기본값' 행은 위에서 이미 넣었다
      const opt = document.createElement("option");
      opt.value = entry.model;
      opt.textContent = entry.label || entry.model;
      if (entry.model === keep) opt.selected = true;
      modelSel.appendChild(opt);
    }
    modelSel.dataset.current = "";
    modelSel.disabled = modelSel.options.length < 2;
  }

  function syncRecoverModels(root) {
    const panel = root || currentTaskRoot();
    fillModelSelect(panel,
      panel && panel.querySelector("[data-recover-agent]"),
      panel && panel.querySelector("[data-recover-model]"));
  }

  // 정규 편집 폼용 — 오류 조치 패널과 같은 목록(data-provider-models)을 읽는다
  function syncEditModels(root) {
    const panel = root || currentTaskRoot();
    fillModelSelect(panel,
      panel && panel.querySelector(".task-edit select[name=agent]"),
      panel && panel.querySelector("[data-edit-model]"));
  }

  function toggleEdit(on, root) {
    const scope = root || currentTaskRoot();
    if (!scope) return;
    const form = scope.querySelector(".task-edit");
    if (!form) return;
    form.hidden = !on;
    const desc = scope.querySelector(".task-desc");
    if (desc) desc.hidden = on;
    const deps = scope.querySelector(".task-deps");
    if (deps) deps.hidden = on;
    const editBtn = scope.querySelector("[data-task-edit]");
    if (editBtn) editBtn.hidden = on;
    const panel = scope.querySelector(".task-panel");
    if (panel) panel.classList.toggle("is-editing", on);
    if (on) {
      syncEditModels(scope);
      // 모바일 시트에서 입력란이 보이도록 패널 상단으로 스크롤
      form.scrollIntoView({ block: "nearest", behavior: "smooth" });
      const title = form.querySelector("input[name=title]");
      if (title) title.focus({ preventScroll: true });
    }
  }

  const editFormOpen = () => {
    const scope = currentTaskRoot();
    const f = scope && scope.querySelector(".task-edit");
    return !!f && !f.hidden;
  };

  // 선행 연결 저장 — 캔버스 post()와 같은 경로. htmx 폼 제출은 모바일에서
  // 피드백이 없고 실패 메시지도 시트 뒤에 가려져 "반응 없음"처럼 보였다.
  async function saveDeps(form) {
    const taskId = form.dataset.taskDeps;
    if (!taskId) return;
    const btn = form.querySelector(".btn-deps-save, button[type=submit]");
    const deps = [...form.querySelectorAll('input[name="deps"]:checked')]
      .map((el) => el.value);
    if (btn) {
      btn.disabled = true;
      btn.dataset.label = btn.textContent;
      btn.textContent = tr("저장 중…");
    }
    try {
      const ok = await post(`/tasks/${taskId}/deps`, { deps });
      if (ok) flash(tr("연결을 저장했습니다"), "ok");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.label || tr("연결 저장");
      }
    }
  }

  // --- 렌더 직후 복구 -------------------------------------------------------

  function afterBoardRender() {
    const c = canvas();
    paletteOpen = false;
    // 새 조각은 엣지 엘리먼트를 새로 그리므로 낡은 선택 참조를 들고 있을 수 없다
    selEdge = null;
    cancelLinkMode(false);
    if (!c) { prevNodeStatus = new Map(); return; }
    if (window.htmx) window.htmx.process(c.parentElement || c);
    // 서버가 이 폭에 맞지 않는 방향으로 그려 보냈으면(o 파라미터가 빠졌거나
    // 폭 판정이 요청 사이에 바뀌었거나) 한 번 다시 받아 온다. 폰에 데스크톱
    // 가로 배치가 눌러앉는 상태를 방향 판정 경로와 무관하게 끊어 준다.
    if (c.dataset.orientation !== boardOrientation() && !orientationRetry) {
      orientationRetry = true;
      reloadBoard().finally(() => { orientationRetry = false; });
      return;
    }
    // 방향이 바뀌었으면(회전·창 크기) 이전 카메라는 의미가 없다 — 다시 맞춘다
    if (c.dataset.orientation !== lastOrientation) {
      view = null;
      lastOrientation = c.dataset.orientation;
    }
    applyView();
    highlightSel();
    syncLiveState();
    restoreSelection();
  }

  // 새로고침 직후 첫 렌더에서 딱 한 번, 이전에 선택해뒀던 태스크가 아직
  // 존재하면 인스펙터를 다시 열어준다.
  function restoreSelection() {
    if (restoredSelection || selTask !== null) return;
    restoredSelection = true;
    const key = selKey();
    if (!key) return;
    const saved = localStorage.getItem(key);
    if (!saved) return;
    if (document.querySelector(`.graph-node[data-task="${saved}"]`)) {
      selectTask(+saved);
    } else {
      localStorage.removeItem(key);
    }
  }

  // 폴링으로 조각이 갈릴 때마다 태스크 상태 스냅샷을 chat-rail(실행 로그 탭)에
  // 흘려보내고, 직전 렌더 대비 상태가 바뀐 노드를 짧게 반짝여 "지금 여기가
  // 움직였다"를 캔버스에서도 알 수 있게 한다.
  function syncLiveState() {
    const nodes = [...document.querySelectorAll(".graph-node")];
    const tasks = nodes.map((n) => ({
      id: +n.dataset.task, seq: +n.dataset.seq, title: n.dataset.title,
      status: n.dataset.status,
      job_id: n.dataset.job ? +n.dataset.job : null,
    }));
    const nextStatus = new Map();
    for (const n of nodes) {
      const id = n.dataset.task;
      const status = n.dataset.status;
      nextStatus.set(id, status);
      if (prevNodeStatus.size && prevNodeStatus.get(id) !== status) {
        n.classList.remove("orca-live-flash");
        // eslint-disable-next-line no-unused-expressions
        n.offsetWidth; // 리플로우 강제 — 같은 클래스를 다시 붙였을 때도 애니메이션 재생
        n.classList.add("orca-live-flash");
      }
    }
    prevNodeStatus = nextStatus;
    document.body.dispatchEvent(new CustomEvent("orca-tasks-updated", { detail: tasks }));
    // 백그라운드(비활성) 태스크 탭도 상태 점만은 실시간으로 따라가게 한다.
    if (window.OrcaWorkspace) window.OrcaWorkspace.syncStatuses(tasks);
  }

  // --- 제스처 --------------------------------------------------------------

  function onPointerDown(e) {
    if (!e.target.closest || !e.target.closest(".graph-canvas")) return;
    const s = svg();
    if (!s) return;

    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) { startPinch(); return; }
    if (pointers.size > 2) return;

    const node = e.target.closest(".graph-node");
    const port = e.target.closest(".node-port");
    const edge = e.target.closest(".graph-edge-g");
    const p = toSvg(e);
    busy = true;

    // 다른 대상을 만지기 시작했다면 이전에 선택해 둔 화살표는 놓는다
    if (!edge) clearEdgeSelection();

    if (port && node && port.classList.contains("port-out") && editable()) {
      drag = { kind: "link", from: +node.dataset.seq };
    } else if (node) {
      drag = {
        kind: "node", el: node, id: +node.dataset.task,
        ox: +node.dataset.x, oy: +node.dataset.y,
        grabX: p.x, grabY: p.y, moved: false,
        // 모바일(tb)은 서버가 항상 한 줄 스택으로 다시 쌓으므로 옮겨도 제자리로
        // 돌아온다 — 끌기는 화면 이동에만 쓰고, 탭은 그대로 선택으로 둔다.
        movable: node.dataset.editable === "1",
        startX: e.clientX, startY: e.clientY,
        vx: view ? view.x : 0, vy: view ? view.y : 0,
      };
    } else if (edge && editable()) {
      drag = { kind: "edge", from: +edge.dataset.from, to: +edge.dataset.to, el: edge };
    } else {
      drag = {
        kind: "pan", moved: false, startX: e.clientX, startY: e.clientY,
        vx: view ? view.x : 0, vy: view ? view.y : 0,
      };
    }
  }

  function onPointerMove(e) {
    if (pointers.has(e.pointerId)) {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (pinch) { updatePinch(); return; }
    if (!drag || !svg()) return;

    if (drag.kind === "node") {
      // 옮길 수 없는 노드(실행 완료·모바일) 위에서 시작한 끌기는 화면 이동으로
      // 넘긴다 — 모바일은 캔버스 대부분이 노드라 그러지 않으면 팬이 막힌다.
      if (!drag.movable) {
        if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > DRAG_SLOP) {
          drag.moved = true;
        }
        panBy(drag, e);
        return;
      }
      const p = toSvg(e);
      const nx = Math.max(0, drag.ox + (p.x - drag.grabX));
      const ny = Math.max(0, drag.oy + (p.y - drag.grabY));
      if (Math.hypot(nx - drag.ox, ny - drag.oy) > DRAG_SLOP) drag.moved = true;
      drag.nx = nx; drag.ny = ny;
      drag.el.setAttribute("transform", `translate(${nx}, ${ny})`);
      trackEdges(+drag.el.dataset.seq, nx, ny);
    } else if (drag.kind === "link") {
      const rubber = svg().querySelector(".graph-edge.linking");
      if (!rubber) return;
      rubber.classList.add("active");
      rubber.setAttribute("d", edgePath(anchor(drag.from, "out"), toSvg(e)));
    } else if (drag.kind === "pan") {
      if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > DRAG_SLOP) {
        drag.moved = true;
      }
      panBy(drag, e);
    }
  }

  async function onPointerUp(e) {
    pointers.delete(e.pointerId);
    if (pinch) {
      if (pointers.size < 2) { pinch = null; busy = false; }
      return;
    }
    const d = drag;
    drag = null;
    if (!d) { busy = false; return; }
    try {
      if (d.kind === "node") {
        if (linkMode && !d.moved) {
          await finishLinkMode(d.el);
        } else if (d.moved && !d.movable) {
          /* 화면만 움직였다 — 저장할 것도, 열 것도 없다 */
        } else if (d.moved) {
          d.el.dataset.x = d.nx;
          d.el.dataset.y = d.ny;
          await post(`/tasks/${d.id}/move`,
                     { x: Math.round(d.nx), y: Math.round(d.ny) });
        } else {
          await selectTask(d.id);
        }
      } else if (d.kind === "link") {
        const rubber = svg() && svg().querySelector(".graph-edge.linking");
        if (rubber) { rubber.classList.remove("active"); rubber.setAttribute("d", ""); }
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const target = el && el.closest && el.closest(".graph-node");
        if (target) await connect(d.from, target);
      } else if (d.kind === "edge") {
        selectEdge(d.el);
      } else if (d.kind === "pan" && linkMode && !d.moved) {
        cancelLinkMode(true);
      } else if (d.kind === "pan" && !d.moved && selTask !== null) {
        // 빈 캔버스를 다시 누르면 태스크 컨텍스트를 닫고 프로젝트 채팅으로 돌아간다.
        closePanel();
      }
    } finally {
      busy = false;
    }
  }

  function startPinch() {
    drag = null;
    const [a, b] = [...pointers.values()];
    pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) };
  }

  function updatePinch() {
    const [a, b] = [...pointers.values()];
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    if (!dist || !pinch.dist) return;
    zoomAt(pinch.dist / dist, toSvgXY((a.x + b.x) / 2, (a.y + b.y) / 2));
    pinch.dist = dist;
  }

  function onWheel(e) {
    if (!e.target.closest || !e.target.closest(".graph-canvas") || !svg()) return;
    e.preventDefault();
    zoomAt(e.deltaY > 0 ? 1.12 : 1 / 1.12, toSvg(e));
  }

  // --- 연결 편집 -----------------------------------------------------------

  // 자기참조·중복·순환은 서버도 400으로 막지만(set_task_deps), 왕복 없이 바로
  // 이유를 보여주려고 같은 규칙을 클라이언트에서 먼저 검사한다.
  async function connect(fromSeq, targetNode) {
    if (targetNode.dataset.editable !== "1") {
      flash(tr("이미 실행된 태스크는 편집할 수 없습니다"));
      return;
    }
    const to = +targetNode.dataset.seq;
    if (to === fromSeq) {
      flash(tr("자기 자신에 의존할 수 없습니다"));
      return;
    }
    const deps = depsOf(to);
    if (deps.includes(fromSeq)) {
      flash(tr("이미 연결되어 있습니다"));
      return;
    }
    if (reachable(to, fromSeq)) {
      flash(tr("순환 의존은 만들 수 없습니다"));
      return;
    }
    await post(`/tasks/${targetNode.dataset.task}/deps`,
               { deps: deps.concat(fromSeq) });
  }

  async function disconnect(fromSeq, toSeq) {
    const g = svg().querySelector(`.graph-node[data-seq="${toSeq}"]`);
    if (!g || g.dataset.editable !== "1") return false;
    return post(`/tasks/${g.dataset.task}/deps`,
                { deps: depsOf(toSeq).filter((d) => d !== fromSeq) });
  }

  // --- 화살표 선택 · 삭제 (키보드 Delete/Backspace 또는 툴바 버튼) ------------

  function clearEdgeSelection() {
    if (selEdge) selEdge.el.classList.remove("selected");
    selEdge = null;
    const btn = document.querySelector("#board .graph-toolbar .graph-edge-del");
    if (btn) btn.hidden = true;
  }

  function selectEdge(g) {
    if (selEdge && selEdge.el === g) return;
    clearEdgeSelection();
    selEdge = { from: +g.dataset.from, to: +g.dataset.to, el: g };
    g.classList.add("selected");
    const bar = document.querySelector("#board .graph-toolbar");
    if (!bar) return;
    let btn = bar.querySelector(".graph-edge-del");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-ghost graph-edge-del";
      btn.textContent = tr("연결 삭제");
      btn.addEventListener("click", disconnectSelected);
      const spacer = bar.querySelector(".spacer");
      if (spacer) bar.insertBefore(btn, spacer); else bar.appendChild(btn);
    }
    btn.textContent = tr("연결 삭제");
    btn.hidden = false;
  }

  async function disconnectSelected() {
    if (!selEdge) return;
    const { from, to } = selEdge;
    clearEdgeSelection();
    if (await disconnect(from, to)) flash(tr("연결을 삭제했습니다"), "ok");
  }

  // --- 연결 모드 (터치 대안: 노드 선택 → '연결' 버튼 → 대상 노드 탭) --------

  function updateLinkModeUI() {
    document.body.classList.toggle("link-mode-active", !!linkMode);
    document.querySelectorAll("[data-task-link]").forEach((btn) => {
      btn.classList.toggle("is-active", !!linkMode && +btn.dataset.seq === linkMode.seq);
    });
  }

  function cancelLinkMode(showMsg) {
    if (!linkMode) return;
    linkMode = null;
    updateLinkModeUI();
    if (showMsg) flash(tr("연결 모드를 취소했습니다"), "ok");
  }

  async function finishLinkMode(targetNode) {
    const from = linkMode.seq;
    linkMode = null;
    updateLinkModeUI();
    await connect(from, targetNode);
  }

  // --- 툴바 / 팔레트 --------------------------------------------------------

  function openPalette(open) {
    const form = document.querySelector("#board .node-palette");
    if (!form) return;
    paletteOpen = open;
    form.hidden = !open;
    if (!open || !view) return;
    // 새 노드는 지금 보이는 화면 한가운데에 놓는다
    form.querySelector("input[name=x]").value = Math.round(view.x + view.w / 2 - 95);
    form.querySelector("input[name=y]").value = Math.round(view.y + view.h / 2 - 36);
    form.querySelector("input[name=title]").focus();
  }

  async function onToolbar(action) {
    switch (action) {
      case "add": openPalette(true); break;
      case "cancel-add": openPalette(false); break;
      case "fit": view = null; applyView(); break;
      case "zoom-in": zoomAt(1 / 1.25, viewCenter()); break;
      case "zoom-out": zoomAt(1.25, viewCenter()); break;
      case "relayout": {
        const pid = canvas().dataset.project;
        view = null;
        await post(`/projects/${pid}/relayout`, {});
        break;
      }
    }
  }

  // --- 바인딩 --------------------------------------------------------------

  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
  document.addEventListener("wheel", onWheel, { passive: false });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-canvas]");
    if (btn) { onToolbar(btn.dataset.canvas); return; }
    if (e.target.closest("[data-task-close]")) { closePanel(); return; }
    if (e.target.closest("[data-task-edit]")) { toggleEdit(true, taskRootOf(e.target)); return; }
    if (e.target.closest("[data-task-edit-cancel]")) { toggleEdit(false, taskRootOf(e.target)); return; }
    const linkBtn = e.target.closest("[data-task-link]");
    if (linkBtn) {
      const seq = +linkBtn.dataset.seq;
      if (linkMode && linkMode.seq === seq) {
        cancelLinkMode(true);
      } else {
        linkMode = { seq };
        updateLinkModeUI();
        flash(tr("연결할 태스크를 탭하세요"), "ok");
      }
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.closest && e.target.closest("[data-recover-agent]")) {
      syncRecoverModels(taskRootOf(e.target));
    }
    if (e.target.closest && e.target.closest(".task-edit select[name=agent]")) {
      syncEditModels(taskRootOf(e.target));
    }
  });

  // 연결 저장 폼 — submit을 JS post()로 처리한다.
  document.addEventListener("submit", (e) => {
    const form = e.target.closest && e.target.closest("form.task-deps");
    if (!form) return;
    e.preventDefault();
    saveDeps(form);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (paletteOpen) openPalette(false);
      else if (linkMode) cancelLinkMode(true);
      else if (editFormOpen()) toggleEdit(false);
      else if (selTask !== null) closePanel();
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selEdge) {
      const tag = document.activeElement && document.activeElement.tagName;
      // 입력란에서 타이핑 중인 Delete/Backspace는 그대로 텍스트 편집에 쓰게 둔다
      if (tag === "INPUT" || tag === "TEXTAREA" ||
          (document.activeElement && document.activeElement.isContentEditable)) return;
      e.preventDefault();
      disconnectSelected();
    }
  });

  // 폴링이 편집을 방해하지 않게 한다. plan_ready/paused에서는 오케스트레이터가
  // 프로젝트를 전진시키지 않으므로 갱신할 상태 자체가 없다 — 통째로 건너뛴다.
  document.body.addEventListener("htmx:beforeRequest", (e) => {
    const elt = e.detail && e.detail.elt;
    if (elt && elt.id === "board" && (busy || paletteOpen || editFormOpen() || editable())) {
      e.preventDefault();
    }
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id !== "board") return;
    afterBoardRender();
    // 선택한 태스크의 상태가 바뀐 순간에만 상세를 다시 불러온다
    // (매번 갈아끼우면 미디어 재생이 끊기므로)
    if (selTask !== null) {
      const node = document.querySelector(`.graph-node[data-task="${selTask}"]`);
      if (node && node.dataset.status !== selStatus) selectTask(selTask);
    }
  });

  // 상세 패널의 폼(편집·연결·삭제)은 #board를 갈아끼운다 — 패널도 함께 맞춘다.
  document.body.addEventListener("htmx:afterRequest", (e) => {
    const elt = e.detail && e.detail.elt;
    if (!elt || !elt.closest) return;
    if (!elt.closest("#task-detail") && !elt.closest('.orca-tab-panel[data-tab-kind="task"]')) return;
    if (e.detail.successful) refreshPanel();
  });

  document.body.addEventListener("htmx:responseError", (e) => {
    let msg = e.detail.xhr.statusText;
    try { msg = JSON.parse(e.detail.xhr.responseText).detail || msg; } catch (err) { /* 본문 없음 */ }
    flash(msg);
  });

  // 활성 에이전트 반응을 시각적 무드로 전환한다. queue/run에 진입하면 해당
  // 메시지와 함께 애니메이션 상태를 켜고, 완료 시 해제한다.
  function setAgentMood(messageId, provider, status) {
    const target = document.querySelector(`[data-msg-id="${messageId}"]`);
    if (!target) return;
    target.dataset.agentProvider = provider || "";
    target.dataset.agentStatus = status;
  }
  document.body.addEventListener("orca-queued", (e) => {
    setAgentMood(e.detail?.id, e.detail?.provider || "", "queued");
  });
  document.body.addEventListener("orca-running", (e) => {
    setAgentMood(e.detail?.id, e.detail?.provider || "", "running");
  });
  document.body.addEventListener("orca-finished", (e) => {
    setAgentMood(e.detail?.id, e.detail?.provider || "", "done");
  });

  function animateMessageAppear(el) {
    if (!el || typeof gsap === "undefined") return;
    gsap.fromTo(el,
      { opacity: 0, y: 10 },
      { opacity: 1, y: 0, duration: 0.32, ease: "power2.out", overwrite: true }
    );
  }
  document.body.addEventListener("orca-message-appended", (e) => {
    animateMessageAppear(e.detail?.el);
  });

  function animateNodeRegistration(node, statusAtRegistration) {
    if (!node || typeof gsap === "undefined") return;
    const base = { opacity: 0, scale: 0.86 };
    gsap.fromTo(node, base, {
      opacity: 1, scale: 1, duration: 0.38, ease: "back.out(1.4)", overwrite: true
    });
    if (statusAtRegistration === "queued") {
      node.classList.add("orca-live-flash");
      setTimeout(() => node.classList.remove("orca-live-flash"), 1100);
    }
  }
  document.body.addEventListener("orca-task-registered", (e) => {
    animateNodeRegistration(e.detail?.node, e.detail?.status);
  });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const c = canvas();
      if (!c) return;
      // 가로↔세로가 바뀌면 서버에 다시 배치를 받아야 한다
      if (c.dataset.orientation !== boardOrientation()) reloadBoard();
      else { view = null; applyView(); }
    }, 200);
  });

  // 데스크톱 워크스페이스에서 사이드바 폭을 드래그로 바꾸는 등, window resize
  // 이벤트 없이 #board의 컨테이너 크기만 바뀌는 경우(예: 채팅 레일 리사이즈
  // 핸들)에도 뷰박스를 다시 맞춘다. viewBox 자체는 SVG width/height=100%로
  // 이미 늘어나지만, 가로세로 비율이 바뀌면 fitView를 다시 돌려야 여백 없이
  // 꽉 찬다.
  if (window.ResizeObserver) {
    let boardResizeTimer;
    const boardResizeObserver = new ResizeObserver(() => {
      clearTimeout(boardResizeTimer);
      boardResizeTimer = setTimeout(() => {
        if (!canvas()) return;
        view = null;
        applyView();
      }, 120);
    });
    const boardEl = board();
    if (boardEl) boardResizeObserver.observe(boardEl);
  }

  // chat-rail에서 에이전트 메시지가 끝났다는 신호를 보내오면 2초 폴링을 기다리지
  // 않고 바로 보드를 다시 그린다 — 채팅에서의 활동이 캔버스에 즉시 반영되게 한다.
  document.body.addEventListener("orca-refresh-board", () => {
    if (!busy && !paletteOpen && !editFormOpen() && !editable()) reloadBoard();
  });
})();
