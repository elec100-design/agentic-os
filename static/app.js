"use strict";

// ---- 사이드바 토글 (모바일) --------------------------------------------
document.getElementById("nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});

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

// ---- 프로바이더 선택 + 모델 피커 ---------------------------------------
const providerInput = document.getElementById("provider-input");
const modelInput = document.getElementById("model-input");
const picker = document.getElementById("provider-picker");
const modelPopup = document.getElementById("model-popup");
const chosenModel = {};                     // provider -> model id ("" = 기본값)

function defaultModel(p) {
  const list = (MODELS[p] || []);
  const d = list.find((m) => m.default) || list[0];
  return d ? (d.model || "") : "";
}
function modelLabel(p, id) {
  const m = (MODELS[p] || []).find((x) => (x.model || "") === (id || ""));
  return m ? m.label : "";
}

function selectProvider(p, openPopup) {
  providerInput.value = p;
  for (const b of picker.querySelectorAll(".pill")) {
    b.classList.toggle("active", b.dataset.provider === p);
  }
  const id = p in chosenModel ? chosenModel[p] : "";
  modelInput.value = id;
  // 선택 프로바이더의 pill에 현재 모델 라벨 표시 (기본값이 아니면)
  const pill = picker.querySelector(`.pill[data-provider="${p}"]`);
  const label = pill?.querySelector(".pill-model");
  if (label) label.textContent = (id && id !== defaultModel(p)) ? modelLabel(p, id) : "";
  updateAutoHint();
  if (openPopup && (MODELS[p] || []).length > 1) openModelPopup(pill, p);
  else closeModelPopup();
}

function openModelPopup(pill, p) {
  const cur = p in chosenModel ? chosenModel[p] : "";
  modelPopup.innerHTML = "";
  const head = document.createElement("div");
  head.className = "popup-head";
  head.textContent = "모델";
  modelPopup.appendChild(head);
  for (const m of MODELS[p]) {
    const id = m.model || "";
    const row = document.createElement("button");
    row.type = "button";
    row.className = "popup-row";
    const sel = (id === cur) || (!cur && m.default);
    row.innerHTML =
      `<span class="popup-check">${sel ? "✓" : ""}</span>` +
      `<span class="popup-label">${m.label}</span>` +
      (m.default ? `<span class="popup-tag">기본값</span>` : "");
    row.addEventListener("click", () => {
      chosenModel[p] = id;
      selectProvider(p, false);
    });
    modelPopup.appendChild(row);
  }
  const r = pill.getBoundingClientRect();
  modelPopup.hidden = false;
  modelPopup.style.top = (r.bottom + 6 + window.scrollY) + "px";
  modelPopup.style.left = (r.left + window.scrollX) + "px";
}
function closeModelPopup() { if (modelPopup) modelPopup.hidden = true; }

picker?.addEventListener("click", (e) => {
  const pill = e.target.closest(".pill");
  if (!pill) return;
  const p = pill.dataset.provider;
  const reopen = providerInput.value === p && modelPopup.hidden;
  selectProvider(p, true && (reopen || providerInput.value !== p));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#model-popup") && !e.target.closest(".provider-picker")) {
    closeModelPopup();
  }
});

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

// ---- 작업 위치(workspace) 추가/제거 ------------------------------------
async function postWorkspace(url, data) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(data),
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => "");
    return { ok: false, msg: msg.slice(0, 300) };
  }
  const picker = document.getElementById("workspace-picker");
  picker.innerHTML = await res.text();
  if (window.htmx) htmx.process(picker);
  // 방금 추가한 위치를 자동 선택
  const path = res.headers.get("X-Workspace-Path");
  if (path) {
    const sel = document.getElementById("ws-select");
    if (sel) sel.value = path;
  }
  return { ok: true };
}

document.addEventListener("click", (e) => {
  if (e.target.closest(".ws-add")) {
    openWsModal();
  } else if (e.target.closest(".ws-remove")) {
    const sel = document.getElementById("ws-select");
    const opt = sel?.selectedOptions[0];
    if (!opt || !opt.dataset.id) { alert("연동 해제할 작업 위치를 먼저 선택하세요."); return; }
    if (confirm(`'${opt.textContent.trim()}' 연동을 해제할까요? (폴더 자체는 삭제되지 않습니다)`))
      postWorkspace("/workspaces/remove", { id: opt.dataset.id });
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
  rows.push(`<div class="folder-path" title="${d.path}">${d.path}</div>`);
  rows.push('<ul class="folder-list">');
  if (d.canUp) rows.push(`<li class="folder-item up" data-path="${d.parent}">⬆︎ 상위 폴더</li>`);
  for (const f of d.dirs) {
    rows.push(`<li class="folder-item" data-path="${f.path.replace(/"/g, "&quot;")}">📁 ${f.name}</li>`);
  }
  if (!d.dirs.length && !d.canUp) rows.push('<li class="modal-empty">하위 폴더가 없습니다</li>');
  rows.push("</ul>");
  wsBody.innerHTML = rows.join("");

  wsBody.querySelectorAll(".folder-item").forEach((li) =>
    li.addEventListener("click", () => loadFolders(li.dataset.path)));

  wsFoot.innerHTML =
    `<span class="modal-hint">이 폴더에서 작업이 실행됩니다</span>` +
    `<button type="button" class="btn-primary" id="folder-pick">이 폴더 선택</button>`;
  document.getElementById("folder-pick").addEventListener("click", async () => {
    const r = await postWorkspace("/workspaces/add", { value: d.path });
    if (r.ok) closeWsModal();
    else alert("추가하지 못했습니다.\n" + r.msg);
  });
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
