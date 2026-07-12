"use strict";

// ---- 사이드바 토글 (모바일) --------------------------------------------
document.getElementById("nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});

// ---- 사용량 패널 접기/펼치기 (기본 접힘, 상태는 localStorage 유지) --------
const usageSection = document.getElementById("usage");
const USAGE_KEY = "aos-usage-open";
function applyUsageState() {
  if (!usageSection) return;
  const open = localStorage.getItem(USAGE_KEY) === "1";
  usageSection.classList.toggle("usage-open", open);
  const btn = usageSection.querySelector(".usage-toggle");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".usage-toggle") || !usageSection) return;
  const open = !usageSection.classList.contains("usage-open");
  localStorage.setItem(USAGE_KEY, open ? "1" : "0");
  applyUsageState();
});
// 15초마다 htmx가 사용량 partial을 새로 갈아끼워도 접힘/펼침 상태를 다시 적용
document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.id === "usage") applyUsageState();
});
applyUsageState();

// ---- 파일 첨부 칩 + 드래그 앤 드롭 -------------------------------------
const composer = document.getElementById("composer");
const fileInput = document.getElementById("file-input");
const chips = document.getElementById("file-chips");

function renderChips() {
  chips.innerHTML = "";
  for (const f of fileInput.files) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = f.name;
    chips.appendChild(chip);
  }
}
fileInput?.addEventListener("change", renderChips);
composer?.addEventListener("dragover", (e) => {
  e.preventDefault();
  composer.classList.add("dragging");
});
composer?.addEventListener("dragleave", (e) => {
  if (e.target === composer) composer.classList.remove("dragging");
});
composer?.addEventListener("drop", (e) => {
  e.preventDefault();
  composer.classList.remove("dragging");
  const dt = new DataTransfer();
  for (const f of fileInput.files) dt.items.add(f);
  for (const f of e.dataTransfer.files) dt.items.add(f);
  fileInput.files = dt.files;
  renderChips();
});

// ---- 에이전트/모델 선택 + 도구(+) 메뉴 ---------------------------------
const providerInput = document.getElementById("provider-input");
const modelInput = document.getElementById("model-input");
const agentBtn = document.getElementById("agent-btn");
const agentLabel = document.getElementById("agent-label");
const agentPopup = document.getElementById("agent-popup");
const modelBtn = document.getElementById("model-btn");
const modelChipLabel = document.getElementById("model-label");
const modelPopup = document.getElementById("model-popup");
const toolsBtn = document.getElementById("tools-btn");
const toolsPopup = document.getElementById("tools-popup");
const chosenModel = {};                     // provider -> model id ("" = 기본값)

// 팝업에 노출할 에이전트: 자동 + 지정 순서(claude/antigravity/grok/hermes) + 협의
// 협의(council)는 여러 에이전트가 제안·비평하고 하나가 종합하는 모드 —
// 모델 선택이 없어(MODELS에 없음) 모델 칩은 자동으로 숨겨진다.
const AGENTS = [{ id: "auto", name: "자동" }]
  .concat(
    AGENT_ORDER.filter((p) => MODELS[p]).map((p) => ({ id: p, name: agentName(p) }))
  )
  .concat([{ id: "council", name: "협의" }]);

function agentName(p) {
  if (p === "auto") return "자동";
  if (p === "council") return "협의";
  return p.charAt(0).toUpperCase() + p.slice(1);
}
function defaultModel(p) {
  const list = MODELS[p] || [];
  const d = list.find((m) => m.default) || list[0];
  return d ? (d.model || "") : "";
}
function modelLabel(p, id) {
  const m = (MODELS[p] || []).find((x) => (x.model || "") === (id || ""));
  return m ? m.label : "";
}

// 모델 칩: 자동이 아니고 모델이 2개 이상인 에이전트에서만 노출한다
function modelChipVisible(p) {
  return p !== "auto" && (MODELS[p] || []).length > 1;
}

function refreshChips() {
  const p = providerInput.value;
  agentLabel.textContent = agentName(p);
  if (modelChipVisible(p)) {
    modelBtn.hidden = false;
    const id = chosenModel[p] || defaultModel(p);
    modelChipLabel.textContent = modelLabel(p, id) || "모델";
  } else {
    modelBtn.hidden = true;
  }
}

