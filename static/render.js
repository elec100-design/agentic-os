// 마크다운 렌더링 헬퍼 (job/note 출력 공통).
// marked로 파싱 → highlight.js로 코드 하이라이트 → 코드블록 복사 버튼.
// 전부 vendored(로컬), CDN·빌드 도구 없음.
"use strict";

if (window.marked) {
  marked.setOptions({ gfm: true, breaks: false });
}

// AI 출력에 원시 HTML이 섞일 수 있으므로 렌더 후 위험 요소를 제거한다.
// (localhost 단일 사용자라 위험은 낮지만, 웹에서 긁어온 내용이 <script>·onerror=
//  같은 걸 포함할 수 있어 최소 방어를 둔다.)
function _sanitize(root) {
  root.querySelectorAll("script, iframe, object, embed").forEach((n) => n.remove());
  root.querySelectorAll("*").forEach((el) => {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) el.removeAttribute(attr.name);
      if ((name === "href" || name === "src") &&
          /^\s*javascript:/i.test(attr.value)) {
        el.removeAttribute(attr.name);
      }
    }
  });
}

function _copyIcon() {
  return `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5.5" y="5.5" width="8" height="8" rx="1.3"/><path d="M10.5 5.5V3.2a1 1 0 0 0-1-1H3.2a1 1 0 0 0-1 1v6.3a1 1 0 0 0 1 1h2.3"/></svg>`;
}

function _decorateCodeBlocks(root) {
  root.querySelectorAll("pre > code").forEach((code) => {
    if (window.hljs) {
      try { hljs.highlightElement(code); } catch (_) { /* 언어 미지원 등 무시 */ }
    }
    const pre = code.parentElement;
    if (pre.querySelector(".code-copy")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy";
    const _t = window.t || ((s) => s);
    btn.innerHTML = _copyIcon() + "<span>" + _t("복사") + "</span>";
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        btn.classList.add("copied");
        btn.querySelector("span").textContent = _t("복사됨");
        setTimeout(() => {
          btn.classList.remove("copied");
          btn.querySelector("span").textContent = _t("복사");
        }, 1400);
      } catch (_) { /* clipboard 미허용 환경 무시 */ }
    });
    pre.appendChild(btn);
  });
}

// text(마크다운)를 el 안에 렌더한다. window.renderMarkdown로 노출.
function renderMarkdown(el, text) {
  if (!window.marked) {           // 라이브러리 로드 실패 시 평문 폴백
    el.textContent = text;
    return;
  }
  el.innerHTML = marked.parse(text || "");
  _sanitize(el);
  _decorateCodeBlocks(el);
}

window.renderMarkdown = renderMarkdown;
