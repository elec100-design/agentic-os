import uuid
from datetime import datetime, timedelta, timezone

import json

from app.providers import (
    PROVIDERS,
    AntigravityProvider,
    ClaudeProvider,
    CodexProvider,
    GeminiProvider,
    GrokProvider,
    HermesProvider,
    OpenClawProvider,
    route_auto,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


# --- Claude ---

def test_claude_build_new():
    # --allowedTools is variadic; prompt must follow `--` so it is not eaten as a tool
    cmd = ClaudeProvider().build_command("안녕")
    assert cmd == [
        # stream-json: 도구 호출·결과가 한 줄에 하나씩 흘러 타임라인을 만든다
        # (--print 와 함께 쓰려면 --verbose 가 필요하다)
        "claude", "-p", "--output-format", "stream-json", "--verbose",
        # 이게 없으면 Write/Edit/Bash 가 auto-deny 되고도 exit 0 으로 끝난다
        "--permission-mode", "acceptEdits",
        # 사용자 전역 훅이 result를 덮어쓰지 않도록 훅 비활성
        "--settings", '{"disableAllHooks": true}',
        "--allowedTools", "WebSearch", "WebFetch", "Bash",
        "--", "안녕",
    ]


def test_claude_can_edit_files_like_other_agents():
    """claude 만 읽기 전용이면 route_auto 결과에 따라 작업이 조용히 실패한다.

    2026-08-01 실측: --permission-mode 없이 파일 생성을 시키면
    permission_denials=[Bash, Write] 로 아무것도 못 하고도 subtype="success",
    result="...needs your permission approval" 로 끝난다. codex 는
    --dangerously-bypass-approvals-and-sandbox 로 도는데 claude 만 막혀 있어
    같은 작업이 그날 잔량에 따라 성공/실패가 갈렸다.
    """
    cmd = ClaudeProvider().build_command("파일 고쳐줘")
    assert "--permission-mode" in cmd
    mode = cmd[cmd.index("--permission-mode") + 1]
    assert mode == "acceptEdits"
    # acceptEdits 는 Bash 를 자동 승인하지 않는다(실측: permission_denials=[Bash]).
    # 테스트 실행·빌드가 필요한 작업이 절반만 되므로 함께 사전 허용한다.
    tools = cmd[cmd.index("--allowedTools") + 1:cmd.index("--")]
    assert "Bash" in tools
    # 모든 도구를 여는 방식으로 넓히지는 않는다
    assert "bypassPermissions" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    # 권한 플래그가 `--` 앞에 있어야 프롬프트가 도구 이름으로 먹히지 않는다
    assert cmd.index("--permission-mode") < cmd.index("--")


def test_claude_keeps_permission_mode_on_resume():
    cmd = ClaudeProvider().build_command("이어서", session_id="s1", model="opus")
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--") + 1] == "이어서"


def test_claude_build_allows_web_tools_on_resume():
    cmd = ClaudeProvider().build_command("이어", session_id="s1")
    assert "--allowedTools" in cmd
    assert "WebSearch" in cmd and "WebFetch" in cmd
    # prompt comes after `--`, never as a bare trailing arg after tools
    assert cmd[cmd.index("--") + 1] == "이어"
    assert cmd.index("--allowedTools") < cmd.index("--")


def test_claude_build_resume():
    cmd = ClaudeProvider().build_command("이어서 해줘", session_id="abc-123")
    assert "--resume" in cmd and "abc-123" in cmd
    assert cmd[cmd.index("--") + 1] == "이어서 해줘"
    assert cmd.index("--allowedTools") < cmd.index("--")


def test_claude_parse_json_output():
    stdout = '{"result": "답변입니다", "session_id": "sess-9"}'
    r = ClaudeProvider().parse_output(stdout, "", 0)
    assert r.text == "답변입니다"
    assert r.session_id == "sess-9"


def test_claude_parse_non_json_falls_back():
    r = ClaudeProvider().parse_output("plain text", "", 0)
    assert r.text == "plain text"
    assert r.session_id is None


def test_claude_parse_non_dict_json_falls_back():
    r = ClaudeProvider().parse_output('["a"]', "", 0)
    assert r.text == '["a"]'
    assert r.session_id is None
    r2 = ClaudeProvider().parse_output("null", "", 0)
    assert r2.text == "null"
    assert r2.session_id is None


def test_claude_rate_limit_with_epoch():
    out = "Claude AI usage limit reached|1751719800"
    ra = ClaudeProvider().detect_rate_limit(out, 1, now=NOW)
    assert ra == datetime.fromtimestamp(1751719800, tz=timezone.utc)


def test_claude_rate_limit_without_epoch_uses_default():
    ra = ClaudeProvider().detect_rate_limit("usage limit reached", 1, now=NOW)
    assert ra == NOW + timedelta(minutes=60)


def test_claude_no_limit_on_success_exit():
    assert ClaudeProvider().detect_rate_limit("usage limit reached", 0, now=NOW) is None


def test_claude_no_limit_on_normal_error():
    assert ClaudeProvider().detect_rate_limit("some other error", 1, now=NOW) is None


# --- Antigravity (agy) ---

def test_antigravity_never_resumes():
    p = AntigravityProvider()
    # agy는 헤드리스 세션 재개가 불가능 → 이어가기 미지원. session_id를 줘도
    # 무시하고 항상 단발 실행(-c 안 붙음).
    assert p.build_command("hi") == ["agy", "-p", "hi"]
    assert p.build_command("hi", session_id="latest") == ["agy", "-p", "hi"]
    assert "-c" not in p.build_command("hi", session_id="whatever")


def test_antigravity_rate_limit():
    p = AntigravityProvider()
    assert p.detect_rate_limit("Error 429: RESOURCE_EXHAUSTED", 1, now=NOW) == NOW + timedelta(minutes=60)
    assert p.detect_rate_limit("fine", 1, now=NOW) is None


def test_antigravity_parse_leaves_no_session():
    # 세션 ID를 남기지 않아 이어가기 대상으로 표시되지 않는다.
    assert AntigravityProvider().parse_output("out", "", 0).session_id is None


# --- Grok ---

def test_grok_build_mints_session_id_for_new_conversation():
    p = GrokProvider()
    cmd = p.build_command("hi")
    # 새 대화 → 우리가 UUID를 발급해 --session-id로 지정하고, parse_output이
    # 그 UUID를 세션 ID로 돌려준다(다음 턴에 --resume 대상).
    assert cmd[0] == "grok"
    assert "--session-id" in cmd
    minted = cmd[cmd.index("--session-id") + 1]
    uuid.UUID(minted)  # 유효한 UUID여야 함
    assert cmd[-2:] == ["-p", "hi"]
    assert p.parse_output("out", "", 0).session_id == minted


def test_grok_resume_targets_exact_session_id():
    p = GrokProvider()
    resumed = p.build_command("hi", session_id="abc-123")
    # 재개 → 저장해둔 그 ID로 정확히 재개(--resume), 새 UUID 발급 안 함.
    assert "--resume" in resumed
    assert resumed[resumed.index("--resume") + 1] == "abc-123"
    assert "--session-id" not in resumed
    assert p.parse_output("out", "", 0).session_id == "abc-123"


def _minted_id(cmd):
    return cmd[cmd.index("--session-id") + 1]


def test_grok_new_session_ids_are_unique():
    p = GrokProvider()
    # 새 대화마다 서로 다른 UUID가 발급돼야 한다(세션 혼선 방지).
    assert _minted_id(p.build_command("x")) != _minted_id(p.build_command("y"))


def test_grok_rate_limit():
    p = GrokProvider()
    assert p.detect_rate_limit("Too Many Requests", 1, now=NOW) is not None


# --- Codex ---

def test_codex_build_new():
    cmd = CodexProvider().build_command("hi")
    assert cmd[:2] == ["codex", "exec"]
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert cmd[-1] == "hi"
    assert "resume" not in cmd


def test_codex_build_resume():
    cmd = CodexProvider().build_command("continue", session_id="thread-abc")
    assert cmd[:4] == ["codex", "exec", "resume", "thread-abc"]
    assert cmd[-1] == "continue"


def test_codex_parse_jsonl():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"tid-1"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"첫 답"}}',
        '{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"최종 답"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}',
    ])
    r = CodexProvider().parse_output(stdout, "", 0)
    assert r.text == "최종 답"
    assert r.session_id == "tid-1"


