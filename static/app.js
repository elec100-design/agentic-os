"use strict";

// ---- 사이드바 토글 (모바일) --------------------------------------------
document.getElementById("nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});
// 사이드바 안의 ← 버튼 — 서랍을 다시 왼쪽으로 밀어 넣는다.
document.getElementById("nav-close")?.addEventListener("click", () => {
  document.body.classList.remove("nav-open");
});
// 서랍 밖을 탭하면 닫힌다 — 모바일에서 버튼을 다시 찾아 누르지 않아도 되게.
document.addEventListener("click", (e) => {
  if (!document.body.classList.contains("nav-open")) return;
  if (e.target.closest(".sidebar") || e.target.closest("#nav-toggle")) return;
  document.body.classList.remove("nav-open");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.body.classList.remove("nav-open");
});

// ---- 테마 토글 (라이트/다크, localStorage 유지) -------------------------
// 저장값이 없으면 OS 설정(prefers-color-scheme)을 따르고, 한 번 누르면
// 명시값(data-theme)으로 고정된다. head의 인라인 스크립트가 페인트 전에
// data-theme를 적용해 깜빡임(FOUC)을 막는다.
const THEME_KEY = "aos-theme";
const themeToggle = document.getElementById("theme-toggle");
function effectiveTheme() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit) return explicit;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function syncThemeLabel() {
  if (themeToggle) themeToggle.dataset.theme = effectiveTheme();
}
themeToggle?.addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* 무시 */ }
  syncThemeLabel();
});
syncThemeLabel();

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

// ---- 컴포저 (첨부·에이전트·모델·작업위치) --------------------------------
// 한 페이지에 컴포저가 둘 이상 있을 수 있다(홈: 우측 작업 채팅 + 좌측 비전보드
// 채팅). 그래서 요소는 전부 폼 안에서 class로 찾고, 선택 상태(chosenModel,
// 작업 위치)도 폼마다 따로 둔다.
const COMPOSERS = [];

// 팝업에 노출할 에이전트: 자동 + 활성 에이전트(서버가 /setup 선택을 반영해
// AGENT_ORDER로 주입) + 협의. 협의(council)는 활성 멤버가 2명 이상일 때만
// 노출되고, 모델 선택이 없어(MODELS에 없음) 모델 칩은 자동으로 숨겨진다.
const AGENTS = [{ id: "auto", name: t("자동") }]
  .concat(
    AGENT_ORDER.filter((p) => MODELS[p]).map((p) => ({ id: p, name: agentName(p) }))
  )
  .concat(COUNCIL_ENABLED ? [{ id: "council", name: t("협의") }] : []);

