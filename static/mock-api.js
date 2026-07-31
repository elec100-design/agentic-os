"use strict";

// 백엔드에 닿지 않을 때(오프라인/데모) 에이전트 응답을 흉내 내는 목 레이어.
// chat-rail.js가 실제 API fetch에 실패했을 때만 이걸로 폴백한다 — 정상 동작 시
// 이 파일은 전혀 관여하지 않는다.
window.OrcaMock = (function () {
  const PREFIX = "orca-mock-msgs-";
  const REPLY = "지금은 서버에 연결할 수 없어 시뮬레이션 응답을 보여드리고 있어요. " +
    "네트워크가 복구되면 실제 에이전트가 이어받습니다.";

  function load(channelId) {
    try { return JSON.parse(localStorage.getItem(PREFIX + channelId) || "[]"); }
    catch (e) { return []; }
  }
  function save(channelId, list) {
    localStorage.setItem(PREFIX + channelId, JSON.stringify(list));
  }
  function nextId() {
    return "mock-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }
  function findAcrossChannels(id) {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(PREFIX)) continue;
      const channelId = key.slice(PREFIX.length);
      const list = load(channelId);
      const idx = list.findIndex((m) => m.id === id);
      if (idx !== -1) return { channelId, list, idx };
    }
    return null;
  }

  function getRoots(channelId) {
    return load(channelId).filter((m) => m.role === "user" && !m.parent_id);
  }

  function getMessage(id) {
    const found = findAcrossChannels(id);
    return found ? found.list[found.idx] : null;
  }

  function getThread(rootId) {
    const found = findAcrossChannels(rootId);
    if (!found) return [];
    return found.list.filter((m) => m.id === rootId || m.parent_id === rootId);
  }

  function updateMessage(id, patch) {
    const found = findAcrossChannels(id);
    if (!found) return null;
    found.list[found.idx] = { ...found.list[found.idx], ...patch };
    save(found.channelId, found.list);
    return found.list[found.idx];
  }

  async function sendMessage(channelId, body, provider, parentId) {
    const list = load(channelId);
    const now = new Date().toISOString();
    const userMsg = {
      id: nextId(), role: "user", body, status: "done",
      provider: provider || "auto", parent_id: parentId || null, created_at: now,
    };
    const agentMsg = {
      id: nextId(), role: "assistant", body: "", status: "running",
      provider: provider || "auto", parent_id: parentId || userMsg.id, created_at: now,
    };
    list.push(userMsg, agentMsg);
    save(channelId, list);
    return { user_message_id: userMsg.id, message_id: agentMsg.id, _mock: true };
  }

  // EventSource 흉내 — addEventListener/close만 지원하는 최소 구현.
  function fakeStream(messageId) {
    const listeners = {};
    const timers = [];
    const es = {
      addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      close() { timers.forEach(clearTimeout); },
    };
    const emit = (name, data) => (listeners[name] || []).forEach((fn) => fn({ data: JSON.stringify(data) }));
    timers.push(setTimeout(() => emit("step", {
      id: 1, kind: "thought", status: "running", title: "생각 중…", detail: "목 응답 준비 중 (오프라인 모드)",
    }), 250));
    timers.push(setTimeout(() => {
      emit("step", {
        id: 1, kind: "thought", status: "done", title: "생각 완료", detail: "목 응답 준비 중 (오프라인 모드)",
      });
      updateMessage(messageId, { body: REPLY, status: "done" });
      emit("status", "done");
    }, 800));
    timers.push(setTimeout(() => emit("done", {}), 900));
    return es;
  }

  return { getRoots, getMessage, getThread, sendMessage, fakeStream, REPLY };
})();
