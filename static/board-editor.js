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

  const NARROW = 700;        // 이 폭 미만이면 세로 흐름(tb)으로 재정렬한다
  const DRAG_SLOP = 4;       // 이보다 덜 움직이면 클릭(=선택)으로 본다

  let view = null;           // {x, y, w, h} — null이면 다음 렌더에서 화면 맞춤
  let lastOrientation = null;
  let selTask = null, selStatus = null;
  let busy = false;          // 제스처 진행 중 — 폴링을 막는다
  let paletteOpen = false;
  let drag = null, pinch = null;
  const pointers = new Map();
  let prevNodeStatus = new Map(); // task id → 직전 렌더에서의 status, 변화 감지용
  let restoredSelection = false; // 새로고침 직후 localStorage에서 선택 복원을 1회만 시도

  // --- 조회 헬퍼 ------------------------------------------------------------

  const board = () => document.getElementById("board");
  const canvas = () => document.querySelector("#board .graph-canvas");
  const svg = () => document.querySelector("#board .graph-svg");
  const editable = () => { const c = canvas(); return !!c && c.dataset.editable === "1"; };
  const orientation = () => { const c = canvas(); return c ? c.dataset.orientation : "lr"; };
  const tr = (s) => (window.t ? window.t(s) : s);

  // 서버에 어떤 방향으로 그려 달라고 할지 — htmx의 hx-vals가 이 함수를 부른다.
  function boardOrientation() {
    return window.innerWidth < NARROW ? "tb" : "lr";
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
    // 1:1보다 크게는 확대하지 않는다 — 노드 두어 개짜리 그래프가 우스꽝스럽게
    // 부풀지 않도록. (축소는 그대로 허용 — 큰 그래프는 다 들어와야 한다)
    if (box.width > 0 && vw < box.width) {
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

  // --- 태스크 상세 패널 -----------------------------------------------------

  const taskPanel = () => document.getElementById("task-detail");
  const taskBackdrop = () => document.getElementById("task-detail-backdrop");

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

  async function selectTask(id) {
    selTask = id;
    const res = await fetch(`/partials/task/${id}`);
    if (!res.ok) return;
    const panel = taskPanel();
    panel.innerHTML = await res.text();
    if (window.htmx) window.htmx.process(panel);
    const src = panel.querySelector(".task-src");
    if (src) renderMarkdown(panel.querySelector(".task-out"), src.textContent);
    const node = document.querySelector(`.graph-node[data-task="${id}"]`);
    selStatus = node ? node.dataset.status : null;
    setPanelOpen(true);
    highlightSel();
    const key = selKey();
    if (key) localStorage.setItem(key, String(id));
    // chat-rail이 이 태스크의 잡을 실행 로그 탭에서 바로 따라가도록 알린다.
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
    const panel = taskPanel();
    if (panel) panel.innerHTML = "";
    setPanelOpen(false);
    highlightSel();
    const key = selKey();
    if (key) localStorage.removeItem(key);
  }
  window.closeTaskPanel = closePanel;

  function highlightSel() {
    document.querySelectorAll(".graph-node").forEach((n) =>
      n.classList.toggle("selected", n.dataset.task == selTask));
  }

  // 선택한 태스크가 아직 있으면 최신 값으로 다시 그리고, 지워졌으면 패널을 비운다.
  function refreshPanel() {
    if (selTask === null) return;
    if (document.querySelector(`.graph-node[data-task="${selTask}"]`)) {
      selectTask(selTask);
    } else {
      closePanel();
    }
  }

  function toggleEdit(on) {
    const form = document.querySelector("#task-detail .task-edit");
    if (!form) return;
    form.hidden = !on;
    const desc = document.querySelector("#task-detail .task-desc");
    if (desc) desc.hidden = on;
    const deps = document.querySelector("#task-detail .task-deps");
    if (deps) deps.hidden = on;
    const editBtn = document.querySelector("#task-detail [data-task-edit]");
    if (editBtn) editBtn.hidden = on;
    const panel = document.querySelector("#task-detail .task-panel");
    if (panel) panel.classList.toggle("is-editing", on);
    if (on) {
      // 모바일 시트에서 입력란이 보이도록 패널 상단으로 스크롤
      form.scrollIntoView({ block: "nearest", behavior: "smooth" });
      const title = form.querySelector("input[name=title]");
      if (title) title.focus({ preventScroll: true });
    }
  }

  const editFormOpen = () => {
    const f = document.querySelector("#task-detail .task-edit");
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
    if (!c) { prevNodeStatus = new Map(); return; }
    if (window.htmx) window.htmx.process(c.parentElement || c);
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

    if (port && node && port.classList.contains("port-out") && editable()) {
      drag = { kind: "link", from: +node.dataset.seq };
    } else if (node) {
      drag = {
        kind: "node", el: node, id: +node.dataset.task,
        ox: +node.dataset.x, oy: +node.dataset.y,
        grabX: p.x, grabY: p.y, moved: false,
        movable: node.dataset.editable === "1",
      };
    } else if (edge && editable()) {
      drag = { kind: "edge", from: +edge.dataset.from, to: +edge.dataset.to };
    } else {
      drag = {
        kind: "pan", startX: e.clientX, startY: e.clientY,
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
      if (!drag.movable) return;
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
    } else if (drag.kind === "pan" && view) {
      const box = canvas().getBoundingClientRect();
      const scale = box.width > 0 ? view.w / box.width : 1;
      view.x = drag.vx - (e.clientX - drag.startX) * scale;
      view.y = drag.vy - (e.clientY - drag.startY) * scale;
      applyView();
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
        if (d.moved) {
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
        if (target && +target.dataset.seq !== d.from) await connect(d.from, target);
      } else if (d.kind === "edge") {
        await disconnect(d.from, d.to);
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

  async function connect(fromSeq, targetNode) {
    if (targetNode.dataset.editable !== "1") {
      flash(tr("이미 실행된 태스크는 편집할 수 없습니다"));
      return;
    }
    const to = +targetNode.dataset.seq;
    const deps = depsOf(to);
    if (deps.includes(fromSeq)) return;
    await post(`/tasks/${targetNode.dataset.task}/deps`,
               { deps: deps.concat(fromSeq) });
  }

  async function disconnect(fromSeq, toSeq) {
    const g = svg().querySelector(`.graph-node[data-seq="${toSeq}"]`);
    if (!g || g.dataset.editable !== "1") return;
    if (!confirm(tr("이 연결을 끊을까요?"))) return;
    await post(`/tasks/${g.dataset.task}/deps`,
               { deps: depsOf(toSeq).filter((d) => d !== fromSeq) });
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
    if (e.target.closest("[data-task-edit]")) { toggleEdit(true); return; }
    if (e.target.closest("[data-task-edit-cancel]")) { toggleEdit(false); }
  });

  // 연결 저장 폼 — submit을 JS post()로 처리한다.
  document.addEventListener("submit", (e) => {
    const form = e.target.closest && e.target.closest("form.task-deps");
    if (!form) return;
    e.preventDefault();
    saveDeps(form);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (paletteOpen) openPalette(false);
    else if (editFormOpen()) toggleEdit(false);
    else if (selTask !== null) closePanel();
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
    if (!elt || !elt.closest || !elt.closest("#task-detail")) return;
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

  // chat-rail에서 에이전트 메시지가 끝났다는 신호를 보내오면 2초 폴링을 기다리지
  // 않고 바로 보드를 다시 그린다 — 채팅에서의 활동이 캔버스에 즉시 반영되게 한다.
  document.body.addEventListener("orca-refresh-board", () => {
    if (!busy && !paletteOpen && !editFormOpen() && !editable()) reloadBoard();
  });
})();