function agentName(p) {
  if (p === "auto") return t("자동");
  if (p === "council") return t("협의");
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

const WS_DEFAULT = t("기본(연동 안 함)");

function mountComposer(form) {
  const providerInput = form.querySelector(".composer-provider");
  const modelInput = form.querySelector(".composer-model");
  // 컨트롤이 없는 컴포저(후속 지시·새 스레드 등)는 마운트하지 않는다
  if (!providerInput || !modelInput) return null;

  const textarea = form.querySelector("textarea");
  const fileInput = form.querySelector(".composer-files");
  const chips = form.querySelector(".file-chips");
  const agentBtn = form.querySelector(".agent-btn");
  const agentLabel = form.querySelector(".agent-label");
  const agentPopup = form.querySelector(".agent-popup:not(.model-popup)");
  const modelBtn = form.querySelector(".model-btn");
  const modelChipLabel = form.querySelector(".model-label");
  const modelPopup = form.querySelector(".model-popup");
  const toolsBtn = form.querySelector(".tools-btn");
  const toolsPopup = form.querySelector(".tools-popup");
  const autoHint = form.querySelector(".auto-hint");
  const picker = form.querySelector(".workspace-picker");

  const chosenModel = {};       // provider -> model id ("" = 기본값)
  let wsSelectedPath = "";
  let hintTimer = null;

  // ── ⌘/Ctrl+Enter 전송 (채팅 UX 표준) ──────────────────────────────────
  textarea?.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  // ── 파일 첨부 칩 + 드래그 앤 드롭 ─────────────────────────────────────
  function renderChips() {
    if (!chips || !fileInput) return;
    chips.innerHTML = "";
    for (const f of fileInput.files) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = f.name;
      chips.appendChild(chip);
    }
  }
  if (fileInput) {
    fileInput.addEventListener("change", renderChips);
    form.addEventListener("dragover", (e) => {
      e.preventDefault();
      form.classList.add("dragging");
    });
    form.addEventListener("dragleave", (e) => {
      if (e.target === form) form.classList.remove("dragging");
    });
    form.addEventListener("drop", (e) => {
      e.preventDefault();
      form.classList.remove("dragging");
      const dt = new DataTransfer();
      for (const f of fileInput.files) dt.items.add(f);
      for (const f of e.dataTransfer.files) dt.items.add(f);
      fileInput.files = dt.files;
      renderChips();
    });
  }

  // ── 에이전트/모델 칩 ──────────────────────────────────────────────────
  function refreshChipLabels() {
    if (!agentLabel || !modelBtn || !modelChipLabel) return;
    const p = providerInput.value;
    agentLabel.textContent = agentName(p);
    if (modelChipVisible(p)) {
      modelBtn.hidden = false;
      const id = chosenModel[p] || defaultModel(p);
      modelChipLabel.textContent = modelLabel(p, id) || t("모델");
    } else {
      modelBtn.hidden = true;
    }
  }

  function selectProvider(p) {
    providerInput.value = p;
    modelInput.value = p === "auto" ? "" : (chosenModel[p] || "");
    refreshChipLabels();
    updateAutoHint();
  }

  function selectModel(p, id) {
    chosenModel[p] = id;
    modelInput.value = id;
    refreshChipLabels();
  }

  function renderAgentPopup() {
    const cur = providerInput.value;
    agentPopup.innerHTML = "";
    const head = document.createElement("div");
    head.className = "popup-head";
    head.textContent = t("에이전트");
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
          ? `<span class="popup-tag" title="${t("여러 에이전트가 제안·비평하고 하나가 종합합니다 (사용량 다중 소모)")}">${t("토론")}</span>`
          : multi ? `<span class="popup-tag">${t("모델")}</span>` : "");
      row.addEventListener("click", () => { selectProvider(a.id); closeAllComposerPopups(); });
      agentPopup.appendChild(row);
    }
  }

  function renderModelPopup() {
    const cur = providerInput.value;
    modelPopup.innerHTML = "";
    const head = document.createElement("div");
    head.className = "popup-head";
    head.textContent = agentName(cur) + " " + t("모델");
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
        (m.default ? `<span class="popup-tag">${t("기본값")}</span>` : "");
      row.addEventListener("click", () => { selectModel(cur, id); closeAllComposerPopups(); });
      modelPopup.appendChild(row);
    }
  }

  function closePopups() {
    if (agentPopup) agentPopup.hidden = true;
    if (modelPopup) modelPopup.hidden = true;
    if (toolsPopup) toolsPopup.hidden = true;
    const wsPopup = picker?.querySelector(".ws-popup");
    if (wsPopup) wsPopup.hidden = true;
  }

  function openPopup(popup, anchor, fallbackW) {
    closeAllComposerPopups();
    popup.hidden = false;
    placePopup(popup, anchor, fallbackW || 200);
  }

  agentBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!agentPopup.hidden) { closeAllComposerPopups(); return; }
    renderAgentPopup();
    openPopup(agentPopup, agentBtn);
  });
  modelBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!modelPopup.hidden) { closeAllComposerPopups(); return; }
    renderModelPopup();
    openPopup(modelPopup, modelBtn);
  });
  toolsBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!toolsPopup.hidden) { closeAllComposerPopups(); return; }
    openPopup(toolsPopup, toolsBtn);
  });

  // ── 자동 모드 코칭 힌트 (힌트 UI가 있는 컴포저에서만) ──────────────────
  async function updateAutoHint() {
    if (!autoHint) return;
    if (providerInput.value !== "auto") { autoHint.hidden = true; return; }
    try {
      const q = encodeURIComponent(textarea?.value || "");
      const res = await fetch(`/api/recommend?prompt=${q}`);
      if (!res.ok) return;
      const d = await res.json();
      autoHint.innerHTML =
        `<span class="hint-mark">${t("자동 추천")}</span> ${d.provider} — ${d.reason}`;
      autoHint.hidden = false;
    } catch (_) { /* 무시 */ }
  }
  if (autoHint) {
    textarea?.addEventListener("input", () => {
      clearTimeout(hintTimer);
      hintTimer = setTimeout(updateAutoHint, 400);
    });
  }

  // ── 작업 위치(폴더/리포) 선택 ─────────────────────────────────────────
  function selectWorkspace(path) {
    wsSelectedPath = path || "";
    const input = picker?.querySelector(".ws-workdir");
    if (input) input.value = wsSelectedPath;
    const label = picker?.querySelector(".ws-label");
    if (label) {
      const row = wsSelectedPath
        ? picker.querySelector(`.ws-row[data-path="${CSS.escape(wsSelectedPath)}"]`)
        : null;
      label.textContent = row?.dataset.name || WS_DEFAULT;
    }
    picker?.querySelectorAll(".ws-row").forEach((row) => {
      const check = row.querySelector(".ws-check");
      if (check) check.textContent = (row.dataset.path || "") === wsSelectedPath ? "✓" : "";
    });
  }

  function initWsPicker(preferredPath) {
    if (!picker || !picker.querySelector(".ws-row")) return;
    let path = preferredPath ?? wsSelectedPath ?? "";
    if (path && !picker.querySelector(`.ws-row[data-path="${CSS.escape(path)}"]`)) path = "";
    selectWorkspace(path);
  }

  function openWsPopup() {
    const btn = picker?.querySelector(".ws-chip");
    const popup = picker?.querySelector(".ws-popup");
    if (!btn || !popup) return;
    openPopup(popup, btn, 240);
  }

  const handle = {
    form, picker, closePopups, initWsPicker, openWsPopup,
    currentWorkdir: () => wsSelectedPath,
    // 전송 후 첨부를 비운다 (비전보드 채팅처럼 폼이 남아 있는 경우)
    clearAttachments() {
      if (fileInput) { fileInput.value = ""; renderChips(); }
    },
  };
  form.__composer = handle;
  COMPOSERS.push(handle);

  refreshChipLabels();
  updateAutoHint();
  return handle;
}