function selectProvider(p) {
  providerInput.value = p;
  modelInput.value = p === "auto" ? "" : (chosenModel[p] || "");
  refreshChips();
  updateAutoHint();
}

function selectModel(p, id) {
  chosenModel[p] = id;
  modelInput.value = id;
  refreshChips();
}

function renderAgentPopup() {
  const cur = providerInput.value;
  agentPopup.innerHTML = "";
  const head = document.createElement("div");
  head.className = "popup-head";
  head.textContent = "에이전트";
  agentPopup.appendChild(head);

  for (const a of AGENTS) {
    const multi = a.id !== "auto" && (MODELS[a.id] || []).length > 1;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "popup-row";
    row.innerHTML =
      `<span class="popup-check">${a.id === cur ? "✓" : ""}</span>` +
      `<span class="popup-label">${a.name}</span>` +
      (a.id === "council"
        ? `<span class="popup-tag" title="여러 에이전트가 제안·비평하고 하나가 종합합니다 (사용량 다중 소모)">토론</span>`
        : multi ? `<span class="popup-tag">모델</span>` : "");
    row.addEventListener("click", () => { selectProvider(a.id); closeComposerPopups(); });
    agentPopup.appendChild(row);
  }
}

function renderModelPopup() {
  const cur = providerInput.value;
  modelPopup.innerHTML = "";
  const head = document.createElement("div");
  head.className = "popup-head";
  head.textContent = agentName(cur) + " 모델";
  modelPopup.appendChild(head);

  const curModel = chosenModel[cur] || "";
  for (const m of MODELS[cur] || []) {
    const id = m.model || "";
    const sel = (id === curModel) || (!curModel && m.default);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "popup-row";
    row.innerHTML =
      `<span class="popup-check">${sel ? "✓" : ""}</span>` +
      `<span class="popup-label">${m.label}</span>` +
      (m.default ? `<span class="popup-tag">기본값</span>` : "");
    row.addEventListener("click", () => { selectModel(cur, id); closeComposerPopups(); });
    modelPopup.appendChild(row);
  }
}

function closeComposerPopups() {
  if (agentPopup) agentPopup.hidden = true;
  if (modelPopup) modelPopup.hidden = true;
  if (toolsPopup) toolsPopup.hidden = true;
  closeWsPopup();
}
function openComposerPopup(popup, anchor) {
  closeComposerPopups();
  popup.hidden = false;
  const r = anchor.getBoundingClientRect();
  const w = popup.offsetWidth || 200;
  let left = r.left + window.scrollX;
  const maxLeft = window.scrollX + document.documentElement.clientWidth - w - 8;
  if (left > maxLeft) left = Math.max(8, maxLeft);
  popup.style.top = (r.bottom + 6 + window.scrollY) + "px";
  popup.style.left = left + "px";
}

agentBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!agentPopup.hidden) { closeComposerPopups(); return; }
  renderAgentPopup();
  openComposerPopup(agentPopup, agentBtn);
});
modelBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!modelPopup.hidden) { closeComposerPopups(); return; }
  renderModelPopup();
  openComposerPopup(modelPopup, modelBtn);
});
toolsBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!toolsPopup.hidden) { closeComposerPopups(); return; }
  openComposerPopup(toolsPopup, toolsBtn);
});
document.addEventListener("click", (e) => {
  if (e.target.closest("#agent-popup") || e.target.closest("#agent-btn")
      || e.target.closest("#model-popup") || e.target.closest("#model-btn")
      || e.target.closest("#tools-popup") || e.target.closest("#tools-btn")
      || e.target.closest("#ws-popup") || e.target.closest("#ws-btn")) return;
  closeComposerPopups();
});
window.addEventListener("resize", closeComposerPopups);
refreshChips();

// ---- 자동 모드 코칭 힌트 ------------------------------------------------
const autoHint = document.getElementById("auto-hint");
const promptEl = document.getElementById("prompt");
let hintTimer = null;

async function updateAutoHint() {
  if (providerInput.value !== "auto") { autoHint.hidden = true; return; }
  try {
    const q = encodeURIComponent(promptEl.value || "");
    const res = await fetch(`/api/recommend?prompt=${q}`);
    if (!res.ok) return;
    const d = await res.json();
    autoHint.innerHTML =
      `<span class="hint-mark">자동 추천</span> ${d.provider} — ${d.reason}`;
    autoHint.hidden = false;
  } catch (_) { /* 무시 */ }
}
promptEl?.addEventListener("input", () => {
  clearTimeout(hintTimer);
  hintTimer = setTimeout(updateAutoHint, 400);
});
updateAutoHint();

