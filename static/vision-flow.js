// 중앙 비전 보드 — 진행 흐름(카드 + 트리 다이어그램 + 변경 파일) 뷰.
//
// board-editor.js의 노드-링크 다이어그램(#board .graph-canvas)과 같은 데이터를
// 쓰지만 별도 API를 부르지 않는다 — board.html이 이미 각 <g class="graph-node">에
// data-title/provider/type/artifact/error를 싣고 있으므로, 이 파일은 그 DOM만 읽어
// "지금 프로젝트가 어디까지 왔는지"를 카드/트리/파일 목록으로 다시 그린다.
// #board가 폴링·편집으로 통째로 갈릴 때마다 MutationObserver가 다시 그려서,
// board-editor.js를 건드리지 않고도 항상 최신 상태를 반영한다.
//
// 렌더 결과는 board-workspace.js가 관리하는 독립 탭(kind="flow")의 패널에
// 주입된다 — #board 내부에 패널을 붙였다 떼는 예전 토글 방식은 쓰지 않는다.
// 탭 자체의 존재/활성화/DOM 생명주기는 전부 OrcaWorkspace가 맡고, 이 파일은
// "패널이 있으면 최신 내용으로 채운다"만 책임진다.

(function () {
  "use strict";

  const ICON = { text: "📝", image: "🖼️", video: "🎬", audio: "🎵" };
  const STATUS_LABEL = {
    pending: "대기", queued: "대기열", running: "실행 중", done: "완료", failed: "실패",
  };
  const STATUS_ORDER = ["pending", "queued", "running", "done", "failed"];

  let prevStatus = new Map(); // task id → 직전 렌더 status, 변화 시 트리 카드를 반짝인다

  const tr = (s) => (window.t ? window.t(s) : s);
  const board = () => document.getElementById("board");
  const canvas = () => document.querySelector("#board .graph-canvas");
  const toolbar = () => document.querySelector("#board .graph-toolbar");
  const toggleBtn = () => document.querySelector("#board [data-flow-toggle]");

  function readNodes() {
    return [...document.querySelectorAll("#board .graph-node")].map((g) => ({
      id: g.dataset.task,
      seq: +g.dataset.seq,
      status: g.dataset.status,
      title: g.dataset.title || "",
      provider: g.dataset.provider || "",
      type: g.dataset.type || "text",
      artifact: g.dataset.artifact || "",
      error: g.dataset.error === "1",
    }));
  }

  function readDeps() {
    // seq → [dep seq, ...] — .graph-edge-g가 캔버스와 같은 소스이므로 재사용
    const deps = new Map();
    document.querySelectorAll("#board .graph-edge-g").forEach((eg) => {
      const from = +eg.dataset.from, to = +eg.dataset.to;
      if (!deps.has(to)) deps.set(to, []);
      deps.get(to).push(from);
    });
    return deps;
  }

  function depthOf(seq, deps, cache, trail) {
    if (cache.has(seq)) return cache.get(seq);
    const parents = (deps.get(seq) || []).filter((p) => p !== seq && !trail.has(p));
    const d = parents.length === 0
      ? 0
      : 1 + Math.max(...parents.map((p) => depthOf(p, deps, cache, new Set([...trail, seq]))));
    cache.set(seq, d);
    return d;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // --- 카드: 진행 통계 -------------------------------------------------------

  function renderStats(nodes) {
    const total = nodes.length;
    const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0]));
    nodes.forEach((n) => { if (n.status in counts) counts[n.status] += 1; });
    const done = counts.done;
    const pct = total ? Math.round((done / total) * 100) : 0;

    const tiles = [
      ["total", tr("전체"), total],
      ["done", tr("완료"), counts.done],
      ["running", tr("실행 중"), counts.running],
      ["pending", tr("대기"), counts.pending + counts.queued],
      ["failed", tr("실패"), counts.failed],
    ];

    return `
      <div class="orca-flow-stats">
        ${tiles.map(([key, label, n]) => `
          <div class="orca-flow-stat-card is-${key}">
            <span class="orca-flow-stat-num">${n}</span>
            <span class="orca-flow-stat-label">${escapeHtml(label)}</span>
          </div>`).join("")}
        <div class="orca-flow-progress" role="progressbar" aria-valuenow="${pct}"
             aria-valuemin="0" aria-valuemax="100">
          <div class="orca-flow-progress-bar" style="width:${pct}%"></div>
          <span class="orca-flow-progress-label">${pct}%</span>
        </div>
      </div>`;
  }

  // --- 트리 다이어그램: 의존성 깊이 순 로드맵 --------------------------------

  function renderTree(nodes) {
    if (!nodes.length) {
      return `<p class="orca-flow-empty">${tr("아직 태스크가 없습니다")}</p>`;
    }
    const deps = readDeps();
    const cache = new Map();
    const withDepth = nodes.map((n) => ({ ...n, depth: depthOf(n.seq, deps, cache, new Set()) }));
    withDepth.sort((a, b) => a.depth - b.depth || a.seq - b.seq);

    return `
      <ol class="orca-flow-tree">
        ${withDepth.map((n) => {
          const changed = prevStatus.size && prevStatus.get(n.id) !== n.status;
          return `
          <li class="orca-flow-tree-node" style="--depth:${n.depth}">
            <button type="button" class="orca-flow-tree-card status-${n.status}${changed ? " orca-live-flash" : ""}"
                    data-flow-select="${n.id}">
              <span class="orca-flow-tree-connector" aria-hidden="true"></span>
              <span class="orca-flow-tree-icon">${ICON[n.type] || "📝"}</span>
              <span class="orca-flow-tree-title">[${n.seq}] ${escapeHtml(n.title)}</span>
              <span class="orca-flow-tree-provider">${escapeHtml(n.provider)}</span>
              <span class="orca-badge orca-badge-${n.status}">${tr(STATUS_LABEL[n.status] || n.status)}</span>
            </button>
          </li>`;
        }).join("")}
      </ol>`;
  }

  // --- 카드: 생성/수정된 파일 -------------------------------------------------

  function renderFiles(nodes, projectId) {
    const files = nodes.filter((n) => n.artifact);
    if (!files.length) {
      return `<p class="orca-flow-empty">${tr("아직 생성된 파일이 없습니다")}</p>`;
    }
    return `
      <div class="orca-flow-files">
        ${files.map((n) => `
          <a class="orca-flow-file-card" data-flow-select="${n.id}"
             href="/artifacts/${projectId}/${encodeURIComponent(n.artifact)}" target="_blank" rel="noopener">
            <span class="orca-flow-file-icon">${ICON[n.type] || "📝"}</span>
            <span class="orca-flow-file-meta">
              <span class="orca-flow-file-name">${escapeHtml(n.artifact)}</span>
              <span class="orca-flow-file-task">[${n.seq}] ${escapeHtml(n.title)}</span>
            </span>
          </a>`).join("")}
      </div>`;
  }

  // --- 패널 렌더 ---------------------------------------------------------

  // 탭이 아직 열려 있지 않으면 만들지 않는다 — 패널 내용은 실제로 탭이
  // 열렸을 때만 채운다(생성은 OrcaWorkspace.openFlowTab의 몫).
  function getPanel() {
    return document.getElementById("orca-tab-panel-flow");
  }

  function render() {
    const c = canvas();
    const panel = getPanel();
    if (!c || !panel) return;

    const nodes = readNodes();
    const projectId = c.dataset.project;
    panel.innerHTML = `
      <div class="orca-flow">
        ${renderStats(nodes)}
        <div class="orca-flow-section">
          <h3>${tr("로드맵")}</h3>
          ${renderTree(nodes)}
        </div>
        <div class="orca-flow-section">
          <h3>${tr("변경된 파일")}</h3>
          ${renderFiles(nodes, projectId)}
        </div>
      </div>`;
    prevStatus = new Map(nodes.map((n) => [n.id, n.status]));
    // 새로 그려진 카드에 대해 등록 애니메이션/무드를 적용한다.
    if (typeof CustomEvent !== "undefined") {
      [...panel.querySelectorAll(".orca-flow-tree-card")].forEach((card) => {
        const id = card.dataset.flowSelect;
        const status = [...card.classList].find((c) => c.startsWith("status-"))?.replace("status-", "") || "pending";
        document.body.dispatchEvent(new CustomEvent("orca-task-registered", { detail: { node: card, status } }));
      });
      [...panel.querySelectorAll(".orca-flow-file-card")].forEach((card) => {
        const id = card.dataset.flowSelect;
        const status = prevStatus.get(id) || "pending";
        document.body.dispatchEvent(new CustomEvent("orca-task-registered", { detail: { node: card, status } }));
      });
    }
  }

  // 탭바 밖(캔버스 툴바) 버튼 — 진행 흐름 탭을 열고(없으면 만들고) 활성화한다.
  // 실제 렌더는 뒤이어 오는 orca-tab-activated 이벤트 리스너가 담당한다.
  function openFlowTab() {
    if (window.OrcaWorkspace) window.OrcaWorkspace.openFlowTab();
  }

  // --- 바인딩 ------------------------------------------------------------

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-flow-toggle]")) { openFlowTab(); return; }
    const card = e.target.closest("[data-flow-select]");
    if (card && window.selectTask) {
      e.preventDefault();
      window.selectTask(+card.dataset.flowSelect);
    }
  });

  document.body.addEventListener("orca-tab-activated", (e) => {
    const btn = toggleBtn();
    if (btn) btn.setAttribute("aria-pressed", String(e.detail && e.detail.kind === "flow"));
    if (e.detail && e.detail.kind === "flow") render();
  });
  document.body.addEventListener("orca-tab-closed", (e) => {
    if (!(e.detail && e.detail.kind === "flow")) return;
    const btn = toggleBtn();
    if (btn) btn.setAttribute("aria-pressed", "false");
  });

  // #board 전체가 폴링/편집으로 통째로 갈릴 때마다(htmx 스왑, board-editor.js의
  // post() 응답) 다시 그린다 — 진행 흐름 탭이 열려 있을 때만 실제로 그리며,
  // board-editor.js 이벤트에 얹혀가지 않고 DOM만 본다.
  const observer = new MutationObserver((mutations) => {
    const relevant = mutations.some((m) => [...m.addedNodes, ...m.removedNodes].some((n) => n.nodeType === 1));
    if (relevant) render();
  });

  document.addEventListener("DOMContentLoaded", () => {
    const b = board();
    if (!b) return;
    observer.observe(b, { childList: true });
  });
})();