def test_codex_rate_limit():
    p = CodexProvider()
    assert p.detect_rate_limit("rate limit exceeded", 1, now=NOW) is not None
    assert p.detect_rate_limit("ok", 0, now=NOW) is None


# --- Gemini ---

def test_gemini_build_ignores_session():
    p = GeminiProvider()
    assert p.build_command("hi") == [
        "gemini", "-p", "hi", "-y", "-o", "json", "--skip-trust",
    ]
    assert p.build_command("hi", session_id="sess") == [
        "gemini", "-p", "hi", "-y", "-o", "json", "--skip-trust",
    ]


def test_gemini_parse_json():
    r = GeminiProvider().parse_output(
        '{"response": "안녕", "session_id": "g-1"}', "", 0
    )
    assert r.text == "안녕"
    assert r.session_id == "g-1"


def test_gemini_rate_limit():
    p = GeminiProvider()
    assert p.detect_rate_limit("Error 429 RESOURCE_EXHAUSTED", 1, now=NOW) is not None


# --- OpenClaw ---

def test_openclaw_build_mints_session_id():
    p = OpenClawProvider()
    cmd = p.build_command("hi")
    assert cmd[0:2] == ["openclaw", "agent"]
    assert "--session-id" in cmd
    sid = cmd[cmd.index("--session-id") + 1]
    uuid.UUID(sid)
    assert cmd[cmd.index("--message") + 1] == "hi"
    assert "--json" in cmd
    assert p.parse_output("plain", "", 0).session_id == sid


