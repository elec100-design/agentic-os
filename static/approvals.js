"use strict";

// ---- 사이드바 승인 대기 배지 ------------------------------------------------
// 승인이 밀리면 에이전트가 멈춰 서 있다는 뜻이라 눈에 띄어야 한다. 별도 SSE 를
// 여는 대신 가볍게 폴링한다 — 승인은 사람이 처리하는 일이라 초 단위 지연은
// 문제가 되지 않는다.
(function () {
  const badge = document.getElementById("approvals-badge");
  if (!badge) return;

  async function refresh() {
    try {
      const r = await fetch("/api/approvals");
      if (!r.ok) return;
      const { pending_count: count } = await r.json();
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = !count;
    } catch (e) {
      // 서버가 잠깐 안 떠 있어도 배지 때문에 콘솔이 시끄러우면 안 된다
    }
  }

  refresh();
  setInterval(refresh, 20000);
})();
