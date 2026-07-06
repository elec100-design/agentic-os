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
    cmd = ClaudeProvider().build_command("안녕")
    assert cmd == ["claude", "-p", "--output-format", "json", "안녕"]


def test_claude_build_resume():
    cmd = ClaudeProvider().build_command("이어서 해줘", session_id="abc-123")
    assert "--resume" in cmd and "abc-123" in cmd
    assert cmd[-1] == "이어서 해줘"


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

def test_antigravity_build_and_resume():
    p = AntigravityProvider()
    assert p.build_command("hi") == ["agy", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert resumed[:2] == ["agy", "-p"]
    assert "-c" in resumed


def test_antigravity_rate_limit():
    p = AntigravityProvider()
    assert p.detect_rate_limit("Error 429: RESOURCE_EXHAUSTED", 1, now=NOW) == NOW + timedelta(minutes=60)
    assert p.detect_rate_limit("fine", 1, now=NOW) is None


def test_antigravity_parse_records_session():
    assert AntigravityProvider().parse_output("out", "", 0).session_id == "latest"


# --- Grok ---

def test_grok_build_and_resume():
    p = GrokProvider()
    assert p.build_command("hi") == ["grok", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert resumed[:2] == ["grok", "-c"]
    assert "hi" in resumed


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
    cmd = ClaudeProvider().build_command("안녕", model="claude-opus-4-8")
    assert "--model" in cmd and "claude-opus-4-8" in cmd
    assert cmd[-1] == "안녕"


def test_claude_build_model_and_resume():
    cmd = ClaudeProvider().build_command("이어", session_id="s1", model="claude-sonnet-5")
    assert "--model" in cmd and "claude-sonnet-5" in cmd
    assert "--resume" in cmd and "s1" in cmd
    assert cmd[-1] == "이어"


def test_antigravity_build_with_model():
    cmd = AntigravityProvider().build_command("hi", model="gemini-3-pro")
    assert cmd == ["agy", "-p", "hi", "--model", "gemini-3-pro"]


def test_grok_build_with_model():
    cmd = GrokProvider().build_command("hi", model="grok-4")
    assert cmd == ["grok", "--model", "grok-4", "-p", "hi"]


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