def test_openclaw_resume_uses_given_session():
    p = OpenClawProvider()
    cmd = p.build_command("next", session_id="sess-9")
    assert cmd[cmd.index("--session-id") + 1] == "sess-9"


def test_openclaw_parse_gateway_json():
    stdout = json.dumps({
        "status": "ok",
        "result": {
            "sessionId": "s-from-result",
            "payloads": [{"text": "hello claw"}],
        },
    })
    r = OpenClawProvider().parse_output(stdout, "", 0)
    assert r.text == "hello claw"
    assert r.session_id == "s-from-result"


def test_openclaw_rate_limit():
    p = OpenClawProvider()
    assert p.detect_rate_limit("quota exceeded", 1, now=NOW) is not None


# --- Hermes ---

def test_hermes_build_and_never_limits():
    p = HermesProvider()
    assert p.build_command("hi") == ["hermes", "-z", "hi"]
    assert p.detect_rate_limit("rate limit", 1, now=NOW) is None


# --- Model selection ---

def test_claude_build_with_model():
    # 패밀리 별칭 — CLI가 항상 최신 full ID로 해석
    cmd = ClaudeProvider().build_command("안녕", model="opus")
    assert "--model" in cmd and "opus" in cmd
    assert cmd[cmd.index("--") + 1] == "안녕"
    assert cmd.index("--allowedTools") < cmd.index("--")


def test_claude_build_model_and_resume():
    cmd = ClaudeProvider().build_command("이어", session_id="s1", model="sonnet")
    assert "--model" in cmd and "sonnet" in cmd
    assert "--resume" in cmd and "s1" in cmd
    assert cmd[cmd.index("--") + 1] == "이어"
    assert cmd.index("--allowedTools") < cmd.index("--")


def test_antigravity_build_with_model():
    mid = "Gemini 3.5 Flash (Medium)"
    cmd = AntigravityProvider().build_command("hi", model=mid)
    assert cmd == ["agy", "-p", "hi", "--model", mid]


def test_grok_build_with_model():
    cmd = GrokProvider().build_command("hi", model="grok-4.5")
    # 새 대화라 --session-id <uuid>가 앞에 붙는다. 모델·프롬프트 배선만 검증.
    assert cmd[0] == "grok"
    assert cmd[cmd.index("--model") + 1] == "grok-4.5"
    assert cmd[-2:] == ["-p", "hi"]


