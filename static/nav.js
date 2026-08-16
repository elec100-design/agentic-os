"use strict";

// ---- 좁은 화면의 좌측 서랍 토글 --------------------------------------------
// 홈은 app.js 가 같은 일을 한다. 대화 페이지(채널·DM·채널 만들기)는 app.js 를
// 통째로 싣지 않으므로 이 조각만 따로 쓴다.
(function () {
  const toggle = document.getElementById("nav-toggle");
  if (!toggle) return;

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    document.body.classList.toggle("nav-open");
  });
  document.addEventListener("click", (e) => {
    if (!document.body.classList.contains("nav-open")) return;
    if (e.target.closest(".sidebar") || e.target.closest("#nav-toggle")) return;
    document.body.classList.remove("nav-open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.body.classList.remove("nav-open");
  });
})();