function closeAllComposerPopups() {
  for (const c of COMPOSERS) c.closePopups();
}

// 버튼 아래(공간이 없으면 위)에 팝업을 띄운다.
// 컴포저는 홈에서 우측 레일(position:absolute) 안에, 다른 화면에서는 문서
// 최상위에 있으므로 뷰포트 좌표로 자리를 잡은 뒤 offsetParent 기준으로 변환한다.
function placePopup(popup, anchor, fallbackW) {
  const r = anchor.getBoundingClientRect();
  const w = popup.offsetWidth || fallbackW;
  const h = popup.offsetHeight || 160;
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;

  let left = Math.max(8, Math.min(r.left, vw - w - 8));
  let top = r.bottom + 6;
  if (top + h > vh - 8) top = Math.max(8, r.top - h - 6);

  const host = popup.offsetParent;
  if (host) {
    const hr = host.getBoundingClientRect();
    left -= hr.left;
    top -= hr.top;
  } else {
    left += window.scrollX;
    top += window.scrollY;
  }
  popup.style.top = `${top}px`;
  popup.style.left = `${left}px`;
}

// 팝업/앵커 밖을 누르면 모두 닫는다
document.addEventListener("click", (e) => {
  if (e.target.closest(".agent-popup") || e.target.closest(".agent-btn")
      || e.target.closest(".model-popup") || e.target.closest(".model-btn")
      || e.target.closest(".tools-popup") || e.target.closest(".tools-btn")
      || e.target.closest(".ws-popup") || e.target.closest(".ws-chip")) return;
  closeAllComposerPopups();
});
window.addEventListener("resize", closeAllComposerPopups);