def test_codex_build_with_model():
    cmd = CodexProvider().build_command("hi", model="gpt-5.4")
    assert cmd[cmd.index("--model") + 1] == "gpt-5.4"


def test_gemini_build_with_model():
    cmd = GeminiProvider().build_command("hi", model="gemini-2.5-pro")
    assert cmd == [
        "gemini", "-p", "hi", "-y", "-o", "json", "--skip-trust",
        "-m", "gemini-2.5-pro",
    ]


def test_openclaw_build_with_model():
    cmd = OpenClawProvider().build_command("hi", model="openai/gpt-5.5")
    assert cmd[cmd.index("--model") + 1] == "openai/gpt-5.5"


def test_no_model_omits_flag():
    assert "--model" not in ClaudeProvider().build_command("x")
    assert "--model" not in AntigravityProvider().build_command("x")
    assert "--model" not in GrokProvider().build_command("x")
    assert "--model" not in CodexProvider().build_command("x")
    assert "-m" not in GeminiProvider().build_command("x")
    assert "--model" not in OpenClawProvider().build_command("x")


# --- Registry & routing ---

def test_registry_has_all_providers():
    assert set(PROVIDERS) == {
        "claude", "codex", "antigravity", "gemini", "grok", "openclaw", "hermes",
    }
    # 정식 UI 순서
    assert list(PROVIDERS) == [
        "claude", "codex", "antigravity", "gemini", "grok", "openclaw", "hermes",
    ]


def _remaining(**kw):
    """provider -> remaining% 로 usage_state dict 생성."""
    return {p: {"remaining": v, "available": v is None or v > 0}
            for p, v in kw.items()}


def test_route_auto_simple_goes_hermes():
    assert route_auto("안녕하세요")[0] == "hermes"
    assert route_auto("오늘 날짜 알려줘")[0] == "hermes"


def test_route_auto_complex_picks_most_remaining():
    st = _remaining(claude=15, antigravity=70, grok=40)
    prov, reason = route_auto("이 코드 리팩터링 구현해줘", usage_state=st)
    assert prov == "antigravity"
    assert "antigravity" in reason


def test_route_auto_complex_skips_exhausted():
    # 미언급 클라우드(codex/gemini/openclaw)는 unknown=50으로 끼어들 수 있어
    # 비교 대상만 명시적으로 활성 목록에 넣는다.
    st = {"claude": {"remaining": 0, "available": False},
          "antigravity": {"remaining": 30, "available": True},
          "grok": {"remaining": 50, "available": True}}
    assert route_auto(
        "버그 수정해줘", usage_state=st,
        enabled=["claude", "antigravity", "grok", "hermes"],
    )[0] == "grok"


def test_route_auto_all_exhausted_falls_back_hermes():
    st = {p: {"remaining": 0, "available": False}
          for p in ("claude", "codex", "antigravity", "gemini", "grok", "openclaw")}
    assert route_auto("버그 수정 구현해줘", usage_state=st)[0] == "hermes"


def test_route_auto_unknown_usage_uses_priority():
    # 사용량 정보가 없으면(None) 우선순위 첫 번째 claude
    assert route_auto("버그 수정 구현해줘")[0] == "claude"


def test_route_auto_long_prompt_is_complex():
    assert route_auto("가" * 200)[0] != "hermes"


def test_route_auto_known_high_beats_unknown():
    # grok만 잔여를 알고 90% 남음 → unknown(=50)인 claude/antigravity보다 우선
    st = {"grok": {"remaining": 90, "available": True}}
    assert route_auto("코드 구현해줘", usage_state=st)[0] == "grok"


def test_rank_cloud_filters_by_enabled():
    from app.providers import rank_cloud
    ranked = rank_cloud({}, enabled=["grok", "hermes"])
    assert [n for n, _ in ranked] == ["grok"]