// ---- 노트 컨텍스트 메뉴 (···) ------------------------------------------
const dropdown = document.getElementById("note-dropdown");

// ---- 접이식 그룹 폴더 ---------------------------------------------------
// 기본은 접힘. 열어둔 그룹은 localStorage에 저장해 자동 갱신에도 유지한다.
const OPEN_GROUPS_KEY = "aos-open-groups";

function openGroupSet() {
  try { return new Set(JSON.parse(localStorage.getItem(OPEN_GROUPS_KEY)) || []); }
  catch (_) { return new Set(); }
}

function restoreNoteGroups() {
  const open = openGroupSet();
  document.querySelectorAll("#memory .note-group").forEach((g) => {
    g.classList.toggle("open", open.has(g.dataset.group));
  });
}

document.addEventListener("click", (e) => {
  const toggle = e.target.closest(".note-group-toggle");
  if (!toggle) return;
  const group = toggle.closest(".note-group");
  const opened = group.classList.toggle("open");
  const open = openGroupSet();
  opened ? open.add(group.dataset.group) : open.delete(group.dataset.group);
  localStorage.setItem(OPEN_GROUPS_KEY, JSON.stringify([...open]));
});

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target.id === "memory") restoreNoteGroups();
});

function existingGroups() {
  const el = document.getElementById("note-groups");
  if (!el) return [];
  try { return JSON.parse(el.textContent) || []; } catch (_) { return []; }
}

async function postNote(url, path, extra) {
  const body = new URLSearchParams(Object.assign({ path }, extra || {}));
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) { console.error("note action failed", url, res.status); return; }
  const html = await res.text();
  const mem = document.getElementById("memory");
  mem.innerHTML = html;
  restoreNoteGroups();
  if (window.htmx) {
    htmx.process(mem);
    // 노트 삭제/이름변경이 작업큐와 연동되므로 큐도 즉시 갱신
    htmx.ajax("GET", "/partials/jobs", { target: "#jobs", swap: "innerHTML" });
  }
}

function closeDropdown() { dropdown.hidden = true; dropdown.innerHTML = ""; }

function openDropdown(btn) {
  const path = btn.dataset.path;
  const pinned = btn.dataset.pinned === "true";
  const archived = btn.dataset.archived === "true";
  dropdown.innerHTML = "";

  const add = (label, fn, cls) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dd-item" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", (e) => { e.stopPropagation(); closeDropdown(); fn(); });
    dropdown.appendChild(b);
  };
  const sep = () => {
    const d = document.createElement("div"); d.className = "dd-sep"; dropdown.appendChild(d);
  };

  add(pinned ? "고정 해제" : "고정", () => postNote("/notes/pin", path));
  add("이름 변경", () => {
    const name = prompt("새 이름", btn.closest(".note-item")?.querySelector(".note-link")?.textContent.trim());
    if (name) postNote("/notes/rename", path, { name });
  });
  // 그룹으로 이동
  const groupBtn = document.createElement("div");
  groupBtn.className = "dd-item dd-has-sub";
  groupBtn.innerHTML = `<span>그룹으로 이동</span><span class="dd-arrow">›</span>`;
  const sub = document.createElement("div");
  sub.className = "dd-sub";
  const addSub = (label, fn, cls) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "dd-item" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", (e) => { e.stopPropagation(); closeDropdown(); fn(); });
    sub.appendChild(b);
  };
  addSub("새 그룹…", () => {
    const g = prompt("그룹 이름");
    if (g) postNote("/notes/group", path, { group: g });
  });
  for (const g of existingGroups()) {
    addSub(g, () => postNote("/notes/group", path, { group: g }));
  }
  addSub("그룹에서 제거", () => postNote("/notes/group", path, { group: "" }), "dd-muted");
  groupBtn.appendChild(sub);
  dropdown.appendChild(groupBtn);

  add(archived ? "보관 해제" : "보관", () => postNote("/notes/archive", path));
  sep();
  add("삭제", () => {
    if (confirm("이 노트를 삭제할까요? 되돌릴 수 없습니다.")) postNote("/notes/delete", path);
  }, "dd-danger");

  // 앵커는 항상 보이는 note-item 기준 (··· 버튼은 hover 시에만 표시돼 rect가 0일 수 있음)
  const anchor = btn.closest(".note-item") || btn;
  const r = anchor.getBoundingClientRect();
  dropdown.hidden = false;
  const w = dropdown.offsetWidth || 180;
  let left = r.right + window.scrollX - w;
  if (left < 8) left = 8;
  dropdown.style.top = (r.bottom + 2 + window.scrollY) + "px";
  dropdown.style.left = left + "px";
}