// 페이지에 있는 모든 컴포저를 마운트한다 (컨트롤이 없는 폼은 알아서 건너뛴다)
document.querySelectorAll("form.composer").forEach(mountComposer);

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

  add(pinned ? t("고정 해제") : t("고정"), () => postNote("/notes/pin", path));
  add(t("이름 변경"), () => {
    const name = prompt(t("새 이름"), btn.closest(".note-item")?.querySelector(".note-link")?.textContent.trim());
    if (name) postNote("/notes/rename", path, { name });
  });
  // 그룹으로 이동
  const groupBtn = document.createElement("div");
  groupBtn.className = "dd-item dd-has-sub";
  groupBtn.innerHTML = `<span>${t("그룹으로 이동")}</span><span class="dd-arrow">›</span>`;
  const sub = document.createElement("div");
  sub.className = "dd-sub";
  const addSub = (label, fn, cls) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "dd-item" + (cls ? " " + cls : "");
    b.textContent = label;
    b.addEventListener("click", (e) => { e.stopPropagation(); closeDropdown(); fn(); });
    sub.appendChild(b);
  };
  addSub(t("새 그룹…"), () => {
    const g = prompt(t("그룹 이름"));
    if (g) postNote("/notes/group", path, { group: g });
  });
  for (const g of existingGroups()) {
    addSub(g, () => postNote("/notes/group", path, { group: g }));
  }
  addSub(t("그룹에서 제거"), () => postNote("/notes/group", path, { group: "" }), "dd-muted");
  groupBtn.appendChild(sub);
  dropdown.appendChild(groupBtn);

  add(archived ? t("보관 해제") : t("보관"), () => postNote("/notes/archive", path));
  sep();
  add(t("삭제"), () => {
    if (confirm(t("이 노트를 삭제할까요? 되돌릴 수 없습니다."))) postNote("/notes/delete", path);
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
// 노트 목록이 우측 레일 같은 내부 스크롤 컨테이너 안에 있으면 그 스크롤에도
// 메뉴가 따라오지 않는다(메뉴는 body 기준 절대배치) — 어떤 조상이 스크롤되든 닫는다.
document.addEventListener("scroll", closeDropdown, true);

// ---- 작업 위치(workspace) 추가·제거 -------------------------------------
// 선택 상태는 컴포저마다 따로다(mountComposer). 목록 자체는 전역이라 추가·제거
// 결과는 페이지의 모든 피커에 반영한다.
function composerOf(el) {
  const form = el.closest("form.composer");
  return form?.__composer || null;
}

async function postWorkspace(url, data) {
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
  const html = await res.text();
  const added = res.headers.get("X-Workspace-Path");
  for (const c of COMPOSERS) {
    if (!c.picker) continue;
    // 새로 추가한 위치는 모달을 연 컴포저에서만 바로 선택되게 하고, 나머지는
    // 각자 고르고 있던 경로를 유지한다(없어졌으면 initWsPicker가 기본으로 되돌림).
    const prev = c.currentWorkdir();
    c.picker.innerHTML = html;
    if (window.htmx) htmx.process(c.picker);
    c.initWsPicker(added && c === wsModalOwner ? added : prev);
  }
  return { ok: true };
}

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.classList?.contains("workspace-picker")) {
    composerOf(e.target)?.initWsPicker();
  }
});

