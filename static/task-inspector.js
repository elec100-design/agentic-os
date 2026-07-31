"use strict";

// 태스크 인스펙터 — 워크플로우 노드를 클릭했을 때 여는 편집 폼 + 태스크 전용 채팅.
//
// 두 곳에서 같은 모듈을 쓴다:
//   · 프로젝트 상세 페이지: 좌측 chat-rail 안(chat-rail.js가 마운트)
//   · 홈 대시보드: 좌측 사이드바(home.js가 마운트) — 중앙은 워크플로우로 남는다
// 호스트마다 "열릴 때/닫힐 때 주변을 어떻게 바꿀지"가 다르므로 그 부분만 콜백으로
// 받고, 폼·채팅·시도 이력 렌더는 전부 여기서 한다.
(function () {
  function tt(s) { return (typeof t === "function") ? t(s) : s; }
  function escapeHtml(s) {
    return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }
  function paintMarkdown(el, body) {
    if (typeof renderMarkdown === "function") renderMarkdown(el, body);
    else el.textContent = body;
  }
  function taskEscape(value) { return escapeHtml(String(value || "")); }

  function optionsHtml(values, selected, labels) {
    return values.map((v) => `<option value="${taskEscape(v)}"${v === selected ? " selected" : ""}>${taskEscape((labels && labels[v]) || v)}</option>`).join("");
  }

  // 좁은 화면에서는 '고급'(에이전트·모델·추가 지시)을 접어 둔다 — 작업 내용과
  // 태스크 채팅이 한 화면에 먼저 들어와야 한다.
  function wideScreen() {
    return window.matchMedia("(min-width: 768px)").matches;
  }

  function taskArtifact(task) {
    if (!task.artifact_path) return "";
    const name = task.artifact_path.split("/").pop();
    return `<a class="orca-task-artifact-link" target="_blank" href="/artifacts/${task.project_id}/${encodeURIComponent(name)}">${taskEscape(name)} ↗</a>`;
  }

  // 시도 이력 — 중단(interrupted)된 시도의 부분 출력까지 남아 "어디까지 하다
  // 멈췄는지"를 확인한 뒤 이어서 실행할 수 있다.
  function runsHtml(runs) {
    if (!runs || !runs.length) return "";
    const rows = runs.slice().reverse().map((r) => `
      <details class="orca-task-run">
        <summary>${taskEscape(tt("시도"))} ${r.attempt} · <span class="badge badge-${taskEscape(r.status)}">${taskEscape(r.status)}</span> ${taskEscape(r.agent || "")}</summary>
        ${r.error ? `<p class="orca-task-run-error">${taskEscape(r.error)}</p>` : ""}
        <pre class="orca-task-run-output">${taskEscape(r.output || "")}</pre>
      </details>`).join("");
    return `<section class="orca-task-runs"><h3>${taskEscape(tt("시도 이력"))}</h3>${rows}</section>`;
  }

  window.mountTaskInspector = function (container, opts) {
    const {
      onOpen = () => {}, onClose = () => {},
      onBoardChanged = () => {}, setTitle = () => {},
    } = opts || {};
    if (!container) return null;
    const context = container.querySelector(".orca-task-context") || container;
    const body = container.querySelector(".orca-task-context-body");
    if (!body) return null;

    let activeTaskId = null;
    let activeProjectId = null;

    function taskMessageEl(message) {
      const row = document.createElement("article");
      row.className = `orca-task-message is-${message.role}`;
      const who = document.createElement("span");
      who.className = "orca-task-message-who";
      who.textContent = message.role === "user" ? tt("나") : tt("에이전트");
      const content = document.createElement("div");
      content.className = "orca-task-message-content";
      paintMarkdown(content, message.content || message.reply || "");
      row.append(who, content);
      if (message.suggested_description) {
        const apply = document.createElement("button");
        apply.type = "button"; apply.className = "btn-ghost orca-task-suggestion";
        apply.textContent = tt("제안 설명 적용");
        apply.addEventListener("click", () => {
          const desc = body.querySelector("[name=description]");
          if (desc) { desc.value = message.suggested_description; desc.focus(); }
        });
        row.appendChild(apply);
      }
      return row;
    }

    // 대화 목록이 자체 스크롤을 가질 때(데스크톱)는 목록을, 작업창 하나가 통째로
    // 스크롤될 때(모바일)는 작업창을 바닥으로 보낸다.
    // outer=true는 작업창 전체를 바닥까지 내린다 — 방금 메시지를 보낸 경우에만
    // 쓴다. 태스크를 처음 열 때까지 그러면 제목·작업 내용이 화면 밖으로 밀린다.
    function scrollChatToEnd(list, outer) {
      if (list.scrollHeight > list.clientHeight + 2) list.scrollTop = list.scrollHeight;
      else if (outer && context) context.scrollTop = context.scrollHeight;
    }

    function close() {
      activeTaskId = null;
      activeProjectId = null;
      body.textContent = "";
      onClose();
    }

    async function show(id, meta) {
      activeTaskId = +id;
      if (meta && meta.projectId != null) activeProjectId = meta.projectId;
      onOpen();
      body.innerHTML = `<p class="orca-task-loading">${taskEscape(tt("태스크를 불러오는 중…"))}</p>`;
      try {
        const [taskRes, messagesRes, runsRes] = await Promise.all([
          fetch(`/api/tasks/${id}`), fetch(`/api/tasks/${id}/messages`),
          fetch(`/api/tasks/${id}/runs`),
        ]);
        if (!taskRes.ok) throw new Error("task load failed");
        const task = await taskRes.json();
        const messages = messagesRes.ok ? await messagesRes.json() : [];
        const runs = runsRes.ok ? await runsRes.json() : [];
        if (activeTaskId !== +id) return;
        activeProjectId = task.project_id;
        setTitle(tt("태스크"));
        render(task, messages, runs);
      } catch (err) {
        if (activeTaskId === +id) body.innerHTML = `<p class="orca-task-error">${taskEscape(tt("태스크를 불러오지 못했습니다."))}</p>`;
      }
    }

    function boardChanged() { onBoardChanged(activeProjectId); }

    function render(task, messages, runs) {
      const disabled = task.editable ? "" : "disabled";
      const agent = task.provider || "auto";
      // 현재 담당이 활성 목록에 없으면(비활성화된 에이전트·media) 그 값도 선택지로
      // 남긴다 — 안 그러면 저장할 때 첫 항목(auto)으로 조용히 바뀐다.
      const agentOptions = [...new Set(["auto", ...(task.agents || []), agent])];
      const modelList = (task.provider_models || {})[task.provider] || [];
      const deps = task.depends_on || [];
      const depsHtml = (task.siblings || []).map((s) => `
      <label class="orca-task-dep"><input type="checkbox" name="depends_on" value="${s.seq}"${deps.includes(s.seq) ? " checked" : ""} ${disabled}><span>#${s.seq} ${taskEscape(s.title)}</span></label>`).join("");
      body.innerHTML = `
      <form class="orca-task-edit-form" data-task-id="${task.id}">
        <div class="orca-task-context-top"><span class="badge badge-${taskEscape(task.status)}">${taskEscape(task.status)}</span><span class="provider-name">${taskEscape(agent)}</span></div>
        <label><span>${taskEscape(tt("제목"))}</span><input name="title" required value="${taskEscape(task.title)}" ${disabled}></label>
        <label><span>${taskEscape(tt("작업 내용"))}</span><textarea name="description" rows="5" required ${disabled}>${taskEscape(task.description)}</textarea></label>
        <label><span>${taskEscape(tt("종류"))}</span><select name="task_type" ${disabled}>${optionsHtml(task.task_types || [], task.task_type)}</select></label>
        <details class="orca-task-advanced"${wideScreen() ? " open" : ""}>
          <summary>${taskEscape(tt("고급"))}</summary>
          <label><span>${taskEscape(tt("담당 에이전트"))}</span><select name="agent" ${disabled}>${optionsHtml(agentOptions, agent)}</select></label>
          <label><span>${taskEscape(tt("모델"))}</span><select name="model" ${disabled}><option value="">${taskEscape(tt("기본"))}</option>${optionsHtml(modelList, task.model || "")}</select></label>
          <label><span>${taskEscape(tt("추가 지시"))}</span><textarea name="extra_instruction" rows="2" ${disabled}>${taskEscape(task.extra_instruction || "")}</textarea></label>
        </details>
        ${depsHtml ? `<fieldset class="orca-task-deps"><legend>${taskEscape(tt("선행 태스크"))}</legend>${depsHtml}</fieldset>` : ""}
        ${task.artifact_path ? `<div class="orca-task-meta">${taskArtifact(task)}</div>` : ""}
        ${task.error ? `<p class="orca-task-error">${taskEscape(task.error)}</p>` : ""}
        <div class="orca-task-actions">
          ${task.editable ? `<button class="btn-primary" type="submit">${taskEscape(tt("저장"))}</button>` : `<p class="orca-task-locked">${taskEscape(tt("실행 중에는 편집할 수 없습니다. 일시정지 후 수정하세요."))}</p>`}
          ${task.retryable ? `<button class="btn-ghost" type="button" data-task-retry>${taskEscape(tt("다시 실행"))}</button>` : ""}
        </div>
      </form>
      ${runsHtml(runs)}
      <section class="orca-task-chat"><h3>${taskEscape(tt("태스크 채팅"))}</h3><div class="orca-task-messages"></div>
        <form class="orca-task-chat-form"><textarea rows="3" required placeholder="${taskEscape(tt("이 태스크에 대한 수정 또는 커멘트를 입력하세요…"))}"></textarea><button class="orca-rail-send" type="submit" aria-label="${taskEscape(tt("전송"))}">↑</button></form>
      </section>`;
      const list = body.querySelector(".orca-task-messages");
      messages.forEach((m) => list.appendChild(taskMessageEl(m)));
      scrollChatToEnd(list);
      const editForm = body.querySelector(".orca-task-edit-form");
      editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const data = new FormData(editForm);
        const save = editForm.querySelector("button[type=submit]"); save.disabled = true;
        const payload = {
          title: data.get("title"), description: data.get("description"),
          task_type: data.get("task_type"), agent: data.get("agent"),
          model: data.get("model") || "",
          extra_instruction: data.get("extra_instruction") || "",
        };
        // 선행 태스크는 체크박스 집합 전체를 교체값으로 보낸다(엣지 추가·삭제 한 번에).
        if (editForm.querySelector(".orca-task-deps")) {
          payload.depends_on = data.getAll("depends_on").map(Number);
        }
        try {
          const res = await fetch(`/api/tasks/${task.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          if (!res.ok) throw new Error((await res.json()).detail || "save failed");
          boardChanged();
          show(task.id);
        } catch (err) { alert(err.message || tt("저장하지 못했습니다.")); save.disabled = false; }
      });
      // 에이전트를 바꾸면 이전 프로바이더의 모델 목록은 무효다 — 그 자리에서 갈아끼운다.
      const agentSel = editForm.querySelector("[name=agent]");
      const modelSel = editForm.querySelector("[name=model]");
      agentSel?.addEventListener("change", () => {
        const models = (task.provider_models || {})[agentSel.value] || [];
        modelSel.innerHTML = `<option value="">${taskEscape(tt("기본"))}</option>${optionsHtml(models, "")}`;
      });
      const retryBtn = body.querySelector("[data-task-retry]");
      retryBtn?.addEventListener("click", async () => {
        retryBtn.disabled = true;
        try {
          const res = await fetch(`/api/tasks/${task.id}/retry`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
          if (!res.ok) throw new Error((await res.json()).detail || "retry failed");
          boardChanged();
          show(task.id);
        } catch (err) { alert(err.message || tt("다시 실행하지 못했습니다.")); retryBtn.disabled = false; }
      });
      const chatForm = body.querySelector(".orca-task-chat-form");
      chatForm.addEventListener("submit", async (e) => {
        e.preventDefault(); const ta = chatForm.querySelector("textarea"); const content = ta.value.trim(); if (!content) return;
        const submit = chatForm.querySelector("button"); submit.disabled = true;
        list.appendChild(taskMessageEl({ role: "user", content })); ta.value = ""; scrollChatToEnd(list, true);
        try {
          const res = await fetch(`/api/tasks/${task.id}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) });
          if (!res.ok) throw new Error((await res.json()).detail || "chat failed");
          const reply = await res.json(); list.appendChild(taskMessageEl({ role: "assistant", content: reply.reply, suggested_description: reply.suggested_description })); scrollChatToEnd(list, true);
        } catch (err) { list.appendChild(taskMessageEl({ role: "assistant", content: err.message || tt("응답을 받지 못했습니다.") })); }
        finally { submit.disabled = false; }
      });
    }

    return {
      show, close,
      get activeTaskId() { return activeTaskId; },
    };
  };
})();