document.addEventListener("click", (e) => {
  const dots = e.target.closest(".note-dots");
  if (dots) {
    e.preventDefault(); e.stopPropagation();
    if (!dropdown.hidden && dropdown.dataset.for === dots.dataset.path) { closeDropdown(); return; }
    dropdown.dataset.for = dots.dataset.path;
    openDropdown(dots);
    return;
  }
  if (!e.target.closest("#note-dropdown")) closeDropdown();
});
window.addEventListener("resize", closeDropdown);
document.querySelector(".note-scroll") && document.addEventListener("scroll", closeDropdown, true);

// ---- 작업 위치(workspace) 선택·추가·제거 --------------------------------
const WS_DEFAULT = "기본(연동 안 함)";
let wsSelectedPath = "";

function selectWorkspace(path) {
  wsSelectedPath = path || "";
  const input = document.getElementById("ws-workdir");
  if (input) input.value = wsSelectedPath;
  const label = document.getElementById("ws-label");
  if (label) {
    if (!wsSelectedPath) {
      label.textContent = WS_DEFAULT;
    } else {
      const row = document.querySelector(`.ws-row[data-path="${CSS.escape(wsSelectedPath)}"]`);
      label.textContent = row?.dataset.name || WS_DEFAULT;
    }
  }
  document.querySelectorAll(".ws-row").forEach((row) => {
    const check = row.querySelector(".ws-check");
    if (check) check.textContent = (row.dataset.path || "") === wsSelectedPath ? "✓" : "";
  });
}

function closeWsPopup() {
  const popup = document.getElementById("ws-popup");
  if (popup) popup.hidden = true;
}

function openWsPopup() {
  const btn = document.getElementById("ws-btn");
  const popup = document.getElementById("ws-popup");
  if (!btn || !popup) return;
  closeComposerPopups();
  popup.hidden = false;
  const r = btn.getBoundingClientRect();
  const w = popup.offsetWidth || 240;
  let left = r.left + window.scrollX;
  const maxLeft = window.scrollX + document.documentElement.clientWidth - w - 8;
  if (left > maxLeft) left = Math.max(8, maxLeft);
  popup.style.top = (r.bottom + 6 + window.scrollY) + "px";
  popup.style.left = left + "px";
}

function initWsPicker(preferredPath) {
  const rows = document.querySelectorAll(".ws-row");
  if (!rows.length) return;
  let path = preferredPath ?? wsSelectedPath ?? "";
  if (path && !document.querySelector(`.ws-row[data-path="${CSS.escape(path)}"]`)) path = "";
  selectWorkspace(path);
}

async function postWorkspace(url, data) {
  const prevPath = document.getElementById("ws-workdir")?.value || "";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(data),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let msg = text.slice(0, 300);
    try {
      const j = JSON.parse(text);
      if (typeof j.detail === "string") msg = j.detail;
    } catch (_) { /* plain-text error */ }
    return { ok: false, msg };
  }
  const picker = document.getElementById("workspace-picker");
  picker.innerHTML = await res.text();
  if (window.htmx) htmx.process(picker);
  const nextPath = res.headers.get("X-Workspace-Path") || prevPath;
  initWsPicker(nextPath);
  return { ok: true };
}

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.id === "workspace-picker") initWsPicker();
});