document.addEventListener("click", (e) => {
  const addBtn = e.target.closest(".ws-add");
  if (addBtn) {
    wsModalOwner = composerOf(addBtn);
    openWsModal();
    return;
  }
  if (e.target.closest(".ws-item-remove")) {
    e.stopPropagation();
    const btn = e.target.closest(".ws-item-remove");
    const msg = window.LANG === "en"
      ? `Remove the '${btn.dataset.name}' workspace? (The folder itself is not deleted.)`
      : `'${btn.dataset.name}' 연동을 해제할까요? (폴더 자체는 삭제되지 않습니다)`;
    if (confirm(msg))
      postWorkspace("/workspaces/remove", { id: btn.dataset.id });
    return;
  }
  const row = e.target.closest(".ws-row");
  if (row) {
    composerOf(row)?.initWsPicker(row.dataset.path || "");
    closeAllComposerPopups();
    return;
  }
  const wsChip = e.target.closest(".ws-chip");
  if (wsChip) {
    e.stopPropagation();
    const popup = wsChip.closest(".workspace-picker")?.querySelector(".ws-popup");
    if (popup && !popup.hidden) closeAllComposerPopups();
    else composerOf(wsChip)?.openWsPopup();
  }
});

// ---- 작업 위치 추가 모달 (폴더 탐색 / GitHub) ---------------------------
const wsModal = document.getElementById("ws-modal");
const wsBody = document.getElementById("ws-modal-body");
const wsFoot = document.getElementById("ws-modal-foot");
// 모달은 페이지에 하나뿐이라, 어느 컴포저의 ＋ 로 열었는지 기억해 둔다
let wsModalOwner = null;

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
    rows.push(`<div class="folder-shortcuts-label">${t("바로가기")}</div>`);
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
  if (d.canUp) rows.push(`<li class="folder-item up" data-path="${d.parent}">⬆︎ ${t("상위 폴더")}</li>`);
  for (const f of d.dirs) {
    const label = f.label || f.name;
    rows.push(
      `<li class="folder-item" data-path="${f.path.replace(/"/g, "&quot;")}">📁 ${label}</li>`
    );
  }
  if (!d.dirs.length && !d.canUp && !d.shortcuts?.length) {
    rows.push(`<li class="modal-empty">${t("하위 폴더가 없습니다")}</li>`);
  }
  rows.push("</ul>");
  wsBody.innerHTML = rows.join("");

  wsBody.querySelectorAll(".folder-item").forEach((li) =>
    li.addEventListener("click", () => loadFolders(li.dataset.path)));

  const pickBlocked = Boolean(d.requested);
  wsFoot.innerHTML =
    `<span class="modal-hint">${
      pickBlocked
        ? t("요청한 iCloud 폴더가 이 Mac에 없습니다. Finder에서 먼저 내려받은 뒤 다시 선택하세요.")
        : t("이 폴더에서 작업이 실행됩니다")
    }</span>` +
    `<button type="button" class="btn-primary" id="folder-pick"${
      pickBlocked ? " disabled" : ""
    }>${t("이 폴더 선택")}</button>`;
  if (!pickBlocked) {
    document.getElementById("folder-pick").addEventListener("click", async () => {
      const r = await postWorkspace("/workspaces/add", { value: d.path });
      if (r.ok) closeWsModal();
      else alert(t("추가하지 못했습니다.") + "\n" + r.msg);
    });
  }
}

