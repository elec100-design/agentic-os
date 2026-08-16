"use strict";

// ---- 좌측 사이드바 목록 접기/펼치기 ----------------------------------------
// 에이전트가 늘어나면 채널이 화면 밖으로 밀려난다. 묶음별로 접을 수 있게 하고,
// 접힌 상태는 localStorage 에 남긴다 — 페이지를 옮길 때마다 다시 접게 하면
// 접는 의미가 없다(홈·채널·DM 이 모두 같은 목록을 쓴다).
(function () {
  const KEY = "aos-rail-collapsed";

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};   // 손상됐거나 접근 불가(사생활 보호 모드) — 전부 펼침으로 본다
    }
  }

  function save(state) {
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch (e) {
      // 저장 못 해도 이번 화면의 접기는 그대로 동작해야 한다
    }
  }

  const state = load();

  document.querySelectorAll(".rail-group[data-rail-group]").forEach((group) => {
    const key = group.dataset.railGroup;
    const btn = group.querySelector(".rail-collapse");
    const body = group.querySelector(".rail-group-body");
    if (!btn || !body) return;

    function apply(collapsed) {
      group.classList.toggle("collapsed", collapsed);
      body.hidden = collapsed;
      btn.setAttribute("aria-expanded", String(!collapsed));
    }

    apply(!!state[key]);

    btn.addEventListener("click", () => {
      const collapsed = !group.classList.contains("collapsed");
      apply(collapsed);
      state[key] = collapsed;
      save(state);
    });
  });

  // ---- 목록에서 고른 대화를 중앙 탭으로 열기 -------------------------------
  // 홈에서는 페이지를 옮기지 않고 중앙 워크스페이스에 탭으로 띄운다 — 작업 탭과
  // 나란히 두고 오갈 수 있어야 하기 때문이다. 홈이 아닌 화면(전용 채널 페이지)
  // 에서는 window.openHomeTab 이 없으므로 링크가 평소대로 동작한다.
  document.addEventListener("click", async (e) => {
    const item = e.target.closest(".rail-item");
    if (!item || !window.openHomeTab) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;  // 새 탭 열기는 그대로
    e.preventDefault();

    let channelId = item.dataset.channelId;
    if (!channelId) {
      // 아직 한 번도 말을 걸지 않은 에이전트는 DM 채널이 없다 — 여기서 만든다.
      const slug = item.dataset.dmSlug;
      if (!slug) return;
      try {
        const res = await fetch(`/api/dm/${encodeURIComponent(slug)}`);
        if (!res.ok) { window.location.href = item.href; return; }
        channelId = (await res.json()).channel_id;
        item.dataset.channelId = channelId;   // 다음 클릭은 곧바로 연다
      } catch (err) {
        window.location.href = item.href;     // 네트워크가 죽으면 평소 이동으로
        return;
      }
    }
    window.openHomeTab({ kind: "channel", refId: +channelId,
                         title: item.dataset.railTitle || undefined });
    document.querySelectorAll(".rail-item.active").forEach((el) => el.classList.remove("active"));
    item.classList.add("active");
  });
})();