document.addEventListener("click", (e) => {
  if (e.target.closest(".ws-add")) {
    openWsModal();
    return;
  }
  if (e.target.closest(".ws-item-remove")) {
    e.stopPropagation();
    const btn = e.target.closest(".ws-item-remove");
    if (confirm(`'${btn.dataset.name}' 연동을 해제할까요? (폴더 자체는 삭제되지 않습니다)`))
      postWorkspace("/workspaces/remove", { id: btn.dataset.id });
    return;
  }
  if (e.target.closest(".ws-row")) {
    selectWorkspace(e.target.closest(".ws-row").dataset.path);
    closeWsPopup();
    return;
  }
  if (e.target.closest("#ws-btn")) {
    e.stopPropagation();
    const popup = document.getElementById("ws-popup");
    if (popup && !popup.hidden) closeWsPopup();
    else openWsPopup();
  }
});

// ---- 작업 위치 추가 모달 (폴더 탐색 / GitHub) ---------------------------
const wsModal = document.getElementById("ws-modal");
const wsBody = document.getElementById("ws-modal-body");
const wsFoot = document.getElementById("ws-modal-foot");

function openWsModal() {
  wsModal.hidden = false;
  switchTab("folder");
}
function closeWsModal() { wsModal.hidden = true; wsBody.innerHTML = ""; wsFoot.innerHTML = ""; }

document.getElementById("ws-modal-close")?.addEventListener("click", closeWsModal);
wsModal?.addEventListener("click", (e) => { if (e.target === wsModal) closeWsModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && wsModal && !wsModal.hidden) closeWsModal();
});
document.querySelectorAll(".modal-tab").forEach((t) =>
  t.addEventListener("click", () => switchTab(t.dataset.tab)));