def test_auto_ranking_considers_every_enabled_cloud_agent():
    """새 provider도 난이도 충족 시 사용량 비교 후보가 된다."""
    from app.providers import rank_auto_agents
    usage = _remaining(claude=10, codex=20, antigravity=30, gemini=40,
                       grok=50, openclaw=60)
    ranked = rank_auto_agents("복잡한 분석 보고서 작성", usage,
                              enabled=["claude", "codex", "antigravity", "gemini",
                                       "grok", "openclaw", "hermes"])
    # hermes 는 사용량이 측정되지 않아(remaining=None) 실측값이 있는 에이전트
    # 뒤로 밀리지만, 후보에서 빠지지는 않는다
    # (test_auto_ranking_prefers_measured_usage_over_unknown_gateway_usage 참고).
    assert [name for name, _ in ranked] == [
        "openclaw", "grok", "gemini", "antigravity",
        "codex", "claude", "hermes",
    ]


def test_auto_ranking_prefers_measured_usage_over_unknown_gateway_usage():
    from app.providers import rank_auto_agents
    ranked = rank_auto_agents(
        "코드 구현해줘",
        {"claude": {"remaining": 10, "available": True},
         "openclaw": {"remaining": None, "available": None}},
        enabled=["claude", "openclaw"],
    )
    assert [name for name, _ in ranked] == ["claude", "openclaw"]


def test_auto_ranking_uses_task_affinity_only_when_usage_tied():
    from app.providers import rank_auto_agents
    usage = _remaining(codex=60, grok=60)
    assert rank_auto_agents("최신 뉴스 조사해줘", usage,
                            enabled=["codex", "grok"])[0][0] == "grok"


def test_route_auto_simple_without_hermes_uses_cloud():
    from app.providers import route_auto
    p, reason = route_auto("안녕", enabled=["claude"])
    assert p == "claude"
    assert "Hermes" in reason      # 기본 언어(en): "Hermes disabled → claude"


def test_route_auto_complex_only_enabled():
    from app.providers import route_auto
    usage = {"claude": {"remaining": 90, "available": True},
             "grok": {"remaining": 10, "available": True}}
    p, _ = route_auto("이 코드 버그 수정 구현", usage_state=usage,
                      enabled=["grok", "hermes"])
    assert p == "grok"


def test_route_auto_all_enabled_exhausted_no_hermes():
    from app.providers import route_auto
    usage = {"claude": {"remaining": 0, "available": False}}
    p, reason = route_auto("이 코드 버그 수정 구현", usage_state=usage,
                           enabled=["claude"])
    assert p == "claude"
    assert "exhausted" in reason   # 기본 언어(en)


def test_route_auto_enabled_none_unchanged():
    from app.providers import route_auto
    assert route_auto("안녕")[0] == "hermes"


def test_claude_supports_mcp_config():
    assert ClaudeProvider.supports_mcp is True


def test_claude_adds_mcp_config_flag_before_allowedtools():
    cmd = ClaudeProvider().build_command("안녕", mcp_config_path="/tmp/mcp.json")
    assert "--mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == "/tmp/mcp.json"
    # 옵션 파싱이 `--` 에서 끝나므로, mcp-config 도 그 앞에 있어야 한다
    assert cmd.index("--mcp-config") < cmd.index("--")


def test_claude_omits_mcp_config_flag_when_none():
    cmd = ClaudeProvider().build_command("안녕")
    assert "--mcp-config" not in cmd


def test_other_providers_do_not_declare_mcp_support():
    for cls in (AntigravityProvider, GrokProvider, CodexProvider, GeminiProvider,
               OpenClawProvider, HermesProvider):
        assert getattr(cls, "supports_mcp", False) is False, cls


def test_other_providers_accept_and_ignore_mcp_config_path():
    """인터페이스는 모든 provider 가 같아야 worker 가 분기 없이 호출할 수 있다."""
    assert CodexProvider().build_command("hi", mcp_config_path="/tmp/x.json")[:2] == ["codex", "exec"]
    assert AntigravityProvider().build_command("hi", mcp_config_path="/tmp/x.json") == ["agy", "-p", "hi"]
