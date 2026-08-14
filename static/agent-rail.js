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
})();
