// 첫 실행 셋업 위저드 — 환영 → 에이전트 선택(설치 감지) → 로그인 안내 → 완료.
// 서버는 /api/setup/status(감지)와 /api/setup/complete(저장)만 제공하고,
// 단계 전환·선택 상태는 전부 이 파일이 관리한다.
"use strict";

const ORDER = ["claude", "antigravity", "grok", "hermes"];
const state = {
  step: SETUP.completed ? 2 : 1,   // 재설정으로 다시 온 경우 선택 단계부터
  status: null,                     // /api/setup/status 결과
  selected: new Set(SETUP.completed ? SETUP.enabled : []),
  defaulted: SETUP.completed,       // 선택 기본값을 이미 정했는가
};

const body = document.getElementById("setup-body");
const nextBtn = document.getElementById("setup-next");
const backBtn = document.getElementById("setup-back");
const skipBtn = document.getElementById("setup-skip");
const errBox = document.getElementById("setup-error");

async function fetchStatus() {
  const r = await fetch("/api/setup/status");
  state.status = await r.json();
  // 첫 방문 기본값: 설치된 CLI 전체, 하나도 없으면 전체(앱은 계속 쓸 수 있게)
  if (!state.defaulted) {
    const installed = ORDER.filter((p) => state.status.providers[p]?.installed);
    (installed.length ? installed : ORDER).forEach((p) => state.selected.add(p));
    state.defaulted = true;
  }
}

function showError(msg) {
  errBox.textContent = msg || "";
  errBox.hidden = !msg;
}

function mark() {
  return `<svg class="spark" viewBox="0 0 24 24" aria-hidden="true"><use href="#spark-mark"/></svg>`;
}

// ---- 단계별 렌더 ---------------------------------------------------------

function renderWelcome() {
  body.innerHTML = `
    <div class="setup-hero">
      ${mark()}
      <h1>Agentic OS에 오신 것을 환영합니다</h1>
      <p class="setup-sub">구독 중인 AI CLI들을 하나의 대시보드로 —
      실측 남은 사용량으로 자동 배분하고, 결과는 노트로 쌓입니다.</p>
    </div>
    <ul class="setup-features">
      <li><span class="feat-ico">⇄</span><div><b>자동 라우팅</b><span>남은 사용량이 가장 많은 에이전트로 작업을 배분합니다</span></div></li>
      <li><span class="feat-ico">▤</span><div><b>작업 큐</b><span>사용 제한에 걸리면 큐에 두었다가 자동으로 재개합니다</span></div></li>
      <li><span class="feat-ico">✎</span><div><b>노트 메모리</b><span>모든 결과가 마크다운 노트로 저장되고 이어서 작업할 수 있습니다</span></div></li>
      <li><span class="feat-ico">⚖</span><div><b>협의 모드</b><span>여러 에이전트가 제안·비평하고 하나가 종합합니다</span></div></li>
    </ul>`;
}

function badge(p) {
  const st = state.status?.providers?.[p];
  if (!st) return `<span class="pc-badge">확인 중…</span>`;
  return st.installed
    ? `<span class="pc-badge badge-ok">✓ 설치됨</span>`
    : `<span class="pc-badge badge-miss" title="${st.installHint || ""}">✗ 없음</span>`;
}

function renderProviders() {
  const cards = ORDER.map((p) => {
    const st = state.status?.providers?.[p] || {};
    const sel = state.selected.has(p) ? " selected" : "";
    const warn = st.installed === false && state.selected.has(p)
      ? `<p class="pc-warn">CLI가 없으면 이 에이전트의 작업은 실패합니다 — 설치: <code>${st.installHint || ""}</code></p>` : "";
    return `
      <button type="button" class="provider-card${sel}" data-provider="${p}" aria-pressed="${state.selected.has(p)}">
        <span class="pc-mono">${(st.label || p).charAt(0)}</span>
        <span class="pc-info">
          <span class="pc-name">${st.label || p} <em>${st.vendor || ""}</em></span>
          <span class="pc-desc">${st.desc || ""}</span>
          ${warn}
        </span>
        ${badge(p)}
      </button>`;
  }).join("");
  body.innerHTML = `
    <div class="setup-head-row">
      <h2>사용하는 에이전트를 선택하세요</h2>
      <button type="button" class="btn-ghost" id="recheck">재확인</button>
    </div>
    <p class="setup-sub2">설치가 감지된 CLI는 자동으로 선택됩니다. 나중에 ⚙︎ 에이전트 설정에서 바꿀 수 있어요.</p>
    <div class="provider-grid">${cards}</div>`;
  body.querySelectorAll(".provider-card").forEach((card) => {
    card.addEventListener("click", () => {
      const p = card.dataset.provider;
      state.selected.has(p) ? state.selected.delete(p) : state.selected.add(p);
      renderProviders();
      syncFooter();
    });
  });
  document.getElementById("recheck").addEventListener("click", recheck);
}