// --- GitHub 리포/브랜치 ---
async function loadGithub() {
  wsBody.innerHTML = `<div class="modal-loading">${t("GitHub 확인 중…")}</div>`;
  wsFoot.innerHTML = "";
  let st;
  try { st = await (await fetch("/api/github/status")).json(); }
  catch (_) { st = { installed: false, loggedIn: false }; }

  if (!st.installed) {
    wsBody.innerHTML = `<div class="modal-empty">${t("gh(GitHub CLI)가 설치되어 있지 않습니다.")}</div>`;
    return;
  }
  if (!st.loggedIn) {
    wsBody.innerHTML = `<div class="modal-empty">${t("GitHub 로그인이 필요합니다.<br>터미널에서 <code>gh auth login</code> 후 다시 시도하세요.")}</div>`;
    return;
  }

  wsBody.innerHTML =
    `<div class="gh-user">${t("로그인:")} <b>${st.user}</b></div>` +
    `<input class="search gh-filter" id="gh-filter" placeholder="${t("리포 검색…")}">` +
    `<div class="modal-loading" id="gh-repos-loading">${t("리포 목록 불러오는 중…")}</div>` +
    `<ul class="gh-repo-list" id="gh-repo-list" hidden></ul>` +
    `<div class="gh-branch-row" id="gh-branch-row" hidden>` +
      `<label>${t("브랜치")} <select class="ws-select" id="gh-branch"></select></label>` +
    `</div>`;

  let repos = [];
  try { repos = (await (await fetch("/api/github/repos")).json()).repos; }
  catch (_) {
    document.getElementById("gh-repos-loading").textContent = t("리포 목록을 불러오지 못했습니다.");
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
    if (!listEl.children.length) listEl.innerHTML = `<li class="modal-empty">${t("검색 결과 없음")}</li>`;
  }
  document.getElementById("gh-filter").addEventListener("input", (e) => renderRepos(e.target.value));
  renderRepos("");

  const branchRow = document.getElementById("gh-branch-row");
  const branchSel = document.getElementById("gh-branch");

  async function pickRepo(repo) {
    selectedRepo = repo;
    renderRepos(document.getElementById("gh-filter").value);
    branchRow.hidden = false;
    branchSel.innerHTML = `<option>${t("불러오는 중…")}</option>`;
    wsFoot.innerHTML = "";
    let b;
    try { b = await (await fetch("/api/github/branches?repo=" + encodeURIComponent(repo))).json(); }
    catch (_) { branchSel.innerHTML = `<option>${t("브랜치 조회 실패")}</option>`; return; }
    branchSel.innerHTML = "";
    for (const name of b.branches) {
      const o = document.createElement("option");
      o.value = name; o.textContent = name;
      if (name === b.default) o.selected = true;
      branchSel.appendChild(o);
    }
    wsFoot.innerHTML =
      `<span class="modal-hint">${t("선택한 브랜치를 클론해 추가합니다")}</span>` +
      `<button type="button" class="btn-primary" id="gh-add">${t("클론해서 추가")}</button>`;
    document.getElementById("gh-add").addEventListener("click", async () => {
      const btn = document.getElementById("gh-add");
      btn.disabled = true; btn.textContent = t("클론 중…");
      const r = await postWorkspace("/workspaces/add-github",
        { repo: selectedRepo, branch: branchSel.value });
      if (r.ok) closeWsModal();
      else { btn.disabled = false; btn.textContent = t("클론해서 추가"); alert(t("추가하지 못했습니다.") + "\n" + r.msg); }
    });
  }
}

// ---- 취소된 프로젝트 삭제 (비전 보드 목록) -------------------------------
function flashBoard(msg, kind) {
  const el = document.getElementById("board-flash");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("ok", kind === "ok");
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 4000);
}

function refreshProjects() {
  const el = document.getElementById("projects");
  if (el && window.htmx) htmx.trigger(el, "load");
}

document.body.addEventListener("click", async (e) => {
  const delBtn = e.target.closest("[data-del-project]");
  if (delBtn) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(t("이 프로젝트를 삭제할까요? 되돌릴 수 없습니다."))) return;
    delBtn.disabled = true;
    try {
      const res = await fetch(`/projects/${delBtn.dataset.delProject}/delete-cancelled`, { method: "POST" });
      if (!res.ok) {
        let msg = t("삭제하지 못했습니다");
        try { msg = (await res.json()).detail || msg; } catch (_) { /* 본문 없음 */ }
        flashBoard(msg);
      }
    } catch (_) {
      flashBoard(t("서버에 연결하지 못했습니다"));
    }
    refreshProjects();
    return;
  }

  const clearBtn = e.target.closest("#clear-cancelled");
  if (clearBtn) {
    if (!confirm(t("취소된 프로젝트를 모두 삭제할까요? 되돌릴 수 없습니다."))) return;
    clearBtn.disabled = true;
    try {
      const res = await fetch("/projects/cancelled/clear", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        flashBoard(t("취소된 프로젝트 {n}개를 삭제했습니다").replace("{n}", data.deleted), "ok");
      } else {
        flashBoard(t("삭제하지 못했습니다"));
      }
    } catch (_) {
      flashBoard(t("서버에 연결하지 못했습니다"));
    }
    refreshProjects();
  }
});