function switchTab(tab) {
  document.querySelectorAll(".modal-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === tab));
  if (tab === "folder") loadFolders("");
  else loadGithub();
}

// --- 폴더 탐색 ---
async function loadFolders(path) {
  wsBody.innerHTML = '<div class="modal-loading">불러오는 중…</div>';
  wsFoot.innerHTML = "";
  let d;
  try {
    const res = await fetch("/api/folders?path=" + encodeURIComponent(path));
    d = await res.json();
  } catch (_) { wsBody.innerHTML = '<div class="modal-empty">폴더를 불러오지 못했습니다.</div>'; return; }

  const rows = [];
  if (d.shortcuts?.length) {
    rows.push('<div class="folder-shortcuts-panel">');
    rows.push('<div class="folder-shortcuts-label">바로가기</div>');
    rows.push('<ul class="folder-list folder-shortcuts">');
    for (const s of d.shortcuts) {
      const icon = s.kind === "icloud" || s.kind === "cloud" ? "☁️" : "📁";
      rows.push(
        `<li class="folder-item shortcut" data-path="${s.path.replace(/"/g, "&quot;")}">` +
        `${icon} ${s.name}</li>`
      );
    }
    rows.push("</ul></div>");
  }
  rows.push(`<div class="folder-path" title="${d.path}">${d.path}</div>`);
  if (d.notice) {
    rows.push(`<div class="folder-notice">${d.notice}</div>`);
  }
  rows.push('<ul class="folder-list">');
  if (d.canUp) rows.push(`<li class="folder-item up" data-path="${d.parent}">⬆︎ 상위 폴더</li>`);
  for (const f of d.dirs) {
    const label = f.label || f.name;
    rows.push(
      `<li class="folder-item" data-path="${f.path.replace(/"/g, "&quot;")}">📁 ${label}</li>`
    );
  }
  if (!d.dirs.length && !d.canUp && !d.shortcuts?.length) {
    rows.push('<li class="modal-empty">하위 폴더가 없습니다</li>');
  }
  rows.push("</ul>");
  wsBody.innerHTML = rows.join("");

  wsBody.querySelectorAll(".folder-item").forEach((li) =>
    li.addEventListener("click", () => loadFolders(li.dataset.path)));

  const pickBlocked = Boolean(d.requested);
  wsFoot.innerHTML =
    `<span class="modal-hint">${
      pickBlocked
        ? "요청한 iCloud 폴더가 이 Mac에 없습니다. Finder에서 먼저 내려받은 뒤 다시 선택하세요."
        : "이 폴더에서 작업이 실행됩니다"
    }</span>` +
    `<button type="button" class="btn-primary" id="folder-pick"${
      pickBlocked ? " disabled" : ""
    }>이 폴더 선택</button>`;
  if (!pickBlocked) {
    document.getElementById("folder-pick").addEventListener("click", async () => {
      const r = await postWorkspace("/workspaces/add", { value: d.path });
      if (r.ok) closeWsModal();
      else alert("추가하지 못했습니다.\n" + r.msg);
    });
  }
}

// --- GitHub 리포/브랜치 ---
async function loadGithub() {
  wsBody.innerHTML = '<div class="modal-loading">GitHub 확인 중…</div>';
  wsFoot.innerHTML = "";
  let st;
  try { st = await (await fetch("/api/github/status")).json(); }
  catch (_) { st = { installed: false, loggedIn: false }; }

  if (!st.installed) {
    wsBody.innerHTML = '<div class="modal-empty">gh(GitHub CLI)가 설치되어 있지 않습니다.</div>';
    return;
  }
  if (!st.loggedIn) {
    wsBody.innerHTML = '<div class="modal-empty">GitHub 로그인이 필요합니다.<br>터미널에서 <code>gh auth login</code> 후 다시 시도하세요.</div>';
    return;
  }

  wsBody.innerHTML =
    `<div class="gh-user">로그인: <b>${st.user}</b></div>` +
    `<input class="search gh-filter" id="gh-filter" placeholder="리포 검색…">` +
    `<div class="modal-loading" id="gh-repos-loading">리포 목록 불러오는 중…</div>` +
    `<ul class="gh-repo-list" id="gh-repo-list" hidden></ul>` +
    `<div class="gh-branch-row" id="gh-branch-row" hidden>` +
      `<label>브랜치 <select class="ws-select" id="gh-branch"></select></label>` +
    `</div>`;

  let repos = [];
  try { repos = (await (await fetch("/api/github/repos")).json()).repos; }
  catch (_) {
    document.getElementById("gh-repos-loading").textContent = "리포 목록을 불러오지 못했습니다.";
    return;
  }
  const listEl = document.getElementById("gh-repo-list");
  const loadingEl = document.getElementById("gh-repos-loading");
  loadingEl.hidden = true; listEl.hidden = false;

  let selectedRepo = null;
  function renderRepos(filter) {
    listEl.innerHTML = "";
    const f = (filter || "").toLowerCase();
    for (const r of repos) {
      if (f && !r.repo.toLowerCase().includes(f)) continue;
      const li = document.createElement("li");
      li.className = "gh-repo-item" + (r.repo === selectedRepo ? " active" : "");
      li.innerHTML = `<span class="gh-repo-name">${r.repo}</span>` +
        (r.private ? '<span class="gh-badge">private</span>' : "");
      li.addEventListener("click", () => pickRepo(r.repo));
      listEl.appendChild(li);
    }
    if (!listEl.children.length) listEl.innerHTML = '<li class="modal-empty">검색 결과 없음</li>';
  }
  document.getElementById("gh-filter").addEventListener("input", (e) => renderRepos(e.target.value));
  renderRepos("");

  const branchRow = document.getElementById("gh-branch-row");
  const branchSel = document.getElementById("gh-branch");

  async function pickRepo(repo) {
    selectedRepo = repo;
    renderRepos(document.getElementById("gh-filter").value);
    branchRow.hidden = false;
    branchSel.innerHTML = '<option>불러오는 중…</option>';
    wsFoot.innerHTML = "";
    let b;
    try { b = await (await fetch("/api/github/branches?repo=" + encodeURIComponent(repo))).json(); }
    catch (_) { branchSel.innerHTML = '<option>브랜치 조회 실패</option>'; return; }
    branchSel.innerHTML = "";
    for (const name of b.branches) {
      const o = document.createElement("option");
      o.value = name; o.textContent = name;
      if (name === b.default) o.selected = true;
      branchSel.appendChild(o);
    }
    wsFoot.innerHTML =
      `<span class="modal-hint">선택한 브랜치를 클론해 추가합니다</span>` +
      `<button type="button" class="btn-primary" id="gh-add">클론해서 추가</button>`;
    document.getElementById("gh-add").addEventListener("click", async () => {
      const btn = document.getElementById("gh-add");
      btn.disabled = true; btn.textContent = "클론 중…";
      const r = await postWorkspace("/workspaces/add-github",
        { repo: selectedRepo, branch: branchSel.value });
      if (r.ok) closeWsModal();
      else { btn.disabled = false; btn.textContent = "클론해서 추가"; alert("추가하지 못했습니다.\n" + r.msg); }
    });
  }
}
