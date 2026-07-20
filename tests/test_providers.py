import uuid
from datetime import datetime, timedelta, timezone

from app.providers import (
    PROVIDERS,
    AntigravityProvider,
    ClaudeProvider,
    GrokProvider,
    HermesProvider,
    route_auto,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


# --- Claude ---

def test_claude_build_new():
    # --allowedTools is variadic; prompt must follow `--` so it is not eaten as a tool
    cmd = ClaudeProvider().build_command("안녕")
    assert cmd == [
        "claude", "-p", "--output-format", "json",
        "--allowedTools", "WebSearch", "WebFetch",
        "--", "안녕",
    ]


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


def test_no_model_omits_flag():
    assert "--model" not in ClaudeProvider().build_command("x")
    assert "--model" not in AntigravityProvider().build_command("x")
    assert "--model" not in GrokProvider().build_command("x")


# --- Registry & routing ---

def test_registry_has_all_four():
    assert set(PROVIDERS) == {"claude", "antigravity", "grok", "hermes"}


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
    st = {"claude": {"remaining": 0, "available": False},
          "antigravity": {"remaining": 30, "available": True},
          "grok": {"remaining": 50, "available": True}}
    assert route_auto("버그 수정해줘", usage_state=st)[0] == "grok"


def test_route_auto_all_exhausted_falls_back_hermes():
    st = {p: {"remaining": 0, "available": False}
          for p in ("claude", "antigravity", "grok")}
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