function renderAuth() {
  const rows = ORDER.filter((p) => state.selected.has(p)).map((p) => {
    const st = state.status?.providers?.[p] || {};
    const cmd = st.authCmd
      ? `<code class="auth-cmd">${st.authCmd}</code>` : `<span class="pc-badge badge-ok">로그인 불필요</span>`;
    return `
      <div class="auth-row">
        <span class="pc-mono">${(st.label || p).charAt(0)}</span>
        <div class="auth-info">
          <span class="pc-name">${st.label || p} ${cmd}</span>
          <span class="pc-desc">${st.authHint || ""}</span>
        </div>
        ${badge(p)}
      </div>`;
  }).join("");
  const tools = Object.entries(state.status?.tools || {}).map(([name, t]) =>
    `<span class="chip tool-chip${t.installed ? " ok" : ""}" title="${t.desc}">${t.installed ? "✓" : "–"} ${name}</span>`
  ).join("");
  body.innerHTML = `
    <div class="setup-head-row">
      <h2>각 CLI에 로그인하세요</h2>
      <button type="button" class="btn-ghost" id="recheck">재확인</button>
    </div>
    <div class="setup-callout">Agentic OS가 대신 로그인할 수는 없습니다.
    각 CLI의 OAuth 로그인을 <b>터미널에서</b> 마친 뒤 계속 진행하세요.
    (로그인 없이 진행해도 되지만 해당 에이전트 작업은 실패합니다)</div>
    <div class="auth-list">${rows}</div>
    <div class="setup-optional">
      <h3>보조 도구 (선택)</h3>
      <div class="tool-chips">${tools}</div>
      <p class="pc-desc">노트 저장 위치: <code>${SETUP.memoryDir}</code>
      ${SETUP.vaultSet ? "" : " — <code>aos.env</code>의 <code>AOS_VAULT_PATH</code>로 Obsidian 볼트를 지정할 수 있어요"}</p>
    </div>`;
  document.getElementById("recheck").addEventListener("click", recheck);
}

function renderDone() {
  const chips = ORDER.filter((p) => state.selected.has(p)).map((p) => {
    const st = state.status?.providers?.[p] || {};
    return `<span class="chip done-chip">${st.label || p}</span>`;
  }).join("");
  const overlap = (SETUP.councilMembers || []).filter((p) => state.selected.has(p));
  const councilNote = overlap.length >= SETUP.councilMin
    ? `<p class="done-ok">✓ 협의 모드 사용 가능 (${overlap.length}개 에이전트)</p>`
    : `<p class="done-warn">협의 모드는 에이전트 ${SETUP.councilMin}개 이상이 필요해 비활성화됩니다</p>`;
  body.innerHTML = `
    <div class="setup-hero">
      ${mark()}
      <h1>준비가 끝났습니다</h1>
      <p class="setup-sub">선택한 에이전트만 대시보드·사용량·자동 라우팅에 나타납니다.</p>
    </div>
    <div class="done-chips">${chips}</div>
    ${councilNote}`;
}

// ---- 전환·푸터 -----------------------------------------------------------

async function recheck(ev) {
  const btn = ev?.currentTarget;
  if (btn) { btn.disabled = true; btn.textContent = "확인 중…"; }
  try { await fetchStatus(); } finally {
    render();  // 버튼도 다시 그려진다
  }
}

function syncFooter() {
  backBtn.hidden = state.step === 1;
  skipBtn.hidden = state.step === 4;
  nextBtn.textContent = state.step === 1 ? "시작하기" : state.step === 4 ? "완료" : "다음";
  nextBtn.disabled = state.step >= 2 && state.selected.size === 0;
  document.querySelectorAll("#setup-steps .step").forEach((el) => {
    const n = Number(el.dataset.step);
    el.classList.toggle("active", n === state.step);
    el.classList.toggle("done", n < state.step);
  });
}

function render() {
  showError("");
  if (state.step === 1) renderWelcome();
  else if (state.step === 2) renderProviders();
  else if (state.step === 3) renderAuth();
  else renderDone();
  body.classList.remove("step-in");
  void body.offsetWidth;             // 재생을 위한 리플로우
  body.classList.add("step-in");
  syncFooter();
}

async function complete() {
  nextBtn.disabled = true;
  try {
    const params = new URLSearchParams();
    state.selected.forEach((p) => params.append("providers", p));
    const r = await fetch("/api/setup/complete", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params,
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      showError(data.detail || "저장에 실패했습니다");
      nextBtn.disabled = false;
      return;
    }
    window.location = "/";
  } catch {
    showError("서버에 연결할 수 없습니다");
    nextBtn.disabled = false;
  }
}

nextBtn.addEventListener("click", async () => {
  if (state.step === 4) return complete();
  state.step += 1;
  if (state.step === 2 && !state.status) await fetchStatus();
  render();
});
backBtn.addEventListener("click", () => { state.step -= 1; render(); });
skipBtn.addEventListener("click", async () => {
  // 건너뛰기 = 현재(또는 기본) 선택 그대로 저장하고 대시보드로
  if (!state.status) await fetchStatus();
  if (state.selected.size === 0) ORDER.forEach((p) => state.selected.add(p));
  complete();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !nextBtn.disabled) nextBtn.click();
});

(async () => {
  if (state.step >= 2) await fetchStatus();
  render();
})();
