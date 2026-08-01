"use strict";

// partials/job_view.html을 마운트한다 — 이전 대화 턴을 말풍선으로 렌더하고,
// .job-src의 원문을 마크다운으로 렌더하며, 아직 끝나지 않은 작업이면
// /jobs/{id}/stream(SSE)을 붙여 실시간으로 이어 그린다. 끝난 작업이면
// "이어서 작업" 컴포저를 그 자리에서 제출해 후속 작업으로 이어간다.
// 단독 페이지(job.html)와 홈 중앙 탭(home.js)이 같은 함수를 쓴다.
//
// onFinish: 작업이 done/failed로 끝났을 때 호출된다(페이지는 리로드, 홈 탭은 재조회).
window.mountJobView = function mountJobView(root, onFinish) {
  if (!root) return () => {};
  const out = root.querySelector(".job-out");
  const src = root.querySelector(".job-src");
  const jobId = root.dataset.jobId;
  const status = root.dataset.jobStatus;

  renderThread(root);
  wireFollowUp(root, jobId);
  wireGitRevert(root, jobId);

  // 스트리밍 중에는 원시 텍스트를 누적하고, 프레임마다 전체를 다시 렌더한다.
  let raw = src ? src.textContent : "";
  let pending = false;
  function paint() {
    pending = false;
    window.renderMarkdown?.(out, raw);
  }
  paint();

  if (!["queued", "running", "rate_limited"].includes(status)) return () => {};

  const es = new EventSource(`/jobs/${jobId}/stream`);
  es.onmessage = (e) => {
    raw += JSON.parse(e.data);
    if (!pending) { pending = true; requestAnimationFrame(paint); }
  };
  es.addEventListener("status", () => { es.close(); onFinish?.(); });
  return () => es.close();
};

// 이 작업 이전의 턴들(스레드 노트에서 읽어 서버가 JSON으로 실어 준다)을
// 이번 턴 말풍선 앞에 채운다. 형식이 예상과 다르면 조용히 건너뛴다.
function renderThread(root) {
  const holder = root.querySelector(".job-thread-src");
  const thread = root.querySelector(".job-thread");
  if (!holder || !thread) return;
  let turns = [];
  try { turns = JSON.parse(holder.textContent) || []; } catch (e) { return; }
  const agent = root.querySelector(".provider-name")?.textContent.trim() || "agent";
  const me = (typeof t === "function") ? t("나") : "나";
  const frag = document.createDocumentFragment();
  for (const turn of turns) {
    const row = document.createElement("div");
    row.className = "chat-turn " + (turn.role === "user" ? "user" : "assistant");
    const role = document.createElement("div");
    role.className = "chat-role";
    role.textContent = turn.role === "user" ? me : agent;
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble md-body";
    row.appendChild(role);
    row.appendChild(bubble);
    frag.appendChild(row);
    window.renderMarkdown?.(bubble, turn.content);
  }
  thread.insertBefore(frag, holder.nextSibling);
}

// git 체크포인트 되돌리기 — 승인 없이 워크스페이스에 직접 쓰는 잡이 많아 만든
// 안전판. 자동으로는 절대 실행되지 않고, 사용자가 버튼을 눌러야만 되돌아간다.
function wireGitRevert(root, jobId) {
  const btn = root.querySelector(".git-revert-btn");
  if (!btn) return;
  const errEl = root.querySelector(".git-diff-error");
  btn.addEventListener("click", async () => {
    const msg = btn.dataset.confirm || "되돌릴까요?";
    if (!confirm(msg)) return;
    btn.disabled = true;
    if (errEl) errEl.hidden = true;
    try {
      const res = await fetch(`/jobs/${jobId}/git-revert`, {
        method: "POST", headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      // 되돌리기 액션을 "되돌렸습니다" 표시로 바꾼다 — 전체 패널을 다시 그릴
      // 필요 없이 이 자리만 갱신한다.
      const actions = root.querySelector(".git-diff-actions");
      if (actions) {
        const p = document.createElement("p");
        p.className = "git-diff-reverted";
        p.textContent = "↩ " + ((typeof t === "function") ? t("되돌렸습니다") : "되돌렸습니다");
        actions.replaceWith(p);
      }
    } catch (e) {
      btn.disabled = false;
      if (errEl) {
        errEl.textContent = e.message;
        errEl.hidden = false;
      }
    }
  });
}

// "이어서 작업" 컴포저 — 페이지를 떠나지 않고 후속 작업을 만든다. 홈에서는
// 새 작업이 곧바로 탭으로 열리고(openJobTab), 단독 페이지에서는 이동한다.
// 홈 우측 레일은 이 컴포저만 따로 받아 붙이므로 전역으로도 노출한다.
window.wireJobFollowUp = function (root, jobId) { wireFollowUp(root, jobId); };

function wireFollowUp(root, jobId) {
  const form = root.querySelector(".job-followup");
  if (!form) return;
  const err = form.querySelector(".followup-error");
  const send = form.querySelector("button[type=submit]");
  const box = form.querySelector("textarea");

  box.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!box.value.trim()) return;
    send.disabled = true;
    if (err) err.hidden = true;
    try {
      const res = await fetch("/jobs", {
        method: "POST", body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.status);
      const { job_id: newId } = await res.json();
      box.value = "";
      if (window.openJobTab) window.openJobTab(newId, +jobId);
      else window.location.href = `/jobs/${newId}`;
    } catch (e2) {
      if (err) {
        err.textContent = ((typeof t === "function") ? t("보낼 수 없습니다.") : "보낼 수 없습니다.")
          + " " + e2.message;
        err.hidden = false;
      }
    } finally {
      send.disabled = false;
    }
  });
}
